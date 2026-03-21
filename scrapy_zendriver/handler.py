import asyncio
import fnmatch
import logging
import time
from functools import partial
from typing import Type, TypeVar, List, Dict, Optional
from scrapy.core.downloader.handlers.http11 import HTTP11DownloadHandler
from dataclasses import dataclass
from scrapy.settings import Settings
from scrapy.crawler import Crawler
from scrapy.utils.reactor import verify_installed_reactor
from scrapy import signals, Spider
import zendriver as zd
from scrapy.http import Request, Response
from zendriver import Tab, Browser
from scrapy.responsetypes import responsetypes

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

# Module-level shared browser instance and lock
_browser: Optional[Browser] = None
_browser_lock: Optional[asyncio.Lock] = None



@dataclass
class Config:
    max_concurrent_pages: int
    headless: bool
    blocked_urls: List


    @classmethod
    def from_settings(cls, settings: Settings) -> "Config":
        cfg = cls(
            max_concurrent_pages=settings.getint("CONCURRENT_REQUESTS") if not settings.getint("ZENDRIVER_MAX_CONCURRENT_PAGES") else settings.getint("ZENDRIVER_MAX_CONCURRENT_PAGES"),
            headless=settings.getbool("ZENDRIVER_HEADLESS", default=True),
            blocked_urls=settings.getlist("ZENDRIVER_BLOCKED_URLS"),
        )
        return cfg



class ScrapyZendriverDownloadHandler(HTTP11DownloadHandler):
    def __init__(self, crawler: Crawler):
        super().__init__(crawler)
        verify_installed_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")
        crawler.signals.connect(self._engine_started, signals.engine_started)
        self.stats = crawler.stats
        self.config = Config.from_settings(crawler.settings)
        self.semaphore = asyncio.Semaphore(self.config.max_concurrent_pages)
        self._page_callbacks: Dict[str, callable] = {}
        self._idle_pages: List[Tab] = []  # Pool of idle pages for reuse
        
        # Initialize module-level lock if not already done
        global _browser_lock
        if _browser_lock is None:
            _browser_lock = asyncio.Lock()


    @classmethod
    def from_crawler(cls: Type[ZendriverHandler], crawler: Crawler) -> ZendriverHandler:
        return cls(crawler)


    async def _engine_started(self) -> None:
        """Launch the browser."""
        await self._launch()


    def _get_total_page_count(self):
        return len(self._page_callbacks)


    def _set_max_concurrent_page_count(self):
        count = self._get_total_page_count()
        current_max_count = self.stats.get_value("zendriver/page_count/max_concurrent")
        if current_max_count is None or count > current_max_count:
            self.stats.set_value("zendriver/page_count/max_concurrent", count)


    async def _launch(self) -> None:
        """Start the browser instance (module-level singleton)."""
        global _browser, _browser_lock
        
        async with _browser_lock:
            if _browser:
                logger.debug("Browser already running, skipping launch")
                return
            
            logger.info("Starting browser (headless=%s)...", self.config.headless)
            _browser = await zd.start(headless=self.config.headless)
            logger.info("Browser started successfully")

            # Add handler for target destruction to track page closures
            _browser.connection.add_handler(
                zd.cdp.target.TargetDestroyed,
                self._handle_target_destroyed
            )


    def _handle_target_destroyed(self, event: zd.cdp.target.TargetDestroyed) -> None:
        """Handle page/tab closure events."""
        target_id = event.target_id
        if target_id in self._page_callbacks:
            callback = self._page_callbacks.pop(target_id)
            if callback:
                callback()


    async def _get_new_page(self) -> Tab:
        """Create a new page/tab, with fallback for race conditions."""
        global _browser
        try:
            page = await _browser.get()
            logger.debug("Got existing tab from browser")
        except StopIteration:
            # No tabs available, create a new one
            logger.debug("No existing tabs, creating new tab")
            page = await _browser.get(new_tab=True)
        return page


    async def _create_page(self, request: Request, spider: Spider) -> Tab:
        await self.semaphore.acquire()

        if not _browser:
            raise RuntimeError("Browser not initialized. Call _launch first.")

        # First, try to get an idle page from the pool
        if self._idle_pages:
            page = self._idle_pages.pop()
            if not page.closed:
                logger.debug("Reusing idle page for %s (target_id=%s)", request.url, page.target_id)
                target_id = page.target_id
                # Re-register the callback for this reused page
                self._page_callbacks[target_id] = partial(self._close_page_callback, target_id)
            else:
                # Page was closed, create a new one
                logger.debug("Idle page was closed, creating new page for %s", request.url)
                page = await self._get_new_page()
                target_id = page.target_id
                self._page_callbacks[target_id] = partial(self._close_page_callback, target_id)
        else:
            logger.debug("Creating new page for %s (no idle pages available)", request.url)
            page = await self._get_new_page()
            target_id = page.target_id
            self._page_callbacks[target_id] = partial(self._close_page_callback, target_id)

        self.stats.inc_value("zendriver/page_count")
        logger.debug(
            "New page created, page count is %i",
            self._get_total_page_count(),
            extra={
                "spider": spider,
                "total_page_count": self._get_total_page_count(),
                "scrapy_request_url": request.url,
                "scrapy_request_method": request.method,
            },
        )
        self._set_max_concurrent_page_count()

        # Wrap handlers to ignore extra arguments from zendriver
        page.add_handler(zd.cdp.network.RequestWillBeSent, lambda e, *args: self._increment_request_stats(e))
        page.add_handler(zd.cdp.network.ResponseReceived, lambda e, *args: self._increment_response_stats(e))
        page.add_handler(zd.cdp.network.RequestWillBeSent, lambda e, *args: self._log_request(e, spider=spider))
        page.add_handler(zd.cdp.network.ResponseReceived, lambda e, *args: self._log_response(e, spider=spider, request=request))
        page.add_handler(zd.cdp.network.LoadingFailed, lambda e, *args: self._log_blocked_request(e))

        return page


    async def close(self):
        logger.info("Closing download handler")
        await super().close()
        await self._close()


    async def _close(self) -> None:
        """Close the browser instance."""
        # Close all idle pages
        for page in self._idle_pages:
            try:
                if not page.closed:
                    await page.close()
            except:
                pass
        self._idle_pages.clear()

        # Close the shared browser instance (only once)
        global _browser, _browser_lock
        async with _browser_lock:
            if _browser:
                logger.info("Stopping browser")
                await _browser.stop()
                _browser = None


    async def download_request(self, request: Request, **kwargs):
        if request.meta.get("zendriver"):
            self.resp_status = 200
            return await self._download_request(request, kwargs.get('spider'))
        return await super().download_request(request, **kwargs)


    async def _download_request(self, request: Request, spider: Spider) -> Response:
        page: Tab = request.meta.get("zendriver_page")
        page_created = False
        if not isinstance(page, Tab) or page.closed:
            page = await self._create_page(request=request, spider=spider)
            page_created = True

        try:
            return await self._download_request_with_page(request, page, spider, page_created)
        except Exception as ex:
            if not request.meta.get("zendriver_include_page") and not page.closed:
                logger.warning(
                    "Error in request: %s exc_type=%s exc_msg=%s",
                    request,
                    type(ex),
                    str(ex),
                    extra={
                        "spider": spider,
                        "scrapy_request_url": request.url,
                        "scrapy_request_method": request.method,
                        "exception": ex,
                    },
                    exc_info=True,
                )
                # Return page to idle pool for reuse and release semaphore
                target_id = page.target_id
                if target_id in self._page_callbacks:
                    del self._page_callbacks[target_id]
                self._idle_pages.append(page)
                if page_created:
                    self.semaphore.release()
            raise


    async def _download_request_with_page(self, request: Request, page: Tab, spider: Spider, page_created: bool = False) -> Response:
        if request.meta.get("zendriver_include_page"):
            request.meta["zendriver_page"] = page

        start_time = time.time()
        headers = {}
        def capture_headers(event, *args):
            nonlocal headers
            # Skip if not a RequestWillBeSent event
            if not hasattr(event, 'request'):
                return
            if event.request.url.strip("/") == request.url.strip("/"):
                headers = dict(event.request.headers)
        page.add_handler(zd.cdp.network.RequestWillBeSent, capture_headers)

        if self.config.blocked_urls:
            await page.send(zd.cdp.network.enable())
            await page.send(zd.cdp.network.set_blocked_ur_ls(self.config.blocked_urls))

        try:
            await page.get(request.url)
        except Exception as ex:
            logger.debug(
                "Navigating to %s failed",
                request.url,
                extra={
                    "spider": spider,
                    "scrapy_request_url": request.url,
                    "scrapy_request_method": request.method,
                },
            )
        try:
            await self._apply_page_methods(page, request, spider)
        except Exception as ex:
            await page.aopen()  # temporary patch for websocket closed issue when page_method failed
            raise
        body_str = await page.get_content()
        request.meta["download_latency"] = time.time() - start_time

        # Handle page lifecycle after request completion
        target_id = page.target_id
        if request.meta.get("zendriver_include_page"):
            # Keep page for reuse, but release semaphore
            # Page stays in _page_callbacks for tracking
            self.semaphore.release()
            logger.debug("Page %s kept for reuse (zendriver_include_page=True)", target_id)
        else:
            # Return page to idle pool for reuse and release semaphore
            if target_id in self._page_callbacks:
                del self._page_callbacks[target_id]
            self._idle_pages.append(page)
            self.semaphore.release()
            logger.debug("Returned page %s to idle pool (pool size: %d)", target_id, len(self._idle_pages))

        body, encoding = _encode_body(headers=headers, text=body_str)
        respcls = responsetypes.from_args(headers=headers, url=request.url, body=body)
        return respcls(
            url=request.url,
            status=self.resp_status,
            headers=headers,
            body=body,
            request=request,
            flags=["zendriver"],
            encoding=encoding,
            ip_address=None,
        )


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


    def _increment_request_stats(self, event) -> None:
        """Increment request stats."""
        if not hasattr(event, 'type_'):
            return
        stats_prefix = "zendriver/request_count"
        self.stats.inc_value(stats_prefix)
        self.stats.inc_value(f"{stats_prefix}/resource_type/{event.type_.value}")


    def _increment_response_stats(self, event) -> None:
        """Increment response stats."""
        if not hasattr(event, 'type_'):
            return
        stats_prefix = "zendriver/response_count"
        self.stats.inc_value(stats_prefix)
        self.stats.inc_value(f"{stats_prefix}/resource_type/{event.type_.value}")


    def _log_request(self, event, spider: Spider = None) -> None:
        """Log network requests."""
        if not hasattr(event, 'request'):
            return
        log_args = [event.request.method.upper(), event.request.url, event.type_.value]
        referrer = _get_header_value(event.request, "referer")
        if referrer:
            log_args.append(referrer)
            log_msg = "Request: <%s %s> (resource type: %s, referrer: %s)"
        else:
            log_msg = "Request: <%s %s> (resource type: %s)"
        logger.debug(
            log_msg,
            *log_args,
            extra={
                "spider": spider,
                "zendriver_request_url": event.request.url,
                "zendriver_request_method": event.request.method.upper(),
                "zendriver_resource_type": event.type_.value,
            },
        )


    def _log_response(self, event, spider: Spider = None, request: Response = None) -> None:
        """Log network responses."""
        # Skip if not a ResponseReceived event
        if not hasattr(event, 'response'):
            return
        if not any(fnmatch.fnmatch(event.response.url, pattern) for pattern in self.config.blocked_urls):
            log_args = [event.response.status, event.response.url]
            location = _get_header_value(event.response, "location")
            if location:
                log_args.append(location)
                log_msg = "Response: <%i %s> (location: %s)"
            else:
                log_msg = "Response: <%i %s>"
            logger.debug(
                log_msg,
                *log_args,
                extra={
                    "spider": spider,
                    "zendriver_response_url": event.response.url,
                    "zendriver_response_status": event.response.status,
                },
            )
            if request.url.strip("/") == event.response.url.strip("/"):
                self.resp_status = event.response.status
        else:
            logger.debug(
                "Aborted Zendriver request <%s %s>",
                "GET",
                event.response.url,
                extra={
                    "spider": spider,
                    "zendriver_request_url": event.response.url,
                    "zendriver_request_method": "GET",
                },
            )


    def _log_blocked_request(self, event) -> None:
        """Log blocked network requests."""
        # Skip if not a LoadingFailed event with blocked_reason
        if not hasattr(event, 'blocked_reason'):
            return
        if event.blocked_reason:
            if event.blocked_reason.value == "inspector":
                self.stats.inc_value("zendriver/request_count/aborted")


    def _close_page_callback(self, target_id: str) -> None:
        """Callback when a page is closed."""
        if target_id in self._page_callbacks:
            del self._page_callbacks[target_id]
        self.semaphore.release()
