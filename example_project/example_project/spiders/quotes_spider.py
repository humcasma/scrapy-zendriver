"""
Example spider demonstrating scrapy-zendriver middleware.

This spider scrapes quotes from quotes.toscrape.com, a site designed
for testing web scrapers. It uses zendriver to handle JavaScript-rendered content.

Usage:
    cd example_project
    scrapy crawl quotes_zendriver -o quotes.json
    
    # Or with visible browser:
    scrapy crawl quotes_zendriver -s ZENDRIVER_HEADLESS=False -o quotes.json
"""

import scrapy
from scrapy.http import HtmlResponse
from scrapy_zendriver.page import PageMethod, CallablePageMethod
from example_project.items import QuoteItem


class QuotesZendriverSpider(scrapy.Spider):
    """Spider that uses zendriver to scrape JavaScript-rendered quotes."""

    name = "quotes_zendriver"
    allowed_domains = ["quotes.toscrape.com"]
    start_urls = ["https://quotes.toscrape.com/"]

    async def start(self):
        """
        Start requests with zendriver enabled.
        
        The key is to set meta['zendriver'] = True to route the request
        through the zendriver download handler instead of the default one.
        """
        for url in self.start_urls:
            yield scrapy.Request(
                url=url,
                callback=self.parse,
                meta={"zendriver": True},  # Enable zendriver for this request
            )

    def parse(self, response, **kwargs):
        """
        Parse the quotes page.

        Since zendriver handles JavaScript execution, we can extract
        content that was rendered dynamically.
        """
        # import pdb
        # pdb.set_trace()
        # Extract all quotes on the page
        quotes = response.css("div.quote")

        for quote in quotes:
            yield QuoteItem(
                text=quote.css("span.text::text").get(),
                author=quote.css("small.author::text").get(),
                tags=quote.css("div.tags a.tag::text").getall(),
            )

        # Follow pagination link
        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield response.follow(
                next_page,
                callback=self.parse,
                meta={"zendriver": True},
            )


class QuotesZendriverAdvancedSpider(scrapy.Spider):
    """
    Advanced spider demonstrating zendriver page methods.

    This shows how to use PageMethod to interact with the page
    (clicking, scrolling, etc.) before parsing.
    """

    name = "quotes_zendriver_advanced"
    allowed_domains = ["quotes.toscrape.com"]
    start_urls = ["https://quotes.toscrape.com/login"]

    async def start(self):
        """
        Start requests with zendriver page methods.

        PageMethod allows you to call methods on the zendriver page object,
        such as 'evaluate', 'click', etc.
        CallablePageMethod allows you to pass arbitrary async callables.
        """
        for url in self.start_urls:
            yield scrapy.Request(
                url=url,
                callback=self.parse_quotes,
                meta={
                    "zendriver": True,
                    "zendriver_include_page": True,
                    "zendriver_page_methods": [
                        # Execute JavaScript to get page title
                        PageMethod("evaluate", "document.title"),
                        # Perform login interaction using a callable
                        CallablePageMethod(self.do_login),
                        PageMethod("wait_for", "div.quote")
                    ],
                },
            )

    async def do_login(self, page):
        """
        Perform login interaction on the page.
        
        This method is called by CallablePageMethod with the page object.
        After login, the page automatically navigates to the quotes homepage.
        """
        self.logger.info("Starting login interaction...")
        
        # Click on Login link
        login_link = await page.find('Login')
        await login_link.click()
        self.logger.info("Clicked login link")

        # Fill username field
        username_input = await page.select("#username", timeout=15)
        await username_input.send_keys('admin')
        self.logger.info("Entered username")

        # Fill password field
        pass_input = await page.select("#password", timeout=5)
        await pass_input.send_keys('password')
        self.logger.info("Entered password")

        # Click submit button
        login_btn = await page.select("input[value=Login]", timeout=5)
        await login_btn.click()
        self.logger.info("Clicked login button")

        # Wait for login to complete and page to navigate
        await page.wait()
        self.logger.info("Login complete - page should now be quotes homepage")

    def parse_quotes(self, response, **kwargs):
        """Parse quotes with page interaction."""
        # Log page method results
        page_methods = response.meta.get("zendriver_page_methods", [])
        for pm in page_methods:
            if pm.method == "evaluate":
                self.logger.info(f"Page title via JS: {pm.result}")
            elif isinstance(pm, CallablePageMethod):
                self.logger.info(f"Callable {pm.callable_method.__name__} completed")

        # Extract quotes
        quotes = response.css("div.quote")
        self.logger.info(f"Found {len(quotes)} quotes on page")

        for quote in quotes:
            yield QuoteItem(
                text=quote.css("span.text::text").get(),
                author=quote.css("small.author::text").get(),
                tags=quote.css("div.tags a.tag::text").getall(),
            )

        # Follow pagination
        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield response.follow(
                next_page,
                callback=self.parse_quotes,
                meta={
                    "zendriver": True,
                    "zendriver_include_page": True,
                    "zendriver_page_methods": [
                        # Scroll to bottom before extracting
                        PageMethod("evaluate", "window.scrollTo(0, document.body.scrollHeight)"),
                        PageMethod("wait_for", "div.quote")
                    ],
                },
            )
        else:
            self.logger.info("No more pages - scraping complete!")




class QuotesSimpleSpider(scrapy.Spider):
    """
    Simple spider showing the difference between regular and zendriver requests.

    This spider uses regular Scrapy for the main page but zendriver for
    JavaScript-heavy subpages.
    """

    name = "quotes_simple"
    allowed_domains = ["quotes.toscrape.com"]
    start_urls = ["https://quotes.toscrape.com/"]

    def parse(self, response, **kwargs):
        """Parse with regular Scrapy (no JavaScript)."""
        quotes = response.css("div.quote")
        self.logger.info(f"Found {len(quotes)} quotes (regular Scrapy)")

        for quote in quotes:
            yield QuoteItem(
                text=quote.css("span.text::text").get(),
                author=quote.css("small.author::text").get(),
                tags=quote.css("div.tags a.tag::text").getall(),
            )

        # For the next page, use zendriver to demonstrate switching
        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield response.follow(
                next_page,
                callback=self.parse_zendriver,
                meta={"zendriver": True},  # Switch to zendriver
            )

    def parse_zendriver(self, response, **kwargs):
        """Parse with zendriver (JavaScript enabled)."""
        quotes = response.css("div.quote")
        self.logger.info(f"Found {len(quotes)} quotes (zendriver)")

        for quote in quotes:
            yield QuoteItem(
                text=quote.css("span.text::text").get(),
                author=quote.css("small.author::text").get(),
                tags=quote.css("div.tags a.tag::text").getall(),
            )

        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield response.follow(
                next_page,
                callback=self.parse_zendriver,
                meta={"zendriver": True},
            )


class QuotesConcurrentSpider(scrapy.Spider):
    """
    Spider demonstrating page reuse with zendriver.
    
    This spider processes pages sequentially, reusing the same page object
    across requests. This is more stable than true concurrency with zendriver
    due to CDP connection limitations.
    
    For comparison, run both this and quotes_zendriver_advanced to see
    different approaches:
    - quotes_zendriver: Sequential with page pooling (default)
    - quotes_zendriver_advanced: Login + scrape with CallablePageMethod
    - quotes_concurrent: Multiple independent requests (demonstrates limits)
    
    Note: True concurrent page processing with zendriver is limited by the
    underlying CDP connection. For most use cases, sequential processing with
    page reuse is more stable and efficient.
    """

    name = "quotes_concurrent"
    allowed_domains = ["quotes.toscrape.com"]
    
    # Generate all page URLs upfront
    base_url = "https://quotes.toscrape.com/page/"
    total_pages = 10

    async def start(self):
        """
        Yield multiple independent requests at once.
        
        Note: Due to zendriver's CDP connection limitations, these will be
        processed sequentially even though multiple requests are yielded.
        """
        for page_num in range(1, self.total_pages + 1):
            url = f"{self.base_url}{page_num}/"
            self.logger.info(f"Requesting page {page_num}: {url}")
            yield scrapy.Request(
                url=url,
                callback=self.parse,
                meta={
                    "zendriver": True,
                    # Each request gets its own page from the pool
                },
            )

    def parse(self, response, **kwargs):
        """Parse quotes from a single page."""
        quotes = response.css("div.quote")
        self.logger.info(f"Page {response.url}: Found {len(quotes)} quotes")

        for quote in quotes:
            yield QuoteItem(
                text=quote.css("span.text::text").get(),
                author=quote.css("small.author::text").get(),
                tags=quote.css("div.tags a.tag::text").getall(),
            )

    def closed(self, reason):
        """Log stats when spider closes."""
        max_concurrent = self.crawler.stats.get_value('zendriver/page_count/max_concurrent', 0)
        total_pages = self.crawler.stats.get_value('zendriver/page_count', 0)
        self.logger.info(f"Spider closed: {reason}")
        self.logger.info(f"Total pages processed: {total_pages}")
        self.logger.info(f"Max concurrent pages reached: {max_concurrent}")
        self.logger.info(f"ZENDRIVER_MAX_CONCURRENT_PAGES setting: {self.crawler.settings.getint('ZENDRIVER_MAX_CONCURRENT_PAGES')}")
        self.logger.info("Note: Sequential processing is more stable with zendriver due to CDP connection limits")
