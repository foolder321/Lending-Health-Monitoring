"""Repository helpers for alert state persistence and user wallets."""

from __future__ import annotations

from sqlalchemy import text


class AlertRepository:
    """Persistence adapter for alert state and user wallets."""

    async def ensure_schema(self, session) -> None:
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS alert_state (
                    address TEXT PRIMARY KEY,
                    health_factor REAL,
                    hf_bucket TEXT,
                    last_alert_sent_at TEXT
                )
                """
            )
        )

        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS telegram_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL UNIQUE,
                    plan TEXT NOT NULL DEFAULT 'free',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_wallets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    address TEXT NOT NULL,
                    label TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, address)
                )
                """
            )
        )

        try:
            await session.execute(
                text("ALTER TABLE alert_state ADD COLUMN last_alert_sent_at TEXT")
            )
        except Exception:
            pass

        await session.commit()

    async def fetch_last_alert_state(self, session, address: str):
        result = await session.execute(
            text(
                """
                SELECT address, health_factor, hf_bucket, last_alert_sent_at
                FROM alert_state
                WHERE address = :address
                """
            ),
            {"address": address},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def upsert_alert_state(
        self,
        session,
        address: str,
        health_factor,
        hf_bucket: str,
        last_alert_sent_at=None,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO alert_state(address, health_factor, hf_bucket, last_alert_sent_at)
                VALUES (:address, :health_factor, :hf_bucket, :last_alert_sent_at)
                ON CONFLICT(address) DO UPDATE SET
                    health_factor = excluded.health_factor,
                    hf_bucket = excluded.hf_bucket,
                    last_alert_sent_at = excluded.last_alert_sent_at
                """
            ),
            {
                "address": address,
                "health_factor": health_factor,
                "hf_bucket": hf_bucket,
                "last_alert_sent_at": (
                    last_alert_sent_at.isoformat() if hasattr(last_alert_sent_at, "isoformat")
                    else last_alert_sent_at
                ),
            },
        )
        await session.commit()

    async def ensure_user(self, session, chat_id: str) -> None:
        await session.execute(
            text(
                """
                INSERT INTO telegram_users(chat_id, plan, is_active, created_at)
                VALUES (:chat_id, 'free', 1, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO NOTHING
                """
            ),
            {"chat_id": str(chat_id)},
        )
        await session.commit()

    async def add_wallet(self, session, chat_id: str, address: str, label: str | None = None) -> bool:
        await self.ensure_user(session, chat_id)

        existing = await session.execute(
            text(
                """
                SELECT id
                FROM user_wallets
                WHERE chat_id = :chat_id
                  AND address = :address
                """
            ),
            {"chat_id": str(chat_id), "address": address.lower()},
        )
        if existing.first():
            return False

        await session.execute(
            text(
                """
                INSERT INTO user_wallets(chat_id, address, label, is_active, created_at)
                VALUES (:chat_id, :address, :label, 1, CURRENT_TIMESTAMP)
                """
            ),
            {
                "chat_id": str(chat_id),
                "address": address.lower(),
                "label": label,
            },
        )
        await session.commit()
        return True

    async def remove_wallet(self, session, chat_id: str, address: str) -> bool:
        result = await session.execute(
            text(
                """
                DELETE FROM user_wallets
                WHERE chat_id = :chat_id
                  AND address = :address
                """
            ),
            {"chat_id": str(chat_id), "address": address.lower()},
        )
        await session.commit()
        return result.rowcount > 0

    async def get_wallets_by_chat_id(self, session, chat_id: str) -> list[dict]:
        result = await session.execute(
            text(
                """
                SELECT address, label, is_active, created_at
                FROM user_wallets
                WHERE chat_id = :chat_id
                  AND is_active = 1
                ORDER BY created_at ASC
                """
            ),
            {"chat_id": str(chat_id)},
        )
        rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def get_all_active_wallets(self, session) -> list[str]:
        result = await session.execute(
            text(
                """
                SELECT DISTINCT address
                FROM user_wallets
                WHERE is_active = 1
                ORDER BY address ASC
                """
            )
        )
        rows = result.fetchall()
        return [row[0] for row in rows]
