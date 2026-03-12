"""
Telegram notification service.

This module encapsulates sending messages to Telegram via the Bot API.
It uses ``httpx`` for asynchronous HTTP requests. Only the minimum
functionality required by the monitoring application is implemented:
sending plain text messages to a single chat. Should additional
features (e.g. inline keyboards, markdown formatting) be required in
the future, they can be added here without changing the rest of the
codebase.
"""

import logging
from typing import Optional

import httpx

from app.core.config import AppSettings


logger = logging.getLogger(__name__)


class TelegramService:
    """Service responsible for sending messages to a Telegram chat."""

    def __init__(self, settings: AppSettings) -> None:
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        # Single HTTP client reused for all requests
        timeout = httpx.Timeout(10.0, connect=5.0)
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send_message(self, text: str) -> bool:
        """Send a text message to the configured chat.

        Parameters
        ----------
        text: str
            The message body. Keep it short; Telegram limits messages
            to roughly 4096 characters.

        Returns
        -------
        bool
            True if the message was sent successfully, False otherwise.
        """
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.warning("Telegram API returned an error: %s", data)
                return False
            return True
        except Exception as exc:
            logger.exception("Failed to send Telegram message: %s", exc)
            return False

    async def close(self) -> None:
        """Clean up underlying HTTP resources."""
        await self._client.aclose()