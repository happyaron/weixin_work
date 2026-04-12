"""
Usage examples for the weixin_work library.

Run any example by setting the required environment variables and then:
    python examples.py
"""

import os
from weixin_work import WebhookClient, AppClient, NewsArticle


# ============================================================
# WEBHOOK EXAMPLES
# ============================================================

def webhook_examples():
    # Credentials via env-var or direct argument
    bot = WebhookClient(os.environ["WEIXIN_WORK_WEBHOOK_KEY"])

    # 1. Plain text
    bot.send_text("Hello from weixin_work!")

    # 2. Text with @mentions
    bot.send_text(
        "Heads up everyone!",
        mentioned_list=["@all"],
    )

    # 3. Markdown
    bot.send_markdown(
        "## Deploy complete\n"
        "> Environment: **production**\n"
        "> Status: <font color='info'>success</font>"
    )

    # 4. Image from file
    bot.send_image("/tmp/screenshot.png")

    # 5. News cards
    bot.send_news([
        NewsArticle(
            title="Release v2.0",
            url="https://example.com/release/v2.0",
            description="New features and bug fixes",
            picurl="https://example.com/cover.png",
        ),
        NewsArticle(
            title="Docs",
            url="https://example.com/docs",
        ),
    ])

    # 6. Template card
    bot.send_template_card(
        title="Build Failed",
        description="main branch CI pipeline failed on step 'test'.",
        url="https://ci.example.com/builds/1234",
        source_text="CI/CD",
        btn_text="View build",
    )

    # 7. Upload a file and send it
    media_id = bot.upload_file("/tmp/report.pdf")
    bot.send_file(media_id)


# ============================================================
# APP API EXAMPLES
# ============================================================

def app_examples():
    # Credentials from environment (or pass directly)
    app = AppClient(
        #corp_id=os.environ["WEIXIN_WORK_CORP_ID"],
        #corp_secret=os.environ["WEIXIN_WORK_CORP_SECRET"],
        #agent_id=int(os.environ["WEIXIN_WORK_AGENT_ID"]),
        corp_id='<REDACTED_CORP_ID>',
        corp_secret='<REDACTED_CORP_SECRET>',
        agent_id=<REDACTED_AGENT_ID>,
    )

    # 1. Broadcast to everyone
    #app.send_text("Broadcast message!", to_user="@all")

    # 2. Target specific users
    app.send_text("测试信息", to_user="@all")

    # 3. Target a department
    #app.send_markdown(
    #    "## Weekly report ready\nPlease review by EOD Friday.",
    #    to_party="engineering",
    #)

    # 4. Send a confidential message (no forwarding)
    #app.send_text("Confidential memo.", to_user="alice", safe=1)

    # 5. Upload media then send
    #media_id = app.upload_media("/tmp/report.pdf", media_type="file")
    #app.send_file(media_id, to_user="@all")

    # 6. News cards
    #app.send_news(
    #    [NewsArticle(title="Q1 OKRs", url="https://wiki.example.com/okrs")],
    #    to_user="@all",
    #)


if __name__ == "__main__":
    mode = os.environ.get("DEMO_MODE", "app")
    if mode == "webhook":
        webhook_examples()
    elif mode == "app":
        app_examples()
    else:
        print("Set DEMO_MODE=webhook or DEMO_MODE=app")
