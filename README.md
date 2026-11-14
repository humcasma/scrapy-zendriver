# scrapy-zendriver: Zendriver integration for Scrapy

[![version](https://img.shields.io/pypi/v/scrapy-zendriver.svg)](https://pypi.python.org/pypi/scrapy-zendriver)
[![pyversions](https://img.shields.io/pypi/pyversions/scrapy-zendriver.svg)](https://pypi.python.org/pypi/scrapy-zendriver)

A [Scrapy](https://github.com/scrapy/scrapy) Download Handler which performs requests using
[Zendriver](https://github.com/cdpdriver/zendriver) (based on a fork of [scrapy-nodriver](https://github.com/Ehsan-U/scrapy-nodriver), modified and improved to use [zendriver](https://github.com/cdpdriver/zendriver)). It can be used to handle pages that require JavaScript (among other things),
while adhering to the regular Scrapy workflow (i.e. without interfering with request scheduling, item processing, etc).

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
  - [Page lifecycle management](#page-lifecycle-management)
  - [Receiving Page objects in callbacks](#receiving-page-objects-in-callbacks)
- [Configuration](#configuration)
- [Automatic Features](#automatic-features)
- [Example Project](#example-project)
- [Known Issues](#known-issues)

---

## Features

- **JavaScript rendering** - Execute JavaScript and extract dynamically loaded content
- **Browser automation** - Click buttons, fill forms, scroll pages, take screenshots
- **Anti-bot evasion** - Uses CDP protocol for better stealth against bot detection
- **Cloudflare bypass** - Automatically detects and solves Cloudflare Turnstile challenges
- **Scrapy-native** - Integrates seamlessly with Scrapy's request/response workflow
- **Concurrent pages** - Manage multiple browser tabs with configurable limits
- **Resource blocking** - Block images, fonts, and other unnecessary resources for faster scraping
- **Cookie persistence** - Browser cookies are automatically shared across all requests

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

`scrapy-zendriver` is not yet available on PyPI. Install it directly from the source:

```bash
# Clone or navigate to the scrapy-zendriver directory
cd /path/to/scrapy-zendriver

# Install in editable mode (recommended for development)
pip install -e .

# Or install directly from a local path
pip install /path/to/scrapy-zendriver
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

[The `asyncio`-based Twisted reactor](https://docs.scrapy.org/en/latest/topics/asyncio.html#installing-the-asyncio-reactor) is the default one in new projects since [Scrapy 2.7](https://github.com/scrapy/scrapy/releases/tag/2.7.0). For earlier versions you need to select it manually:

```python
# settings.py
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
```
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

> **Important:** You do NOT need `zendriver_include_page=True` to execute page methods. Page methods are applied automatically before the response is returned to your callback.

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

### Page lifecycle management

Understanding page lifecycle is crucial for efficient scraping:

#### Default behavior (recommended)

By default, pages are **automatically managed**:

```python
yield scrapy.Request(
    url="https://example.org",
    meta={"zendriver": True},  # That's all you need!
    callback=self.parse,
)
```

**What happens:**
1. A page is created (or reused from idle pool)
2. Your request is processed
3. Page methods are executed (if any)
4. Page is returned to idle pool for reuse
5. Page is automatically closed when spider closes

#### `zendriver_include_page` - Advanced control

Use `zendriver_include_page=True` ONLY if you need direct access to the Page object in your callback:

```python
yield scrapy.Request(
    url="https://example.org",
    meta={
        "zendriver": True,
        "zendriver_include_page": True,  # Keep page open
    },
    callback=self.parse,
)

async def parse(self, response, **kwargs):
    page = response.meta["zendriver_page"]  # Access the Page object
    
    # Do something with the page
    title = await page.title()
    await page.save_screenshot("example.png")
    
    # ⚠️ YOU MUST CLOSE THE PAGE YOURSELF ⚠️
    await page.close()
    
    return {"title": title}
```

**Important notes:**

| Aspect | Default (`zendriver_include_page=False`) | With `zendriver_include_page=True` |
|--------|------------------------------------------|-------------------------------------|
| Page access in callback | ❌ Not available | ✅ Available via `response.meta["zendriver_page"]` |
| Page reuse | ✅ Returned to idle pool | ❌ NOT returned to pool |
| Page closing | ✅ Automatic | ⚠️ **Manual (your responsibility)** |
| Use case | Standard scraping | Advanced browser control |

> **⚠️ Warning:** If you use `zendriver_include_page=True` and forget to close the page:
> - The page will NOT be reused by other requests
> - The page will NOT be automatically closed
> - You may hit `ZENDRIVER_MAX_CONCURRENT_PAGES` limits
> - Memory leaks can occur

**When to use `zendriver_include_page=True`:**
- You need to call Page methods directly in your callback
- You need to reuse the exact same page across multiple requests
- You need fine-grained control over page lifecycle

**When NOT to use it:**
- Standard scraping (use `zendriver_page_methods` instead)
- You just want to scroll, click, or wait for elements
- You're unsure (default is safer)

### Receiving Page objects in callbacks

If you need the Page object in your callback (see above section):

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
        
        # Close page (REQUIRED!)
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

### Reload on block

Some bot protections (e.g. Akamai) block the first visit, then let a reload through once their script has set a valid cookie. When a page looks blocked, the handler waits, reloads it, and checks again.

A page counts as blocked when its status is in `ZENDRIVER_BLOCK_STATUS_CODES` and its body contains one of `ZENDRIVER_BLOCK_MARKERS` (case-insensitive). With no markers, every blocking status code counts.

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `ZENDRIVER_BLOCK_STATUS_CODES` | `List[int]` | `[403]` | Status codes that can mean a block |
| `ZENDRIVER_BLOCK_MARKERS` | `List[str]` | `[]` | Body text that confirms a block |
| `ZENDRIVER_BLOCK_RELOADS` | `int` | `1` | Reloads to try (`0` disables) |
| `ZENDRIVER_BLOCK_WAIT` | `float` | `2.0` | Seconds to wait before each reload |

```python
ZENDRIVER_BLOCK_MARKERS = ["Access Denied", "errors.edgesuite.net"]  # Akamai
ZENDRIVER_BLOCK_RELOADS = 2
```

A response that is still blocked after the reloads gets the `"blocked"` flag (`"blocked" in response.flags`). Stats: `zendriver/blocked`, `zendriver/blocked/reload`, `zendriver/blocked/recovered`, `zendriver/blocked/gave_up`.

### `DOWNLOAD_TIMEOUT`
Type: `float`, Default: `180.0`

Maximum time in seconds to wait for page navigation.

```python
DOWNLOAD_TIMEOUT = 60.0  # 1 minute timeout
```

---

## Automatic Features

### Cookie persistence

All cookies are **automatically shared** across requests because they're tied to the browser instance, not individual pages:

```python
class CookieSpider(scrapy.Spider):
    name = "cookie"
    start_urls = ["https://example.com/login"]

    def parse(self, response):
        # After login, cookies persist automatically
        yield scrapy.Request(
            url="https://example.com/protected",
            callback=self.parse_protected,
            meta={"zendriver": True},
        )  # No need to manually handle cookies!
```

**Key points:**
- Cookies are stored at the **browser level**
- All requests share the same browser session
- Login state persists across all subsequent requests
- No need to manually extract/transfer cookies

### Cloudflare protection

Cloudflare challenges are **automatically detected and bypassed**:

```python
class CloudflareSpider(scrapy.Spider):
    name = "cloudflare"
    start_urls = ["https://example-protected-by-cloudflare.com"]

    def parse(self, response):
        # If Cloudflare challenge is detected, it's automatically solved
        return {"title": response.css("title::text").get()}
```

**How it works:**
1. Response is checked for Cloudflare indicators (status 403/503, challenge DOM)
2. If challenge detected, the Turnstile checkbox is automatically clicked
3. Waits for page redirect after bypass
4. Returns the actual page content

**Logging:**
```
[scrapy.zendriver] DEBUG: Cloudflare indicators found on https://example.com, verifying DOM...
[scrapy.zendriver] INFO: Cloudflare challenge detected! Attempting bypass...
[scrapy.zendriver] INFO: Cloudflare checkbox bypassed! Waiting for page redirect...
```

**Note:** Cloudflare bypass adds minimal overhead - normal pages (without challenges) are processed instantly with zero delay.

---

## Supported `Request.meta` keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `zendriver` | `bool` | `False` | Enable Zendriver for this request |
| `zendriver_include_page` | `bool` | `False` | Keep page open and pass to callback (requires manual close) |
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
- Concurrent vs sequential processing

```bash
# Navigate to the example project
cd example_project

# Install dependencies
pip install -r requirements.txt

# Install scrapy-zendriver (from parent directory)
pip install -e ..

# Run basic spider
scrapy crawl quotes_zendriver -o quotes.json

# Run with visible browser
scrapy crawl quotes_zendriver -s ZENDRIVER_HEADLESS=False -o quotes.json

# Run login + filter spider
scrapy crawl quotes_login_filter -o books_quotes.json

# Run advanced spider with CallablePageMethod
scrapy crawl quotes_zendriver_advanced -o advanced.json
```

---

## Known Issues

### No proxy support
Specifying a proxy via the `proxy` Request meta key is not supported.

### Page lifecycle management
When using `zendriver_include_page=True`, you are responsible for closing pages to avoid memory leaks and hitting `ZENDRIVER_MAX_CONCURRENT_PAGES` limits.

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
