"""
WebhookClient — send messages to a WeCom group-chat robot webhook.

Webhook URL pattern:
    https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<KEY>

Obtain the key from the group chat → "Add Robot" settings page.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Union

import requests

from .exceptions import WebhookError
from .messages import (
    FileMessage,
    ImageMessage,
    MarkdownMessage,
    NewsArticle,
    NewsMessage,
    TemplateCardMessage,
    TextMessage,
)

_BASE = "https://qyapi.weixin.qq.com/cgi-bin/webhook"


class WebhookClient:
    """Client for a single WeCom group-chat webhook.

    Args:
        key:     The webhook key (the part after ``?key=`` in the URL).
                 If omitted, read from the ``WEIXIN_WORK_WEBHOOK_KEY``
                 environment variable.
        timeout: HTTP request timeout in seconds (default 10).
        session: Optional pre-configured ``requests.Session`` for connection
                 pooling or proxy configuration.
    """

    def __init__(
        self,
        key: Optional[str] = None,
        *,
        timeout: int = 10,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.key = key or os.environ.get("WEIXIN_WORK_WEBHOOK_KEY") or ""
        if not self.key:
            raise ValueError(
                "Webhook key is required.  Pass it directly or set the "
                "WEIXIN_WORK_WEBHOOK_KEY environment variable."
            )
        self.timeout = timeout
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    @property
    def _send_url(self) -> str:
        return f"{_BASE}/send?key={self.key}"

    @property
    def _upload_url(self) -> str:
        return f"{_BASE}/upload_media?key={self.key}&type=file"

    def _post(self, payload: dict) -> dict:
        resp = self._session.post(self._send_url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise WebhookError(
                f"Webhook send failed: {data}",
                errcode=data.get("errcode", -1),
                errmsg=data.get("errmsg", ""),
            )
        return data

    # ------------------------------------------------------------------
    # Public: send pre-built message objects
    # ------------------------------------------------------------------

    def send(
        self,
        message: Union[
            TextMessage,
            MarkdownMessage,
            ImageMessage,
            NewsMessage,
            FileMessage,
            TemplateCardMessage,
        ],
    ) -> dict:
        """Send any message object.  Returns the parsed API response."""
        return self._post(message.to_dict())

    def send_raw(self, payload: dict) -> dict:
        """Send an arbitrary JSON payload (for unsupported message types)."""
        return self._post(payload)

    # ------------------------------------------------------------------
    # Public: convenience helpers
    # ------------------------------------------------------------------

    def send_text(
        self,
        content: str,
        *,
        mentioned_list: Optional[List[str]] = None,
        mentioned_mobile_list: Optional[List[str]] = None,
    ) -> dict:
        """Send a plain-text message.

        Args:
            content:               Message body.
            mentioned_list:        User IDs to @mention, or ["@all"].
            mentioned_mobile_list: Phone numbers to @mention, or ["@all"].
        """
        return self.send(
            TextMessage(
                content=content,
                mentioned_list=mentioned_list or [],
                mentioned_mobile_list=mentioned_mobile_list or [],
            )
        )

    def send_markdown(self, content: str) -> dict:
        """Send a Markdown-formatted message."""
        return self.send(MarkdownMessage(content=content))

    def send_image(self, source: Union[str, Path, bytes]) -> dict:
        """Send an image.

        Args:
            source: A file path (str or Path) or raw image bytes.
        """
        if isinstance(source, (str, Path)):
            msg = ImageMessage.from_file(source)
        else:
            msg = ImageMessage(data=source)
        return self.send(msg)

    def send_news(self, articles: List[NewsArticle]) -> dict:
        """Send one or more news-card articles (1–8)."""
        return self.send(NewsMessage(articles=articles))

    def send_file(self, media_id: str) -> dict:
        """Send a previously-uploaded file by its media_id."""
        return self.send(FileMessage(media_id=media_id))

    def send_template_card(
        self,
        title: str,
        description: str,
        url: str,
        *,
        source_text: str = "",
        btn_text: str = "View details",
    ) -> dict:
        """Send a text_notice template card."""
        return self.send(
            TemplateCardMessage(
                title=title,
                description=description,
                url=url,
                source_text=source_text,
                btn_text=btn_text,
            )
        )

    def upload_file(self, path: Union[str, Path]) -> str:
        """Upload a file and return its media_id for use with send_file().

        Args:
            path: Local path to the file to upload.

        Returns:
            The ``media_id`` string returned by the API.
        """
        path = Path(path)
        with path.open("rb") as fh:
            resp = self._session.post(
                self._upload_url,
                files={"media": (path.name, fh)},
                timeout=self.timeout,
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise WebhookError(
                f"File upload failed: {data}",
                errcode=data.get("errcode", -1),
                errmsg=data.get("errmsg", ""),
            )
        return data["media_id"]
