from typing import Any, Callable


__all__ = ["PageMethod", "CallablePageMethod"]


class PageMethod:
    """
    Represents a method to be called (and awaited if necessary) on a
    Zendriver page, such as "click", "screenshot", "evaluate", etc.
    """

    def __init__(self, method: str, *args, **kwargs) -> None:
        self.method: str = method
        self.args: tuple = args
        self.kwargs: dict = kwargs
        self.result: Any = None

    def __str__(self) -> str:
        return f"<{self.__class__.__name__} for method '{self.method}'>"

    __repr__ = __str__


class CallablePageMethod(PageMethod):
    """
    Represents a callable (function/coroutine) to be executed on a Zendriver page.
    
    The callable receives the page object as its first argument and can perform
    arbitrary interactions like clicking, filling forms, etc.
    
    Example:
        async def login(page):
            await page.find('Login').click()
            input = await page.select('#username')
            await input.send_keys('admin')
        
        yield scrapy.Request(
            url="https://example.com/login",
            meta={
                "zendriver": True,
                "zendriver_page_methods": [
                    CallablePageMethod(login),
                ],
            },
        )
    """

    def __init__(self, callable_method: Callable, *args, **kwargs) -> None:
        super().__init__(method="callable", *args, **kwargs)
        self.callable_method = callable_method

    def __str__(self) -> str:
        return f"<{self.__class__.__name__} for callable '{self.callable_method.__name__}'>"

    __repr__ = __str__