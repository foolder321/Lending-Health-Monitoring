"""
Repository classes encapsulating database access patterns.

Repositories serve as an abstraction layer between the rest of the
application and the underlying ORM. This makes it easier to test the
business logic without depending directly on SQLAlchemy queries and
offers a central place to optimise queries or swap out the persistence
layer in the future.
"""

import datetime
from typing import Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import Alert


class AlertRepository:
    """Repository for managing alert history in the database."""

    async def log_alert(
        self, session: AsyncSession, address: str, risk_level: str, message: str
    ) -> None:
        """Persist a new alert to the database.

        Parameters
        ----------
        session: AsyncSession
            The SQLAlchemy session within which to execute the insert.
        address: str
            The wallet address the alert concerns.
        risk_level: str
            The severity of the alert (e.g. WARNING, CRITICAL, LIQUIDATION_RISK).
        message: str
            The human‑readable message sent to the user.
        """
        alert = Alert(
            address=address,
            risk_level=risk_level,
            timestamp=datetime.datetime.utcnow(),
            message=message,
        )
        session.add(alert)
        await session.commit()

    async def get_last_alert(self, session: AsyncSession, address: str) -> Optional[Alert]:
        """Retrieve the most recent alert for a given address, if any."""
        result = await session.execute(
            select(Alert).where(Alert.address == address).order_by(desc(Alert.timestamp)).limit(1)
        )
        alert_row = result.scalar_one_or_none()
        return alert_row