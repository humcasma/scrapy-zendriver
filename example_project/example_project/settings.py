BOT_NAME = "example_project"

SPIDER_MODULES = ["example_project.spiders"]
NEWSPIDER_MODULE = "example_project.spiders"

# Crawl responsibly only if needed
ROBOTSTXT_OBEY = False

# Configure maximum concurrent requests performed by Scrapy (default: 16)
# CONCURRENT_REQUESTS = 32

# Configure a delay for requests for the same website (default: 0)
# DOWNLOAD_DELAY = 3

# Enable or disable the zendriver download handler
# This is the key setting for using scrapy-zendriver
DOWNLOAD_HANDLERS = {
    "http": "scrapy_zendriver.handler.ScrapyZendriverDownloadHandler",
    "https": "scrapy_zendriver.handler.ScrapyZendriverDownloadHandler",
}

# Zendriver-specific settings
ZENDRIVER_HEADLESS = False  # Set to False to see the browser
ZENDRIVER_MAX_CONCURRENT_PAGES = 5  # Limit concurrent browser pages
# Block image/fonts to speed up scraping (CSS kept for proper page rendering)
ZENDRIVER_BLOCKED_URLS = [
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.woff",
    "*.woff2",
]

# Enable or disable extensions
# See: https://docs.scrapy.org/en/latest/topics/extensions.html
# EXTENSIONS = {
#     "scrapy.extensions.telnet.TelnetConsole": None,
# }

# Configure item pipelines
# ITEM_PIPELINES = {
#     "example_project.pipelines.ExampleProjectPipeline": 300,
# }

# Enable and configure the AutoThrottle extension
# AUTOTHROTTLE_ENABLED = True
# AUTOTHROTTLE_START_DELAY = 5
# AUTOTHROTTLE_MAX_DELAY = 60
# AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

# Enable showing throttling stats for every response received
# AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching
# HTTPCACHE_ENABLED = True
# HTTPCACHE_EXPIRATION_SECS = 0
# HTTPCACHE_DIR = "httpcache"
# HTTPCACHE_IGNORE_HTTP_CODES = []
# HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Set settings whose default value is deprecated to a future-proof value
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
