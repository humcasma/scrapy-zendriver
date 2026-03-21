# scrapy-zendriver: Zendriver integration for Scrapy

[![version](https://img.shields.io/pypi/v/scrapy-zendriver.svg)](https://pypi.python.org/pypi/scrapy-zendriver)
[![pyversions](https://img.shields.io/pypi/pyversions/scrapy-zendriver.svg)](https://pypi.python.org/pypi/scrapy-zendriver)

A [Scrapy](https://github.com/scrapy/scrapy) Download Handler which performs requests using
[Zendriver](https://github.com/cdpdriver/zendriver). It can be used to handle pages that require JavaScript (among other things),
while adhering to the regular Scrapy workflow (i.e. without interfering with request scheduling, item processing, etc).

**What makes this package different** from similar projects like [Scrapy-Playwright](https://github.com/scrapy-plugins/scrapy-playwright) is the optimization to stay undetected by most anti-bot solutions.
[CDP](https://chromedevtools.github.io/devtools-protocol/) communication provides even better resistance against web application firewalls (WAFs), while performance gets a massive boost.

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Activation](#activation)
- [Basic Usage](#basic-usage)
- [Advanced Usage](#advanced-usage)
  - [Executing actions on pages](#executing-actions-on-pages)
  - [Using CallablePageMethod for custom interactions](#using-callablepagemethod-for-custom-interactions)
  - [Reusing pages across requests](#reusing-pages-across-requests)
  - [Receiving Page objects in callbacks](#receiving-page-objects-in-callbacks)
- [Configuration](#configuration)
- [Example Project](#example-project)
- [Known Issues](#known-issues)

---

## Features

- **JavaScript rendering** - Execute JavaScript and extract dynamically loaded content
- **Browser automation** - Click buttons, fill forms, scroll pages, take screenshots
- **Anti-bot evasion** - Uses CDP protocol for better stealth against bot detection
- **Scrapy-native** - Integrates seamlessly with Scrapy's request/response workflow
- **Concurrent pages** - Manage multiple browser tabs with configurable limits
- **Resource blocking** - Block images, fonts, and other unnecessary resources for faster scraping

---

## Requirements

- **Python** >= 3.8
- **Scrapy** >= 2.0 (!= 2.4.0)
- **Chrome** (or Chromium) installed on the system

After the release of [Scrapy 2.0](https://docs.scrapy.org/en/latest/news.html#scrapy-2-0-0-2020-03-03),
which includes [coroutine syntax support](https://docs.scrapy.org/en/2.0/topics/coroutines.html)
and [asyncio support](https://docs.scrapy.org/en/2.0/topics/asyncio.html), Scrapy allows
integrating `asyncio`-based projects such as `Zendriver`.

---

## Installation

`scrapy-zendriver` is available on PyPI and can be installed with `pip`:

```bash
pip install scrapy-zendriver
```

`zendriver` is defined as a dependency and will be installed automatically.

---

## Activation

### Download handler

Replace the default `http` and/or `https` Download Handlers through
[`DOWNLOAD_HANDLERS`](https://docs.scrapy.org/en/latest/topics/settings.html):

```python
# settings.py
DOWNLOAD_HANDLERS = {
    "http": "scrapy_zendriver.handler.ScrapyZendriverDownloadHandler",
    "https": "scrapy_zendriver.handler.ScrapyZendriverDownloadHandler",
}
```

> **Note:** The `ScrapyZendriverDownloadHandler` class inherits from the default HTTP/HTTPS handler. Unless explicitly marked (see [Basic usage](#basic-usage)), requests will be processed by the regular Scrapy download handler.

### Twisted reactor

[Install the `asyncio`-based Twisted reactor](https://docs.scrapy.org/en/latest/topics/asyncio.html#installing-the-asyncio-reactor):

```python
# settings.py
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
```

This is the default in new projects since [Scrapy 2.7](https://github.com/scrapy/scrapy/releases/tag/2.7.0).

---

## Basic Usage

Set the `zendriver` [Request.meta](https://docs.scrapy.org/en/latest/topics/request-response.html#scrapy.http.Request.meta) key to download a request using Zendriver:

```python
import scrapy

class AwesomeSpider(scrapy.Spider):
    name = "awesome"

    def start_requests(self):
        # GET request with JavaScript rendering
        yield scrapy.Request("https://httpbin.org/get", meta={"zendriver": True})

    def parse(self, response, **kwargs):
        # 'response' contains the page as seen by the browser
        return {"url": response.url}
```

---

## Advanced Usage

### Executing actions on pages

Pass an iterable of [`PageMethod`](#pagemethod-class) objects in `zendriver_page_methods` to perform actions on the page before the response is returned:

```python
import scrapy
from scrapy_zendriver.page import PageMethod

class ScrollSpider(scrapy.Spider):
    name = "scroll"

    def start_requests(self):
        yield scrapy.Request(
            url="http://quotes.toscrape.com/scroll",
            meta={
                "zendriver": True,
                "zendriver_page_methods": [
                    # Wait for content to load
                    PageMethod("wait_for", "div.quote"),
                    # Scroll to bottom (infinite scroll)
                    PageMethod("evaluate", "window.scrollBy(0, document.body.scrollHeight)"),
                    # Wait for more content to load
                    PageMethod("wait_for", "div.quote:nth-child(11)"),
                ],
            },
        )

    def parse(self, response, **kwargs):
        # Page has been scrolled, extract all loaded quotes
        return {"quote_count": len(response.css("div.quote"))}
```

### Using CallablePageMethod for custom interactions

For complex interactions (like filling forms), use `CallablePageMethod` to pass custom async functions:

```python
import scrapy
from scrapy_zendriver.page import PageMethod, CallablePageMethod

class LoginSpider(scrapy.Spider):
    name = "login"
    start_urls = ["https://example.com/login"]

    async def do_login(self, page):
        """Custom login logic using the page object."""
        # Click login link
        login_link = await page.find("Login")
        await login_link.click()
        
        # Fill username
        username_input = await page.select("#username", timeout=15)
        await username_input.send_keys("admin")
        
        # Fill password
        pass_input = await page.select("#password", timeout=5)
        await pass_input.send_keys("password")
        
        # Submit
        login_btn = await page.select("input[value=Login]", timeout=5)
        await login_btn.click()
        
        # Wait for navigation
        await page.wait()

    def start_requests(self):
        yield scrapy.Request(
            url=self.start_urls[0],
            callback=self.parse_after_login,
            meta={
                "zendriver": True,
                "zendriver_include_page": True,
                "zendriver_page_methods": [
                    # Execute custom login function
                    CallablePageMethod(self.do_login),
                ],
            },
        )

    def parse_after_login(self, response):
        # Page is now the post-login homepage
        return {"url": response.url}
```

### Reusing pages across requests

Use `zendriver_include_page=True` to keep pages open and reuse them:

```python
class ChainSpider(scrapy.Spider):
    name = "chain"

    def start_requests(self):
        yield scrapy.Request(
            url="https://example.org/page1",
            callback=self.parse_page1,
            meta={"zendriver": True, "zendriver_include_page": True},
        )

    def parse_page1(self, response):
        page = response.meta["zendriver_page"]
        # Reuse same page for next request
        yield scrapy.Request(
            url="https://example.org/page2",
            callback=self.parse_page2,
            meta={"zendriver": True, "zendriver_include_page": True, "zendriver_page": page},
        )

    async def parse_page2(self, response):
        page = response.meta["zendriver_page"]
        title = await page.title()
        await page.close()  # Remember to close!
        return {"title": title}
```

> **Important:** When using `zendriver_include_page=True`, always close pages when done to avoid memory leaks and hitting `ZENDRIVER_MAX_CONCURRENT_PAGES` limits.

### Receiving Page objects in callbacks

Access the Zendriver `Tab` object directly in your callback:

```python
from zendriver import Tab

class ScreenshotSpider(scrapy.Spider):
    name = "screenshot"

    def start_requests(self):
        yield scrapy.Request(
            url="https://example.org",
            meta={"zendriver": True, "zendriver_include_page": True},
        )

    async def parse(self, response, **kwargs):
        page: Tab = response.meta["zendriver_page"]
        
        # Take screenshot
        await page.save_screenshot(filename="example.png", full_page=True)
        
        # Get page title
        title = await page.title()
        
        # Close page
        await page.close()
        
        return {"title": title, "screenshot": "example.png"}
```

---

## Configuration

### `ZENDRIVER_HEADLESS`
Type: `bool`, Default: `True`

Run browser in headless mode (no visible window).

```python
ZENDRIVER_HEADLESS = False  # Show browser window (useful for debugging)
```

### `ZENDRIVER_MAX_CONCURRENT_PAGES`
Type: `int`, Default: Value of Scrapy's `CONCURRENT_REQUESTS`

Maximum number of concurrent browser pages allowed.

```python
ZENDRIVER_MAX_CONCURRENT_PAGES = 8
```

### `ZENDRIVER_BLOCKED_URLS`
Type: `List[str]`, Default: `[]`

Block specific resources (images, fonts, etc.) to speed up scraping.

```python
ZENDRIVER_BLOCKED_URLS = [
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.webp",
    "*.woff",
    "*.woff2",
]
```

---

## Supported `Request.meta` keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `zendriver` | `bool` | `False` | Enable Zendriver for this request |
| `zendriver_include_page` | `bool` | `False` | Keep page open and pass to callback |
| `zendriver_page_methods` | `Iterable[PageMethod]` | `()` | Actions to execute on the page |
| `zendriver_page` | `Optional[Tab]` | `None` | Specific page to use for request |

---

## PageMethod class

### `scrapy_zendriver.page.PageMethod(method: str, *args, **kwargs)`

Represents a method to be called on a `zendriver.Tab` object.

- `method`: Name of the method (e.g., `"evaluate"`, `"wait_for"`, `"save_screenshot"`)
- `*args`, `**kwargs`: Arguments passed to the method
- `result`: Return value stored after execution

### `scrapy_zendriver.page.CallablePageMethod(callable, *args, **kwargs)`

Represents a custom callable (function/coroutine) to be executed on the page.

- `callable`: Async function that receives the `page` object as first argument
- `*args`, `**kwargs`: Additional arguments passed to the callable
- `result`: Return value stored after execution

---

## Example Project

A complete example project is included in the `example_project/` directory demonstrating:

- Basic zendriver usage
- Login automation with `CallablePageMethod`
- Filtering by tag
- Page methods for scrolling and interaction

```bash
cd example_project
pip install -r requirements.txt
pip install -e ..

# Run basic spider
scrapy crawl quotes_zendriver -o quotes.json

# Run with visible browser
scrapy crawl quotes_zendriver -s ZENDRIVER_HEADLESS=False -o quotes.json

# Run login + filter spider
scrapy crawl quotes_login_filter -o books_quotes.json
```

---

## Known Issues

### No proxy support
Specifying a proxy via the `proxy` Request meta key is not supported.

### Page lifecycle management
When using `zendriver_include_page=True`, you are responsible for closing pages to avoid memory leaks.

---

## Reporting issues

Before opening an issue, please ensure the unexpected behavior can only be observed by using this package and not with standalone Zendriver. To do this, translate your spider code to a reasonably close Zendriver script: if the issue also occurs this way, you should instead report it [upstream](https://github.com/cdpdriver/zendriver).

For instance:

```python
# Scrapy spider
import scrapy
from scrapy_zendriver.page import PageMethod

class ExampleSpider(scrapy.Spider):
    name = "example"

    def start_requests(self):
        yield scrapy.Request(
            url="https://example.org",
            meta={
                "zendriver": True,
                "zendriver_page_methods": [
                    PageMethod("save_screenshot", filename="example.jpeg", full_page=True),
                ],
            },
        )
```

Translates roughly to:

```python
# Standalone Zendriver script
import asyncio
import zendriver as uc

async def main():
    browser = await uc.start()
    page = await browser.get("https://example.org")
    await page.save_screenshot(filename="example.jpeg", full_page=True)
    await page.close()

if __name__ == '__main__':
    uc.loop().run_until_complete(main())
```
