"""
WebhookClient — send messages to a WeCom group-chat robot webhook.

Webhook URL pattern:
    https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<KEY>

Obtain the key from the group chat → "Add Robot" settings page.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple, Union

import requests

from .exceptions import WebhookError, WeixinWorkError
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

# Match the ``key=<value>`` query parameter anywhere in a URL-bearing string.
# The webhook key is effectively the bearer credential for the target chat
# room; the WeCom API mandates it in the query string, so we can't remove it
# from outgoing requests — but we can make sure it never appears in raised
# exception messages (``requests.HTTPError`` stringifies with the full URL by
# default) or in any log line we emit ourselves.
_KEY_QUERY_RE = re.compile(r"([?&]key=)[^&\s'\"]+", re.IGNORECASE)


def _scrub(text: str) -> str:
    """Redact the webhook ``key=…`` query param from a URL or error text."""
    if not text:
        return text
    return _KEY_QUERY_RE.sub(r"\1***", text)

# Same response-size cap as AppClient — WeCom webhook responses are always
# small JSON.  Guards against memory exhaustion from a misrouted or
# hostile endpoint.
_MAX_RESPONSE_BYTES = 1 * 1024 * 1024

HTTPTimeout = Union[float, Tuple[float, float], None]


def _read_capped_json(resp: requests.Response) -> dict:
    """Parse a JSON response with a hard byte cap; see AppClient docstring."""
    cl = resp.headers.get("Content-Length")
    if cl is not None:
        try:
            announced = int(cl)
        except ValueError:
            announced = -1
        if announced > _MAX_RESPONSE_BYTES:
            resp.close()
            raise WeixinWorkError(
                f"response too large: Content-Length={announced} "
                f"exceeds cap of {_MAX_RESPONSE_BYTES} bytes"
            )
    body = bytearray()
    for chunk in resp.iter_content(chunk_size=8192):
        if chunk:
            body.extend(chunk)
            if len(body) > _MAX_RESPONSE_BYTES:
                resp.close()
                raise WeixinWorkError(
                    f"response exceeded cap of {_MAX_RESPONSE_BYTES} bytes"
                )
    return json.loads(bytes(body))


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
        timeout: HTTPTimeout = 10,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.key = key or os.environ.get("WEIXIN_WORK_WEBHOOK_KEY") or ""
        if not self.key:
            raise ValueError(
                "Webhook key is required.  Pass it directly or set the "
                "WEIXIN_WORK_WEBHOOK_KEY environment variable."
            )
        self.timeout: HTTPTimeout = timeout
        self._session = session or requests.Session()

    def __repr__(self) -> str:
        # The webhook key is effectively a bearer credential for the chat
        # room; suppress it from reprs so it doesn't land in tracebacks.
        return "WebhookClient(key=***)"

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
        try:
            resp = self._session.post(self._send_url, json=payload,
                                      timeout=self.timeout, stream=True)
            resp.raise_for_status()
        except requests.RequestException as exc:
            # ``requests`` default str() for HTTPError / ConnectionError /
            # Timeout embeds the full URL (including key=…) in the message;
            # re-raise via WebhookError with the key redacted.  ``from None``
            # suppresses the original exception chain so the URL doesn't
            # resurface via __cause__ / __context__.
            raise WebhookError(
                f"webhook HTTP call failed: {_scrub(str(exc))}",
                errcode=-1, errmsg="http-error",
            ) from None
        data = _read_capped_json(resp)
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
        try:
            with path.open("rb") as fh:
                resp = self._session.post(
                    self._upload_url,
                    files={"media": (path.name, fh)},
                    timeout=self.timeout,
                    stream=True,
                )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise WebhookError(
                f"webhook file upload failed: {_scrub(str(exc))}",
                errcode=-1, errmsg="http-error",
            ) from None
        data = _read_capped_json(resp)
        if data.get("errcode", 0) != 0:
            raise WebhookError(
                f"File upload failed: {data}",
                errcode=data.get("errcode", -1),
                errmsg=data.get("errmsg", ""),
            )
        return data["media_id"]
