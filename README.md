# weixin_work

Convenient Python client for the WeCom (企业微信 / Weixin Work) messaging API.

Two delivery modes, picked per credential type:

- **`WebhookClient`** — send to a group-chat robot using just the key from
  the chat's *Add Robot* settings page. No corp-level setup required.
- **`AppClient`** — use Corp ID + App Secret + Agent ID to target
  individual users, departments, or tags with a wider range of message
  types.

## Install

```sh
pip install -e .                 # from this repo
# or, once published to PyPI:
# pip install weixin-work
```

## Quick start — webhook

```python
import os
from weixin_work import WebhookClient, NewsArticle

bot = WebhookClient(os.environ["WEIXIN_WORK_WEBHOOK_KEY"])

bot.send_text("Hello from weixin_work!")
bot.send_text("Heads up everyone!", mentioned_list=["@all"])
bot.send_markdown(
    "## Deploy complete\n"
    "> Environment: **production**\n"
    "> Status: <font color='info'>success</font>"
)
bot.send_image("/tmp/screenshot.png")
bot.send_news([
    NewsArticle(title="Release v2.0",
                url="https://example.com/release/v2.0",
                description="New features and bug fixes"),
])

media_id = bot.upload_file("/tmp/report.pdf")
bot.send_file(media_id)
```

## Quick start — app

```python
import os
from weixin_work import AppClient

app = AppClient(
    corp_id=os.environ["WEIXIN_WORK_CORP_ID"],
    corp_secret=os.environ["WEIXIN_WORK_CORP_SECRET"],
    agent_id=int(os.environ["WEIXIN_WORK_AGENT_ID"]),
)

app.send_text("Broadcast message!", to_user="@all")
app.send_markdown("Weekly report ready.", to_party="engineering")
app.send_text("Confidential memo.", to_user="alice", safe=1)

media_id = app.upload_media("/tmp/report.pdf", media_type="file")
app.send_file(media_id, to_user="@all")
```

## Message types

| Class                 | Webhook | App | Notes                                             |
|-----------------------|:-------:|:---:|---------------------------------------------------|
| `TextMessage`         | ✓       | ✓   | 2048-byte UTF-8 cap, enforced locally             |
| `MarkdownMessage`     | ✓       | ✓   | WeCom's markdown subset                           |
| `ImageMessage`        | ✓       | ✓   | Webhook: inline base64; App: via `upload_media`   |
| `NewsMessage`         | ✓       | ✓   | 1–8 article cards                                 |
| `FileMessage`         | ✓       | ✓   | Requires a prior `upload_file` / `upload_media`   |
| `TemplateCardMessage` | ✓       | —   | `text_notice` variant                             |

For message types not covered, build the payload yourself and call
`send_raw(payload)` on either client.

## Credentials

All credentials can come from environment variables or be passed directly:

| Env var                    | Used by         |
|----------------------------|-----------------|
| `WEIXIN_WORK_WEBHOOK_KEY`  | `WebhookClient` |
| `WEIXIN_WORK_CORP_ID`      | `AppClient`     |
| `WEIXIN_WORK_CORP_SECRET`  | `AppClient`     |
| `WEIXIN_WORK_AGENT_ID`     | `AppClient`     |

Never commit real secrets. `examples.py` uses the env-var form exclusively
to make the "oops, pasted a real secret" failure mode harder to stumble
into; `.gitignore` covers Python bytecode artifacts.

## Thread safety, retries, and response caps

- `AppClient` caches the access token behind a mutex and refreshes it on
  expiry. A one-shot retry fires when the API returns 42001 (token
  expired) or 40014 (token invalid); the invalidation is compare-and-swap
  against the token that actually hit the error, so concurrent failures
  don't stomp a fresh token that another thread just refreshed.
- Both clients accept an optional pre-configured `requests.Session`, so
  callers can share connection pools or install proxy / retry / auth
  middleware once.
- Response bodies are read with a 1 MB cap via streaming — a misrouted or
  hostile endpoint can't exhaust memory even if it lies about
  `Content-Length`.
- Request timeouts accept the full `requests` shape:
  `Union[float, Tuple[float, float], None]` (connect / read split).

## Idempotency

`AppClient` exposes WeCom's `enable_duplicate_check` and
`duplicate_check_interval` on every send helper, so callers retrying on
network errors can let the API deduplicate server-side:

```python
app.send_text(
    "important alert",
    to_user="@all",
    enable_duplicate_check=1,
    duplicate_check_interval=300,      # seconds; max 14 400 (4 h)
)
```

## Security posture

- Webhook keys and app secrets are masked in each client's `__repr__`, so
  they don't appear in tracebacks or stringified log records.
- `WebhookError` messages are scrubbed of the `key=…` query parameter
  before being raised; the underlying `requests.HTTPError` /
  `ConnectionError` (whose default `str()` embeds the full URL) is
  suppressed from the exception chain via `raise … from None`.
- No `eval` / `exec` / `pickle` / `shell=True` / `yaml.load` usage; SSL
  verification is left at the `requests` default (on); `hashlib.md5` is
  called with `usedforsecurity=False` (the MD5 use is required by the API
  purely for body integrity, and the flag keeps it working on FIPS builds).

## Dependencies

- Python ≥ 3.8
- `requests` ≥ 2.20

## Playground

`examples.py` has one-file walkthroughs of both clients:

```sh
WEIXIN_WORK_WEBHOOK_KEY=... DEMO_MODE=webhook python examples.py

WEIXIN_WORK_CORP_ID=...   WEIXIN_WORK_CORP_SECRET=... \
WEIXIN_WORK_AGENT_ID=...  DEMO_MODE=app python examples.py
```

## Exceptions

| Class              | Raised when                                                            |
|--------------------|------------------------------------------------------------------------|
| `WeixinWorkError`  | Base class; also raised directly for oversize responses                |
| `APIError`         | `AppClient` receives non-zero `errcode`                                |
| `WebhookError`     | `WebhookClient` receives non-zero `errcode`, or an HTTP-layer failure  |
