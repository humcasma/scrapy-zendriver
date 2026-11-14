import asyncio
import fnmatch
import logging
import time
from typing import Type, TypeVar, List, Optional
from dataclasses import dataclass

from scrapy.core.downloader.handlers.http11 import HTTP11DownloadHandler
from scrapy.settings import Settings
from scrapy.crawler import Crawler
from scrapy.utils.reactor import verify_installed_reactor
from scrapy import signals, Spider
from scrapy.http import Request, Response
from scrapy.responsetypes import responsetypes

import zendriver as zd
from zendriver import Tab, Browser
from zendriver.core.cloudflare import cf_is_interactive_challenge_present, verify_cf

from scrapy_zendriver._utils import (
    _encode_body,
    _get_header_value,
    _maybe_await,
)

logging.getLogger("websockets.client").setLevel(logging.ERROR)
logging.getLogger("zd").setLevel(logging.ERROR)
logging.getLogger("zendriver").setLevel(logging.ERROR)
logging.getLogger("uc.connection").setLevel(logging.ERROR)
logger = logging.getLogger("scrapy.zendriver")

__all__ = ['ScrapyZendriverDownloadHandler']

ZendriverHandler = TypeVar("ZendriverHandler", bound="ScrapyZendriverDownloadHandler")

@dataclass
class Config:
    max_concurrent_pages: int
    headless: bool
    blocked_urls: List[str]
    download_timeout: float
    block_status_codes: List[int]
    block_markers: List[str]
    block_reloads: int
    block_wait: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "Config":
        return cls(
            max_concurrent_pages=settings.getint(
                "ZENDRIVER_MAX_CONCURRENT_PAGES",
                settings.getint("CONCURRENT_REQUESTS", 16)
            ),
            headless=settings.getbool("ZENDRIVER_HEADLESS", default=True),
            blocked_urls=settings.getlist("ZENDRIVER_BLOCKED_URLS", []),
            download_timeout=settings.getfloat("DOWNLOAD_TIMEOUT", 180.0),
            block_status_codes=[
                int(code) for code in settings.getlist("ZENDRIVER_BLOCK_STATUS_CODES", [403])
            ],
            block_markers=[
                marker.lower() for marker in settings.getlist("ZENDRIVER_BLOCK_MARKERS", [])
            ],
            block_reloads=settings.getint("ZENDRIVER_BLOCK_RELOADS", 1),
            block_wait=settings.getfloat("ZENDRIVER_BLOCK_WAIT", 2.0),
        )


class ScrapyZendriverDownloadHandler(HTTP11DownloadHandler):
    def __init__(self, crawler: Crawler):
        super().__init__(crawler)
        verify_installed_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")
        crawler.signals.connect(self._engine_started, signals.engine_started)
        
        self.stats = crawler.stats
        self.config = Config.from_settings(crawler.settings)
        
        # If the HTTP handler already created the state, the HTTPS handler will just reuse it.
        if not hasattr(crawler, 'zendriver_shared_state'):
            crawler.zendriver_shared_state = {
                'browser': None,
                'browser_lock': asyncio.Lock(),
                'page_creation_lock': asyncio.Lock(),
                'semaphore': asyncio.Semaphore(self.config.max_concurrent_pages),
                'idle_pages': [],
                'active_pages_count': 0,
                'total_pages_created': 0,
                'default_tab_claimed': False,
            }
        
        # Create a convenient reference to the shared dictionary
        self._shared = crawler.zendriver_shared_state

    @classmethod
    def from_crawler(cls: Type[ZendriverHandler], crawler: Crawler) -> ZendriverHandler:
        return cls(crawler)

    async def _engine_started(self) -> None:
        await self._launch()

    def _set_max_concurrent_page_count(self):
        current_max_count = self.stats.get_value("zendriver/page_count/max_concurrent", 0)
        if self._shared['active_pages_count'] > current_max_count:
            self.stats.set_value("zendriver/page_count/max_concurrent", self._shared['active_pages_count'])

    async def _launch(self) -> None:
        async with self._shared['browser_lock']:
            if self._shared['browser']:
                return
            
            logger.info("Starting browser (headless=%s)...", self.config.headless)
            self._shared['browser'] = await zd.start(headless=self.config.headless)
            logger.info("Browser started successfully")

    async def _get_new_page(self) -> Tab:
        # Use a lock to prevent concurrent spiders from spamming the browser's target creator
        async with self._shared['page_creation_lock']:
            if not self._shared['default_tab_claimed']:
                try:
                    page = await self._shared['browser'].get()
                    self._shared['default_tab_claimed'] = True 
                except Exception:
                    # If getting the default tab fails, try creating a new one
                    page = await self._shared['browser'].get(new_tab=True)
                    # If we successfully created a new tab, we effectively bypassed 
                    # the default tab, so mark it claimed to avoid retrying get() forever.
                    self._shared['default_tab_claimed'] = True
            else:
                page = await self._shared['browser'].get(new_tab=True)
                
            # Init empty context block for handlers
            page._scrapy_context = None

            # Create closures so handlers always reference the specific tab's dynamic context
            def create_req_handler(tab):
                def handler(event, *args):
                    self._increment_request_stats(event)
                    ctx = getattr(tab, '_scrapy_context', None)
                    if ctx:
                        if getattr(event, 'request', None):
                            if event.request.url.strip("/") == ctx['url']:
                                ctx['req_state']['headers'] = dict(event.request.headers)
                        self._log_request(event, ctx['spider'])
                return handler

            def create_res_handler(tab):
                def handler(event, *args):
                    self._increment_response_stats(event)
                    ctx = getattr(tab, '_scrapy_context', None)
                    if ctx:
                        if getattr(event, 'response', None):
                            if event.response.url.strip("/") == ctx['url']:
                                ctx['req_state']['status'] = event.response.status
                        self._log_response(event, ctx['spider'])
                return handler

            # Attach GLOBAL stats handlers exactly ONCE
            page.add_handler(zd.cdp.network.RequestWillBeSent, create_req_handler(page))
            page.add_handler(zd.cdp.network.ResponseReceived, create_res_handler(page))
            page.add_handler(zd.cdp.network.LoadingFailed, self._log_blocked_request)
            
            self._shared['total_pages_created'] += 1
            self.stats.inc_value("zendriver/page_count")
            
            return page
                
    def _is_page_closed(self, page: Tab) -> bool:
        """Defensively check if a CDP tab is closed or disconnected."""
        if not page:
            return True
        try:
            if hasattr(page, 'closed'):
                val = getattr(page, 'closed')
                return val() if callable(val) else bool(val)
            if hasattr(page, 'is_closed'):
                val = getattr(page, 'is_closed')
                return val() if callable(val) else bool(val)
            return False
        except Exception:
            return True

    async def _create_page(self, request: Request, spider: Spider) -> Tab:
        if not self._shared['browser']:
            raise RuntimeError("Browser not initialized. Call _launch first.")

        # Try to reuse idle page, discarding dead ones
        while self._shared['idle_pages']:
            page = self._shared['idle_pages'].pop()
            if not self._is_page_closed(page):
                logger.debug("Reusing idle page for %s", request.url)
                return page
            else:
                logger.debug("Discarded a closed/disconnected page from the pool.")

        logger.debug("Creating new page for %s", request.url)
        page = await self._get_new_page()
        logger.debug("New page created, total pages is %i", self._shared['total_pages_created'])
        return page

    async def close(self):
        logger.info("Closing download handler")
        await super().close()
        
        for page in self._shared['idle_pages']:
            try:
                if not page.closed:
                    await page.close()
            except Exception:
                pass
        self._shared['idle_pages'].clear()

        async with self._shared['browser_lock']:
            if self._shared['browser']:
                logger.info("Stopping browser")
                await self._shared['browser'].stop()
                self._shared['browser'] = None

    async def download_request(self, request: Request, **kwargs):
        if request.meta.get("zendriver"):
            return await self._download_request(request, kwargs.get('spider'))
        return await super().download_request(request, **kwargs)

    async def _download_request(self, request: Request, spider: Spider) -> Response:
        await self._shared['semaphore'].acquire()
        self._shared['active_pages_count'] += 1
        self._set_max_concurrent_page_count()
        
        page = None
        try:
            page = request.meta.get("zendriver_page")
            if not isinstance(page, Tab) or self._is_page_closed(page):
                page = await self._create_page(request, spider)
                
            return await self._download_request_with_page(request, page, spider)
            
        except Exception as ex:
            logger.warning("Error in request: %s exc_msg=%s", request, str(ex), exc_info=True)
            raise
            
        finally:
            # Safely handle returning the tab to the pool whether the request succeeded or crashed
            if page and not self._is_page_closed(page):
                if not request.meta.get("zendriver_include_page"):
                    self._shared['idle_pages'].append(page)
                    logger.info("RETURNED: Tab returned to pool. Pool size is %d", len(self._shared['idle_pages']))
                else:
                    logger.info("KEPT: Tab kept by Spider (zendriver_include_page=True).")
            elif page:
                logger.warning("DISCARDED: Tab crashed or was closed during request.")
            
            self._shared['active_pages_count'] -= 1

            self._shared['semaphore'].release()

    async def _download_request_with_page(self, request: Request, page: Tab, spider: Spider) -> Response:
        if request.meta.get("zendriver_include_page"):
            request.meta["zendriver_page"] = page

        start_time = time.time()
        
        req_state = {
            "headers": {},
            "status": 200
        }

        # Dynamically bind context so background event listeners can update req_state
        page._scrapy_context = {
            'url': request.url.strip("/"),
            'spider': spider,
            'request': request,
            'req_state': req_state,
        }

        try:
            if self.config.blocked_urls:
                await page.send(zd.cdp.network.enable())
                await page.send(zd.cdp.network.set_blocked_ur_ls(self.config.blocked_urls))

            await asyncio.wait_for(page.get(request.url), timeout=self.config.download_timeout)
            
            body_str = await page.get_content()
            
            # Cloudflare heuristic check
            # Only run the heavy DOM verification if we see CF indicators.
            # This ensures normal pages are bypassed instantly with 0 delay.
            is_cf_suspect = req_state["status"] in (403, 503) or "challenges.cloudflare.com" in body_str
            
            if is_cf_suspect:
                logger.debug("Cloudflare indicators found on %s, verifying DOM...", request.url)
                
                # Check if the interactive iframe is actually present
                if await cf_is_interactive_challenge_present(page, timeout=3.0):
                    logger.info("Cloudflare challenge detected! Attempting bypass...")
                    try:
                        # Attempt to click and solve the Turnstile checkbox
                        await verify_cf(page, timeout=20.0)
                        logger.info("Cloudflare checkbox bypassed! Waiting for page redirect...")
                        
                        # Wait for the JS redirect to trigger and the real page to settle
                        try:
                            await page.wait_for_ready_state(10)
                        except asyncio.TimeoutError:
                            logger.debug("Network idle timeout reached after CF bypass.")
                            
                        # Update the body content with the now-bypassed real page
                        body_str = await page.get_content()
                    except Exception as e:
                        logger.warning("Cloudflare bypass failed or timed out: %s", str(e))

            # Bot protections like Akamai often block the first visit, then let a reload
            # through once their script has set a valid cookie.
            body_str, blocked = await self._reload_on_block(page, request, req_state, body_str)

            # Apply user-defined page methods (e.g., clicks, scrolls)
            if request.meta.get("zendriver_page_methods"):
                await self._apply_page_methods(page, request, spider)
                # If user methods were executed, the DOM might have changed, so we grab it again
                body_str = await page.get_content()
                
        finally:
            page._scrapy_context = None

        request.meta["download_latency"] = time.time() - start_time

        body, encoding = _encode_body(headers=req_state["headers"], text=body_str)
        respcls = responsetypes.from_args(headers=req_state["headers"], url=request.url, body=body)

        return respcls(
            url=request.url,
            status=req_state["status"],
            headers=req_state["headers"],
            body=body,
            request=request,
            flags=["zendriver", "blocked"] if blocked else ["zendriver"],
            encoding=encoding
        )

    def _is_blocked(self, status: int, body_str: str) -> bool:
        """A block is a blocking status code whose body has one of the markers
        (or any blocking status code when no markers are configured)."""
        if status not in self.config.block_status_codes:
            return False
        if not self.config.block_markers:
            return True
        body_lower = body_str.lower()
        return any(marker in body_lower for marker in self.config.block_markers)

    async def _reload_on_block(self, page: Tab, request: Request, req_state: dict, body_str: str):
        """Reload a blocked page up to ZENDRIVER_BLOCK_RELOADS times.

        Returns the latest body and whether the page is still blocked.
        """
        if not self._is_blocked(req_state["status"], body_str):
            return body_str, False

        self.stats.inc_value("zendriver/blocked")
        logger.info("Blocked (%i) on %s", req_state["status"], request.url)

        for attempt in range(1, self.config.block_reloads + 1):
            # Give the protection script time to run and set its cookies.
            await asyncio.sleep(self.config.block_wait)
            self.stats.inc_value("zendriver/blocked/reload")
            try:
                # The response listener updates req_state["status"] for this URL.
                await asyncio.wait_for(page.get(request.url), timeout=self.config.download_timeout)
            except asyncio.TimeoutError:
                logger.warning("Reload %i timed out on %s", attempt, request.url)
                break
            body_str = await page.get_content()

            if not self._is_blocked(req_state["status"], body_str):
                self.stats.inc_value("zendriver/blocked/recovered")
                logger.info("Unblocked after %i reload(s) on %s", attempt, request.url)
                return body_str, False

        self.stats.inc_value("zendriver/blocked/gave_up")
        logger.warning("Still blocked (%i) after reloading %s", req_state["status"], request.url)
        return body_str, True

    async def _apply_page_methods(self, page: Tab, request: Request, spider: Spider) -> None:
        page_methods = request.meta.get("zendriver_page_methods") or ()
        for pm in page_methods:
            # Handle CallablePageMethod - call the callable with page as argument
            if hasattr(pm, 'callable_method'):
                try:
                    pm.result = await _maybe_await(pm.callable_method(page, *pm.args, **pm.kwargs))
                except Exception as ex:
                    logger.warning(
                        "Ignoring %r: callable raised exception",
                        pm,
                        extra={
                            "spider": spider,
                            "scrapy_request_url": request.url,
                            "scrapy_request_method": request.method,
                            "exception": ex,
                        },
                        exc_info=True,
                    )
                await page.wait()
                continue
            
            # Handle regular PageMethod - call method on page object
            try:
                method = getattr(page, pm.method)
            except AttributeError as ex:
                logger.warning(
                    "Ignoring %r: could not find method",
                    pm,
                    extra={
                        "spider": spider,
                        "scrapy_request_url": request.url,
                        "scrapy_request_method": request.method,
                        "exception": ex,
                    },
                    exc_info=True,
                )
            else:
                pm.result = await _maybe_await(method(*pm.args, **pm.kwargs))
                await page.wait()

    def _increment_request_stats(self, event, *args) -> None:
        if getattr(event, 'type_', None):
            stats_prefix = "zendriver/request_count"
            self.stats.inc_value(stats_prefix)
            self.stats.inc_value(f"{stats_prefix}/resource_type/{event.type_.value}")

    def _increment_response_stats(self, event, *args) -> None:
        if getattr(event, 'type_', None):
            stats_prefix = "zendriver/response_count"
            self.stats.inc_value(stats_prefix)
            self.stats.inc_value(f"{stats_prefix}/resource_type/{event.type_.value}")

    def _log_request(self, event, spider: Spider = None) -> None:
        if not getattr(event, 'request', None):
            return
            
        log_args = [event.request.method.upper(), event.request.url, event.type_.value]
        referrer = _get_header_value(event.request, "referer")
        
        if referrer:
            log_args.append(referrer)
            log_msg = "Request: <%s %s> (resource type: %s, referrer: %s)"
        else:
            log_msg = "Request: <%s %s> (resource type: %s)"
            
        logger.debug(log_msg, *log_args, extra={
            "spider": spider,
            "zendriver_request_url": event.request.url,
            "zendriver_request_method": event.request.method.upper(),
            "zendriver_resource_type": event.type_.value,
        })

    def _log_response(self, event, spider: Spider = None) -> None:
        if not getattr(event, 'response', None):
            return
            
        if not any(fnmatch.fnmatch(event.response.url, pattern) for pattern in self.config.blocked_urls):
            log_args = [event.response.status, event.response.url]
            location = _get_header_value(event.response, "location")
            
            log_msg = "Response: <%i %s> (location: %s)" if location else "Response: <%i %s>"
            if location: log_args.append(location)

            logger.debug(log_msg, *log_args, extra={
                "spider": spider,
                "zendriver_response_url": event.response.url,
                "zendriver_response_status": event.response.status,
            })
        else:
            logger.debug("Aborted Zendriver request <GET %s>", event.response.url)

    def _log_blocked_request(self, event, *args) -> None:
        if getattr(event, 'blocked_reason', None) and event.blocked_reason.value == "inspector":
            self.stats.inc_value("zendriver/request_count/aborted")