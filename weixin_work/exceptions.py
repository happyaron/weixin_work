class WeixinWorkError(Exception):
    """Base exception for this library."""


class WebhookError(WeixinWorkError):
    """Raised when a webhook call fails."""

    def __init__(self, message: str, errcode: int = 0, errmsg: str = ""):
        super().__init__(message)
        self.errcode = errcode
        self.errmsg = errmsg


class APIError(WeixinWorkError):
    """Raised when the WeCom App API returns a non-zero errcode."""

    def __init__(self, errcode: int, errmsg: str):
        super().__init__(f"WeCom API error {errcode}: {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg
