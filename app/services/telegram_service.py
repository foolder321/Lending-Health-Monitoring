"""
Telegram notification and command service.
"""

import logging
from typing import Optional

import httpx

from app.core.config import AppSettings

logger = logging.getLogger(__name__)


class TelegramService:
    """Service responsible for sending and receiving Telegram messages."""

    def __init__(self, settings: AppSettings) -> None:
        self.token = settings.telegram_bot_token
        self.chat_id = str(settings.telegram_chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        timeout = httpx.Timeout(10.0, connect=5.0)
        self._client = httpx.AsyncClient(timeout=timeout)
        self._last_update_id: Optional[int] = None

    async def send_message(self, text: str) -> bool:
        """Send a text message to the configured chat."""
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

    async def get_updates(self) -> list[dict]:
        """Fetch new updates from Telegram."""
        url = f"{self.base_url}/getUpdates"
        params = {
            "timeout": 2,
        }
        if self._last_update_id is not None:
            params["offset"] = self._last_update_id + 1

        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.warning("Telegram getUpdates error: %s", data)
                return []

            results = data.get("result", [])
            if results:
                self._last_update_id = results[-1]["update_id"]
            return results
        except Exception as exc:
            logger.exception("Failed to fetch Telegram updates: %s", exc)
            return []

    async def close(self) -> None:
        """Clean up underlying HTTP resources."""
        await self._client.aclose()
