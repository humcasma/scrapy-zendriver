import scrapy


class QuoteItem(scrapy.Item):
    """Item to store quote data."""
    text = scrapy.Field()
    author = scrapy.Field()
    tags = scrapy.Field()
