"""Telegram messaging service."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def build_main_keyboard() -> dict:
    return {
        "keyboard": [
            ["Показать текущую позицию", "Прогноз риска залога"],
            ["➕ Добавить кошелек"],
            ["➖ Удалить кошелек"],
            ["👛 Мои кошельки"],
        ],
        "resize_keyboard": True,
        "persistent": True,
    }


def build_hide_keyboard() -> dict:
    return {
        "remove_keyboard": True,
    }


class TelegramService:
    def __init__(self, settings) -> None:
        self.bot_token = settings.telegram_bot_token
        self.chat_id = str(settings.telegram_chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._client = httpx.AsyncClient(timeout=120.0)
        self._offset: int | None = None

    async def _post(self, method: str, payload: dict) -> dict:
        url = f"{self.base_url}/{method}"
        response = await self._client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error on {method}: {data}")
        return data

    async def send_message(
        self,
        text: str,
        reply_markup: dict | None = None,
        use_main_keyboard: bool = True,
    ) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        elif use_main_keyboard:
            payload["reply_markup"] = build_main_keyboard()

        await self._post("sendMessage", payload)

    async def get_updates(self) -> list[dict]:
        payload = {
            "timeout": 25,
            "allowed_updates": ["message"],
        }
        if self._offset is not None:
            payload["offset"] = self._offset

        data = await self._post("getUpdates", payload)
        updates = data.get("result", [])

        if updates:
            self._offset = updates[-1]["update_id"] + 1

        return updates

    async def close(self) -> None:
        await self._client.aclose()
