"""
Message payload builders for both the Webhook and App APIs.

Each class has a .to_dict() method that returns the JSON-serialisable payload
understood by the WeCom API.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

# WeCom's documented cap on plain-text message bodies (in UTF-8 bytes).
# Exceeding this yields a cryptic server-side errcode with no indication
# of the actual limit, so we validate locally.
_TEXT_MESSAGE_MAX_BYTES = 2048


@dataclass
class TextMessage:
    """Plain-text message, optionally @-mentioning users or phones.

    Args:
        content:        The message body.
        mentioned_list: List of user-ids to @mention, or ["@all"].
        mentioned_mobile_list: List of phone numbers to @mention, or ["@all"].
    """
    content: str
    mentioned_list: List[str] = field(default_factory=list)
    mentioned_mobile_list: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        encoded = self.content.encode("utf-8")
        if len(encoded) > _TEXT_MESSAGE_MAX_BYTES:
            # Fail locally with an actionable message rather than letting
            # WeCom reject this with an opaque errcode.
            raise ValueError(
                f"TextMessage.content too long: {len(encoded)} bytes "
                f"(WeCom cap is {_TEXT_MESSAGE_MAX_BYTES} bytes, UTF-8)"
            )
        payload: dict = {"content": self.content}
        if self.mentioned_list:
            payload["mentioned_list"] = self.mentioned_list
        if self.mentioned_mobile_list:
            payload["mentioned_mobile_list"] = self.mentioned_mobile_list
        return {"msgtype": "text", "text": payload}


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

@dataclass
class MarkdownMessage:
    """Markdown-formatted message (webhook) or rich-text (app).

    Args:
        content: Markdown string.  Supports a limited WeCom markdown subset.
    """
    content: str

    def to_dict(self) -> dict:
        return {"msgtype": "markdown", "markdown": {"content": self.content}}


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------

@dataclass
class ImageMessage:
    """Send an image by supplying either a file path or raw bytes.

    The API requires the base64-encoded image data and its MD5.
    """
    data: bytes = field(repr=False)

    @classmethod
    def from_file(cls, path: str | Path) -> "ImageMessage":
        return cls(data=Path(path).read_bytes())

    def to_dict(self) -> dict:
        b64 = base64.b64encode(self.data).decode()
        # The WeCom API requires an MD5 here purely for integrity — it's
        # not a security-sensitive use of the hash.  usedforsecurity=False
        # (Python 3.9+) lets this work on FIPS-enabled builds that
        # otherwise reject MD5 entirely.  Fall back for older Pythons.
        try:
            md5 = hashlib.md5(self.data, usedforsecurity=False).hexdigest()
        except TypeError:
            md5 = hashlib.md5(self.data).hexdigest()
        return {"msgtype": "image", "image": {"base64": b64, "md5": md5}}


# ---------------------------------------------------------------------------
# News (link-card list)
# ---------------------------------------------------------------------------

@dataclass
class NewsArticle:
    """A single article card inside a NewsMessage.

    Args:
        title:       Card headline (required).
        url:         Link to open when the card is tapped (required).
        description: Short subtitle shown under the title.
        picurl:      Thumbnail image URL.
    """
    title: str
    url: str
    description: str = ""
    picurl: str = ""

    def to_dict(self) -> dict:
        d: dict = {"title": self.title, "url": self.url}
        if self.description:
            d["description"] = self.description
        if self.picurl:
            d["picurl"] = self.picurl
        return d


@dataclass
class NewsMessage:
    """One or more news-article cards.

    Args:
        articles: List of NewsArticle objects (1–8 items).
    """
    articles: List[NewsArticle]

    def to_dict(self) -> dict:
        if not self.articles:
            raise ValueError("NewsMessage requires at least one article.")
        return {
            "msgtype": "news",
            "news": {"articles": [a.to_dict() for a in self.articles]},
        }


# ---------------------------------------------------------------------------
# File  (Webhook only – media_id obtained via upload endpoint)
# ---------------------------------------------------------------------------

@dataclass
class FileMessage:
    """Send a previously-uploaded file by its media_id.

    Obtain a media_id by uploading via WebhookClient.upload_file() or
    AppClient.upload_media().
    """
    media_id: str

    def to_dict(self) -> dict:
        return {"msgtype": "file", "file": {"media_id": self.media_id}}


# ---------------------------------------------------------------------------
# Template Card  (webhook – "text_notice" variant)
# ---------------------------------------------------------------------------

@dataclass
class TemplateCardMessage:
    """A structured template card (text_notice type).

    Covers the most common use-case.  For advanced card types consult the
    WeCom docs and call WebhookClient.send_raw() with a hand-crafted payload.

    Args:
        title:       Card title.
        description: Body text.
        url:         URL opened when the card is tapped.
        source_text: Small label shown at the top-left (e.g. service name).
        btn_text:    CTA button label (default "View details").
    """
    title: str
    description: str
    url: str
    source_text: str = ""
    btn_text: str = "View details"

    def to_dict(self) -> dict:
        card: dict = {
            "card_type": "text_notice",
            "source": {"desc": self.source_text} if self.source_text else {},
            "main_title": {"title": self.title, "desc": self.description},
            "card_action": {"type": 1, "url": self.url},
            "jump_list": [{"type": 1, "url": self.url, "title": self.btn_text}],
        }
        return {"msgtype": "template_card", "template_card": card}
