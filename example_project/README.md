# Example Scrapy Project using scrapy-zendriver

This is a simple Scrapy project that demonstrates how to use the [scrapy-zendriver](https://github.com/humcasma/scrapy-zendriver) middleware for handling JavaScript-rendered pages.

## Installation

1. Install the dependencies:
```bash
pip install scrapy zendriver
pip install -e ..  # Install scrapy-zendriver from the parent directory
```

Or install from requirements:
```bash
pip install -r requirements.txt
```

## Usage

### Basic Spider (zendriver enabled)

Run the basic zendriver spider:
```bash
scrapy crawl quotes_zendriver -o quotes.json
```

To see the browser in action:
```bash
scrapy crawl quotes_zendriver -s ZENDRIVER_HEADLESS=False -o quotes.json
```

### Advanced Spider (with page methods)

Run the advanced spider that demonstrates page interactions:
```bash
scrapy crawl quotes_zendriver_advanced -o quotes_advanced.json
```

This spider demonstrates:
- Taking screenshots
- Page scrolling
- Keeping pages open across multiple requests

### Simple Spider (mixed regular and zendriver)

Run the simple spider that mixes regular Scrapy with zendriver:
```bash
scrapy crawl quotes_simple -o quotes_simple.json
```

## Key Concepts

### Enabling zendriver for a request

To use zendriver for a specific request, set `meta['zendriver'] = True`:

```python
yield scrapy.Request(
    url="https://example.com",
    callback=self.parse,
    meta={"zendriver": True},  # Enable zendriver
)
```

### Using Page Methods

Page methods allow you to interact with the page before parsing:

```python
from scrapy_zendriver.page import PageMethod

yield scrapy.Request(
    url="https://example.com",
    callback=self.parse,
    meta={
        "zendriver": True,
        "zendriver_page_methods": [
            # Execute JavaScript
            PageMethod("evaluate", "document.title"),
            # Scroll to bottom
            PageMethod("evaluate", "window.scrollTo(0, document.body.scrollHeight)"),
            # Click an element
            PageMethod("click", "button.submit"),
        ],
    },
)
```

Note: zendriver uses `screenshot_b64()` instead of `screenshot()` if you need screenshots.

### Configuration

Key settings in `settings.py`:

```python
# Enable zendriver download handler
DOWNLOAD_HANDLERS = {
    "http": "scrapy_zendriver.handler.ScrapyZendriverDownloadHandler",
    "https": "scrapy_zendriver.handler.ScrapyZendriverDownloadHandler",
}

# Zendriver options
ZENDRIVER_HEADLESS = True  # Run browser in background
ZENDRIVER_MAX_CONCURRENT_PAGES = 5  # Limit concurrent pages
ZENDRIVER_BLOCKED_URLS = ["*.png", "*.jpg", "*.css"]  # Block unnecessary resources
```

## Output

The spiders will output JSON files with quote data:

```json
[
  {
    "text": "\"The world as we have created it is a process of our thinking. It cannot be changed without changing our thinking.\"",
    "author": "Albert Einstein",
    "tags": ["change", "deep-thoughts", "thinking", "world"]
  },
  ...
]
```

## Troubleshooting

- **Browser not starting**: Ensure you have a compatible browser installed for zendriver
- **Requests timing out**: Increase `DOWNLOAD_TIMEOUT` in settings
- **Memory issues**: Reduce `ZENDRIVER_MAX_CONCURRENT_PAGES`

## License

This example project is provided as a proof of concept for testing the scrapy-zendriver middleware.
