# Copyright (c) 2026 Faryaan Khan
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Alert delivery: email (any SMTP account, e.g. Gmail app password) and a Telegram bot.

Both are optional and configured with environment variables. When a channel isn't configured
the message is only logged, so the app runs with zero setup.

Email:    SMTP_HOST, SMTP_PORT (587 STARTTLS or 465 SSL), SMTP_USER, SMTP_PASSWORD, SMTP_FROM
Telegram: TELEGRAM_BOT_TOKEN (from @BotFather). The webhook is registered automatically using
          PUBLIC_URL or Render's RENDER_EXTERNAL_URL.
"""
from __future__ import annotations

import html
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx

from . import security

log = logging.getLogger("skyward.notify")
_bot: dict = {}


def public_url() -> str:
    return (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000").rstrip("/")


# ---------------- email ----------------

def email_enabled() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))


def send_email(to: str, subject: str, text: str, html_body: str | None = None, unsubscribe_url: str | None = None) -> bool:
    if not email_enabled():
        log.info("[email disabled] to=%s subject=%s", to, subject)
        return False
    msg = EmailMessage()
    sender = os.getenv("SMTP_FROM") or os.environ["SMTP_USER"]
    msg["From"] = formataddr(("Skyward Alerts", sender))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    if unsubscribe_url:  # one-click unsubscribe in Gmail/Outlook (RFC 8058)
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
                s.send_message(msg)
        return True
    except Exception as e:
        log.warning("email to %s failed: %s", to, e)
        return False


def email_html(title: str, lines: list[str], button: tuple[str, str] | None = None, footer: str = "") -> str:
    """Simple, client-safe HTML email in Skyward's colours. `lines` must already be HTML-escaped."""
    body = "".join(f'<p style="margin:0 0 12px">{line}</p>' for line in lines)
    btn = (f'<p style="margin:20px 0"><a href="{html.escape(button[1])}" style="background:#c9a96b;color:#1a1408;'
           f'padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:600">{html.escape(button[0])}</a></p>'
           if button else "")
    return (f'<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;padding:24px;color:#2b2823;background:#f3efe7">'
            f'<p style="letter-spacing:.3em;color:#8c6d3a;font-size:13px;margin:0 0 16px">SKYWARD</p>'
            f'<h2 style="margin:0 0 16px;font-size:20px">{html.escape(title)}</h2>{body}{btn}'
            f'<p style="color:#7d7669;font-size:12px;margin-top:24px">{footer}</p></div>')


# ---------------- telegram ----------------

def telegram_enabled() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN"))


def _tg(method: str, **payload) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = httpx.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=15)
    return r.json()


def bot_username() -> str | None:
    if not telegram_enabled():
        return None
    if "username" not in _bot:
        try:
            _bot["username"] = _tg("getMe").get("result", {}).get("username")
        except Exception as e:
            log.warning("telegram getMe failed: %s", e)
            return None
    return _bot.get("username")


def setup_telegram_webhook() -> bool:
    """Point the bot at /api/telegram/webhook with a secret header. Safe to call on every start."""
    if not telegram_enabled() or "localhost" in public_url():
        return False
    try:
        res = _tg("setWebhook", url=f"{public_url()}/api/telegram/webhook", allowed_updates=["message"],
                  secret_token=security.telegram_secret(os.environ["TELEGRAM_BOT_TOKEN"]))
        if not res.get("ok"):
            log.warning("telegram setWebhook failed: %s", res.get("description"))
        bot_username()
        return bool(res.get("ok"))
    except Exception as e:
        log.warning("telegram setup failed: %s", e)
        return False


def send_telegram(chat_id: str, text: str, buttons: list[tuple[str, str]] | None = None) -> bool:
    if not telegram_enabled():
        log.info("[telegram disabled] chat=%s text=%s", chat_id, text)
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "url": u}] for t, u in buttons]}
    try:
        return bool(_tg("sendMessage", **payload).get("ok"))
    except Exception as e:
        log.warning("telegram send failed: %s", e)
        return False
