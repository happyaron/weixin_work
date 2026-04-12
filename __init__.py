"""
weixin_work — A Python library for sending messages via Weixin Work (WeCom / 企业微信).

Two sending modes are supported:

  1. Webhook  — drop a key from a group-chat webhook URL, no auth dance required.
  2. App API  — uses Corp ID + App Secret + Agent ID for user/department targeting.

Quick start (webhook):
    from weixin_work import WebhookClient
    bot = WebhookClient("YOUR_WEBHOOK_KEY")
    bot.send_text("Hello!")

Quick start (app):
    from weixin_work import AppClient
    app = AppClient(corp_id="...", corp_secret="...", agent_id=1000001)
    app.send_text("Hello!", to_user="@all")
"""

from .webhook import WebhookClient
from .app import AppClient
from .messages import (
    TextMessage,
    MarkdownMessage,
    ImageMessage,
    NewsMessage,
    NewsArticle,
    FileMessage,
    TemplateCardMessage,
)
from .exceptions import WeixinWorkError, APIError, WebhookError

__all__ = [
    "WebhookClient",
    "AppClient",
    "TextMessage",
    "MarkdownMessage",
    "ImageMessage",
    "NewsMessage",
    "NewsArticle",
    "FileMessage",
    "TemplateCardMessage",
    "WeixinWorkError",
    "APIError",
    "WebhookError",
]
