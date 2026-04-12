"""
AppClient — send messages via the WeCom App (企业应用) API.

This API requires:
  - Corp ID      (企业ID)        found in "我的企业" → "企业信息"
  - Corp Secret  (应用Secret)    found in the app's credentials page
  - Agent ID     (AgentId)       found in the app settings

Compared with webhooks, app messages can target individual users, departments,
or tags, and support a wider range of message types.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import List, Optional, Union

import requests

from .exceptions import APIError
from .messages import (
    FileMessage,
    ImageMessage,
    MarkdownMessage,
    NewsArticle,
    NewsMessage,
    TextMessage,
)

_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


class _TokenCache:
    """Thread-safe access-token cache with automatic renewal."""

    def __init__(self) -> None:
        self._token: str = ""
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def get(self, corp_id: str, corp_secret: str, session: requests.Session) -> str:
        with self._lock:
            if time.monotonic() < self._expires_at - 60:
                return self._token
            resp = session.get(
                f"{_BASE}/gettoken",
                params={"corpid": corp_id, "corpsecret": corp_secret},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode", 0) != 0:
                raise APIError(data["errcode"], data.get("errmsg", ""))
            self._token = data["access_token"]
            self._expires_at = time.monotonic() + data.get("expires_in", 7200)
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._expires_at = 0.0


class AppClient:
    """Client for the WeCom App message API.

    Args:
        corp_id:     Your WeCom Corp ID.  Falls back to ``WEIXIN_WORK_CORP_ID``.
        corp_secret: App secret.  Falls back to ``WEIXIN_WORK_CORP_SECRET``.
        agent_id:    App agent ID.  Falls back to ``WEIXIN_WORK_AGENT_ID``.
        timeout:     HTTP request timeout in seconds (default 10).
        session:     Optional pre-configured ``requests.Session``.
    """

    def __init__(
        self,
        corp_id: Optional[str] = None,
        corp_secret: Optional[str] = None,
        agent_id: Optional[int] = None,
        *,
        timeout: int = 10,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.corp_id = corp_id or os.environ.get("WEIXIN_WORK_CORP_ID", "")
        self.corp_secret = corp_secret or os.environ.get("WEIXIN_WORK_CORP_SECRET", "")
        _agent_id = agent_id if agent_id is not None else os.environ.get("WEIXIN_WORK_AGENT_ID")
        self.agent_id = int(_agent_id) if _agent_id is not None else None

        for name, val in [("corp_id", self.corp_id), ("corp_secret", self.corp_secret)]:
            if not val:
                raise ValueError(f"{name} is required.")
        if self.agent_id is None:
            raise ValueError("agent_id is required.")

        self.timeout = timeout
        self._session = session or requests.Session()
        self._token_cache = _TokenCache()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _token(self) -> str:
        return self._token_cache.get(self.corp_id, self.corp_secret, self._session)

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _post(self, endpoint: str, payload: dict, *, retry: bool = True) -> dict:
        url = f"{_BASE}/{endpoint}?access_token={self._token()}"
        resp = self._session.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        errcode = data.get("errcode", 0)
        # 42001 = token expired; refresh once and retry
        if errcode == 42001 and retry:
            self._token_cache.invalidate()
            return self._post(endpoint, payload, retry=False)
        if errcode != 0:
            raise APIError(errcode, data.get("errmsg", ""))
        return data

    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        url = f"{_BASE}/{endpoint}"
        p = {"access_token": self._token(), **(params or {})}
        resp = self._session.get(url, params=p, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise APIError(data["errcode"], data.get("errmsg", ""))
        return data

    # ------------------------------------------------------------------
    # Target helpers — at least one of to_user / to_party / to_tag required
    # ------------------------------------------------------------------

    @staticmethod
    def _target(
        to_user: Union[str, List[str], None],
        to_party: Union[str, List[str], None],
        to_tag: Union[str, List[str], None],
    ) -> dict:
        def _join(v: Union[str, List[str], None]) -> str:
            if v is None:
                return ""
            return "|".join(v) if isinstance(v, list) else v

        result = {
            "touser": _join(to_user),
            "toparty": _join(to_party),
            "totag": _join(to_tag),
        }
        if not any(result.values()):
            raise ValueError("Specify at least one of to_user, to_party, or to_tag.")
        return result

    # ------------------------------------------------------------------
    # Public: send pre-built message objects
    # ------------------------------------------------------------------

    def send(
        self,
        message: Union[TextMessage, MarkdownMessage, ImageMessage, NewsMessage, FileMessage],
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
        safe: int = 0,
        enable_duplicate_check: int = 0,
        duplicate_check_interval: int = 1800,
    ) -> dict:
        """Send any supported message object.

        Args:
            message:   A message object from ``weixin_work.messages``.
            to_user:   User ID(s) or "@all".
            to_party:  Department ID(s).
            to_tag:    Tag ID(s).
            safe:      1 = confidential (no forwarding), 0 = normal.
            enable_duplicate_check: 1 = deduplicate within interval.
            duplicate_check_interval: Dedup window in seconds (max 4 hours).
        """
        payload = {
            **self._target(to_user, to_party, to_tag),
            "agentid": self.agent_id,
            "safe": safe,
            "enable_duplicate_check": enable_duplicate_check,
            "duplicate_check_interval": duplicate_check_interval,
            **message.to_dict(),
        }
        return self._post("message/send", payload)

    def send_raw(self, payload: dict) -> dict:
        """Send an arbitrary payload (for message types not covered by this library)."""
        return self._post("message/send", payload)

    # ------------------------------------------------------------------
    # Public: convenience helpers
    # ------------------------------------------------------------------

    def send_text(
        self,
        content: str,
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
        safe: int = 0,
    ) -> dict:
        """Send a plain-text message."""
        return self.send(
            TextMessage(content=content),
            to_user=to_user,
            to_party=to_party,
            to_tag=to_tag,
            safe=safe,
        )

    def send_markdown(
        self,
        content: str,
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
    ) -> dict:
        """Send a Markdown-formatted message."""
        return self.send(
            MarkdownMessage(content=content),
            to_user=to_user,
            to_party=to_party,
            to_tag=to_tag,
        )

    def send_image(
        self,
        media_id: str,
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
    ) -> dict:
        """Send an image by its media_id (upload first via upload_media)."""
        payload = {
            **self._target(to_user, to_party, to_tag),
            "agentid": self.agent_id,
            "msgtype": "image",
            "image": {"media_id": media_id},
        }
        return self._post("message/send", payload)

    def send_news(
        self,
        articles: List[NewsArticle],
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
    ) -> dict:
        """Send news article cards."""
        return self.send(
            NewsMessage(articles=articles),
            to_user=to_user,
            to_party=to_party,
            to_tag=to_tag,
        )

    def send_file(
        self,
        media_id: str,
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
    ) -> dict:
        """Send a file by its media_id."""
        return self.send(
            FileMessage(media_id=media_id),
            to_user=to_user,
            to_party=to_party,
            to_tag=to_tag,
        )

    # ------------------------------------------------------------------
    # Media upload
    # ------------------------------------------------------------------

    def upload_media(
        self,
        path: Union[str, Path],
        media_type: str = "file",
    ) -> str:
        """Upload a file/image/voice/video and return its media_id.

        Args:
            path:       Local file path.
            media_type: One of "image", "voice", "video", "file".

        Returns:
            The ``media_id`` string (valid for 3 days).
        """
        path = Path(path)
        url = f"{_BASE}/media/upload?access_token={self._token()}&type={media_type}"
        with path.open("rb") as fh:
            resp = self._session.post(
                url,
                files={"media": (path.name, fh)},
                timeout=self.timeout,
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise APIError(data["errcode"], data.get("errmsg", ""))
        return data["media_id"]
