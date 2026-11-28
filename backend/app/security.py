# Copyright (c) 2026 Faryaan Khan
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small security helpers: signed one-purpose links and hashed secrets.

* Watch manage tokens are stored as SHA-256 hashes, so a database leak can't be used to
  manage other people's alerts.
* Email confirmation, unsubscribe and Telegram-connect links carry an HMAC signature bound to
  one purpose and one watch id, so they can't be forged or reused for something else.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

log = logging.getLogger("skyward.security")
_warned = False


def _key() -> bytes:
    global _warned
    k = os.getenv("SECRET_KEY") or os.getenv("ADMIN_TOKEN")
    if not k:
        if not _warned:
            log.warning("SECRET_KEY not set: using an insecure development key")
            _warned = True
        k = "dev-only-insecure-key"
    return k.encode()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sign(purpose: str, watch_id: int) -> str:
    """Short URL-safe signature for (purpose, id), e.g. for /confirm?w=12&s=..."""
    return hmac.new(_key(), f"{purpose}:{watch_id}".encode(), hashlib.sha256).hexdigest()[:32]


def verify(purpose: str, watch_id: int, sig: str) -> bool:
    return bool(sig) and hmac.compare_digest(sign(purpose, watch_id), sig)


def telegram_secret(bot_token: str) -> str:
    """Secret Telegram must echo back on every webhook call (header X-Telegram-Bot-Api-Secret-Token)."""
    return hmac.new(_key(), f"telegram:{bot_token}".encode(), hashlib.sha256).hexdigest()[:48]
