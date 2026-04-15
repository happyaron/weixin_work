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

import json
import os
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union

import requests

from .exceptions import APIError, WeixinWorkError
from .messages import (
    FileMessage,
    ImageMessage,
    MarkdownMessage,
    NewsArticle,
    NewsMessage,
    TextMessage,
)

HTTPTimeout = Union[float, Tuple[float, float], None]

_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

# WeCom errcodes that signal the access_token in the request URL was no
# longer valid server-side.  On any of these we invalidate the cached token
# (compare-and-swap) and retry the request exactly once.
#   42001 — access_token expired.
#   40014 — access_token invalid (e.g. revoked mid-flight, wrong corp/app).
_TOKEN_REFRESH_ERRCODES = frozenset({42001, 40014})

# Maximum accepted response body size.  Real WeCom responses are always a
# few KB of JSON; anything above this is a misrouted / hostile endpoint.
# The cap is enforced both up-front (Content-Length header when provided)
# and while streaming, so memory usage stays bounded even if the server
# lies about Content-Length.
_MAX_RESPONSE_BYTES = 1 * 1024 * 1024   # 1 MB

# Refresh-skew used by _TokenCache: a token is considered stale this many
# seconds before its real expiry, so slow requests / GC pauses / clock
# jitter don't push us over the cliff mid-flight.  WeCom tokens live
# 7200 s, so 300 s of headroom is ~4 % of their lifetime.
_TOKEN_REFRESH_SKEW = 300


def _read_capped_json(resp: requests.Response) -> dict:
    """
    Parse a JSON response body with a hard byte cap, streaming as we go
    so a misbehaving or hostile endpoint can't exhaust memory.

    Callers must pass ``stream=True`` when issuing the request (otherwise
    ``requests`` has already read the whole body into memory before we get
    a chance to check the size).  Content-Length is checked up-front when
    the server supplies it; regardless, ``iter_content`` is bounded by
    ``_MAX_RESPONSE_BYTES`` so even a lying server is safe.
    """
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


class _TokenCache:
    """Thread-safe access-token cache with automatic renewal."""

    def __init__(self) -> None:
        self._token: str = ""
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def get(self, corp_id: str, corp_secret: str, session: requests.Session) -> str:
        with self._lock:
            if time.monotonic() < self._expires_at - _TOKEN_REFRESH_SKEW:
                return self._token
            resp = session.get(
                f"{_BASE}/gettoken",
                params={"corpid": corp_id, "corpsecret": corp_secret},
                timeout=10,
                stream=True,
            )
            resp.raise_for_status()
            data = _read_capped_json(resp)
            if data.get("errcode", 0) != 0:
                raise APIError(data["errcode"], data.get("errmsg", ""))
            self._token = data["access_token"]
            self._expires_at = time.monotonic() + data.get("expires_in", 7200)
            return self._token

    def invalidate(self, failed_token: str) -> None:
        """Mark the cache as stale, but only if *failed_token* is still the
        currently-cached value.

        Compare-and-swap prevents a thundering-herd wipe: if two threads both
        see a 42001 / 40014 for the same expired token, the first refreshes
        and the second's invalidate() is a no-op (because the cache now holds
        a fresh token that the second thread just hasn't picked up yet).
        Without this, the second thread would clear a perfectly good fresh
        token and trigger another round-trip to gettoken.
        """
        with self._lock:
            if self._token == failed_token:
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
        timeout: HTTPTimeout = 10,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.corp_id = corp_id or os.environ.get("WEIXIN_WORK_CORP_ID", "")
        self.corp_secret = corp_secret or os.environ.get("WEIXIN_WORK_CORP_SECRET", "")
        _agent_id = agent_id if agent_id is not None else os.environ.get("WEIXIN_WORK_AGENT_ID")
        if _agent_id is None:
            self.agent_id: Optional[int] = None
        else:
            try:
                self.agent_id = int(_agent_id)
            except (TypeError, ValueError) as exc:
                # Raising a clear ValueError here beats ValueError: invalid
                # literal for int() with base 10: '…' far from the real cause.
                raise ValueError(
                    f"agent_id must be an integer (got {_agent_id!r}; "
                    f"check WEIXIN_WORK_AGENT_ID env var or explicit kwarg)"
                ) from exc

        for name, val in [("corp_id", self.corp_id), ("corp_secret", self.corp_secret)]:
            if not val:
                raise ValueError(f"{name} is required.")
        if self.agent_id is None:
            raise ValueError("agent_id is required.")

        self.timeout: HTTPTimeout = timeout
        self._session = session or requests.Session()
        self._token_cache = _TokenCache()

    def __repr__(self) -> str:
        # Explicit repr so the corp_secret never appears in tracebacks or
        # logs.  Dataclass-style default reprs would dump it otherwise.
        return (
            f"AppClient(corp_id={self.corp_id!r}, agent_id={self.agent_id!r}, "
            f"corp_secret=***)"
        )

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _token(self) -> str:
        return self._token_cache.get(self.corp_id, self.corp_secret, self._session)

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _post(self, endpoint: str, payload: dict, *, retry: bool = True) -> dict:
        token = self._token()
        url = f"{_BASE}/{endpoint}?access_token={token}"
        resp = self._session.post(url, json=payload, timeout=self.timeout, stream=True)
        resp.raise_for_status()
        data = _read_capped_json(resp)
        errcode = data.get("errcode", 0)
        if errcode in _TOKEN_REFRESH_ERRCODES and retry:
            self._token_cache.invalidate(failed_token=token)
            return self._post(endpoint, payload, retry=False)
        if errcode != 0:
            raise APIError(errcode, data.get("errmsg", ""))
        return data

    def _get(self, endpoint: str, params: Optional[dict] = None, *, retry: bool = True) -> dict:
        token = self._token()
        url = f"{_BASE}/{endpoint}"
        p = {"access_token": token, **(params or {})}
        resp = self._session.get(url, params=p, timeout=self.timeout, stream=True)
        resp.raise_for_status()
        data = _read_capped_json(resp)
        errcode = data.get("errcode", 0)
        if errcode in _TOKEN_REFRESH_ERRCODES and retry:
            self._token_cache.invalidate(failed_token=token)
            return self._get(endpoint, params, retry=False)
        if errcode != 0:
            raise APIError(errcode, data.get("errmsg", ""))
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
        def _join(field: str, v: Union[str, List[str], None]) -> str:
            if v is None:
                return ""
            if isinstance(v, list):
                parts = [str(x) for x in v]
            elif isinstance(v, str):
                # A bare string is taken verbatim — callers that want to
                # target multiple recipients should pass a list.  Embedded
                # "|" in a plain string would silently reinterpret the
                # input as multiple recipients, which is almost certainly
                # a caller bug.
                parts = [v]
            else:
                parts = [str(v)]
            for p in parts:
                if not p:
                    raise ValueError(
                        f"{field}: empty recipient ID — WeCom will reject "
                        f"a trailing/interior '|' in the joined string"
                    )
                if "|" in p:
                    raise ValueError(
                        f"{field}: recipient {p!r} contains '|' — that "
                        f"character is the API list separator; pass a list "
                        f"instead of a pre-joined string"
                    )
            return "|".join(parts)

        result = {
            "touser":  _join("to_user",  to_user),
            "toparty": _join("to_party", to_party),
            "totag":   _join("to_tag",   to_tag),
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
        enable_duplicate_check: int = 0,
        duplicate_check_interval: int = 1800,
    ) -> dict:
        """Send a plain-text message.  See ``send()`` for dedup semantics."""
        return self.send(
            TextMessage(content=content),
            to_user=to_user,
            to_party=to_party,
            to_tag=to_tag,
            safe=safe,
            enable_duplicate_check=enable_duplicate_check,
            duplicate_check_interval=duplicate_check_interval,
        )

    def send_markdown(
        self,
        content: str,
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
        enable_duplicate_check: int = 0,
        duplicate_check_interval: int = 1800,
    ) -> dict:
        """Send a Markdown-formatted message.  See ``send()`` for dedup semantics."""
        return self.send(
            MarkdownMessage(content=content),
            to_user=to_user,
            to_party=to_party,
            to_tag=to_tag,
            enable_duplicate_check=enable_duplicate_check,
            duplicate_check_interval=duplicate_check_interval,
        )

    def send_image(
        self,
        media_id: str,
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
        enable_duplicate_check: int = 0,
        duplicate_check_interval: int = 1800,
    ) -> dict:
        """Send an image by its media_id (upload first via upload_media).

        See ``send()`` for dedup semantics.
        """
        payload = {
            **self._target(to_user, to_party, to_tag),
            "agentid": self.agent_id,
            "msgtype": "image",
            "image": {"media_id": media_id},
            "enable_duplicate_check": enable_duplicate_check,
            "duplicate_check_interval": duplicate_check_interval,
        }
        return self._post("message/send", payload)

    def send_news(
        self,
        articles: List[NewsArticle],
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
        enable_duplicate_check: int = 0,
        duplicate_check_interval: int = 1800,
    ) -> dict:
        """Send news article cards.  See ``send()`` for dedup semantics."""
        return self.send(
            NewsMessage(articles=articles),
            to_user=to_user,
            to_party=to_party,
            to_tag=to_tag,
            enable_duplicate_check=enable_duplicate_check,
            duplicate_check_interval=duplicate_check_interval,
        )

    def send_file(
        self,
        media_id: str,
        *,
        to_user: Union[str, List[str], None] = None,
        to_party: Union[str, List[str], None] = None,
        to_tag: Union[str, List[str], None] = None,
        enable_duplicate_check: int = 0,
        duplicate_check_interval: int = 1800,
    ) -> dict:
        """Send a file by its media_id.  See ``send()`` for dedup semantics."""
        return self.send(
            FileMessage(media_id=media_id),
            to_user=to_user,
            to_party=to_party,
            to_tag=to_tag,
            enable_duplicate_check=enable_duplicate_check,
            duplicate_check_interval=duplicate_check_interval,
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
        token = self._token()
        url = f"{_BASE}/media/upload?access_token={token}&type={media_type}"
        with path.open("rb") as fh:
            resp = self._session.post(
                url,
                files={"media": (path.name, fh)},
                timeout=self.timeout,
                stream=True,
            )
        resp.raise_for_status()
        data = _read_capped_json(resp)
        errcode = data.get("errcode", 0)
        if errcode != 0:
            # No in-call retry: the file stream has already been consumed and
            # re-seeking isn't always possible (non-regular files).  But CAS
            # invalidate the token so the caller's next request gets a fresh
            # one instead of hitting 42001 again.
            if errcode in _TOKEN_REFRESH_ERRCODES:
                self._token_cache.invalidate(failed_token=token)
            raise APIError(errcode, data.get("errmsg", ""))
        return data["media_id"]
