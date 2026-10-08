from __future__ import annotations

import re

from telegram_notifier import TelegramClient


def check_configuration(environment, *, client=None):
    channel = environment.get("TELEGRAM_RESULTS_CHAT_ID", "").strip()
    users = environment.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not re.fullmatch(r"-100\d+", channel):
        raise ValueError(
            "TELEGRAM_RESULTS_CHAT_ID must be a numeric channel ID beginning with -100"
        )
    if not re.fullmatch(r"[1-9]\d*(?:\s*,\s*[1-9]\d*)*", users):
        raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain positive human user IDs")
    client = client or TelegramClient(environment.get("TELEGRAM_BOT_TOKEN", ""))
    bot = client.call("getMe", {})
    webhook = client.call("getWebhookInfo", {})
    if webhook.get("url"):
        raise ValueError(
            "A webhook is active; polling requires removing it before starting the service"
        )
    chat = client.call("getChat", {"chat_id": channel})
    if chat.get("type") != "channel" or str(chat.get("id")) != channel:
        raise ValueError("The configured destination is not the requested channel")
    member = client.call("getChatMember", {"chat_id": channel, "user_id": bot["id"]})
    if member.get("status") not in {"administrator", "creator"} or (
        member.get("status") == "administrator" and not member.get("can_post_messages")
    ):
        raise ValueError("The bot must be a channel administrator with permission to post messages")
    return {
        "bot": bot["username"],
        "channel_id": chat["id"],
        "channel_title": chat.get("title"),
        "can_post_messages": True,
        "allowed_user_ids": sorted({int(value) for value in users.split(",")}),
        "webhook_active": False,
        "messages_sent": 0,
    }
