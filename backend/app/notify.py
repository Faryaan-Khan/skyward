"""Free notification channels: email (SMTP) and Telegram bot.

Configure via environment variables. When a channel isn't configured the
message is logged instead, so the app runs fine with zero setup.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import httpx

log = logging.getLogger("skyward.notify")


def send_email(to: str, subject: str, body: str) -> bool:
    host = os.getenv("SMTP_HOST")
    if not host:
        log.info("[email disabled] to=%s subject=%s", to, subject)
        return False
    msg = EmailMessage()
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "skyward@localhost"))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as s:
        s.starttls()
        if os.getenv("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    return True


def send_telegram(chat_id: str, text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.info("[telegram disabled] chat=%s text=%s", chat_id, text)
        return False
    r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    return r.is_success


def dispatch(watch: dict, subject: str, body: str) -> None:
    if watch.get("email"):
        send_email(watch["email"], subject, body)
    if watch.get("telegram_chat_id"):
        send_telegram(watch["telegram_chat_id"], f"{subject}\n{body}")
