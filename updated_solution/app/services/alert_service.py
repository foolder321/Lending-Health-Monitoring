"""
Alert deduplication and history tracking service.

This service uses the underlying repository to determine whether a new
alert should be sent based on past alerts for the same address and
risk level. It enforces a minimum repeat interval so that the user
does not receive redundant notifications when the risk state has not
changed significantly. All sent alerts are recorded in the database for
historical reference.
"""

import datetime
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.risk_engine import RiskLevel
from app.storage.repository import AlertRepository


logger = logging.getLogger(__name__)


class AlertService:
    """Manage alert deduplication and persistence."""

    def __init__(self, repository: AlertRepository, repeat_minutes: int = 30) -> None:
        self.repository = repository
        self.repeat_interval = datetime.timedelta(minutes=repeat_minutes)

    async def should_send(
        self, session: AsyncSession, address: str, risk_level: RiskLevel
    ) -> bool:
        """Determine whether a new alert should be sent.

        The rules are as follows:

        - If no previous alert exists for the address, always send.
        - If the risk level has changed (including transitions to ``NONE``), send.
        - If sufficient time has elapsed since the last alert of the same level,
          send again to remind the user.
        - Otherwise, suppress to prevent spamming.

        Note that a ``NONE`` level serves as a recovery indicator. This
        method will allow a recovery message to be sent when the last
        recorded risk level was not ``NONE``.
        """
        last_alert = await self.repository.get_last_alert(session, address)
        if last_alert is None:
            return True
        # Always allow sending if the level differs from the last one (including NONE)
        if last_alert.risk_level != risk_level.value:
            return True
        now = datetime.datetime.utcnow()
        if now - last_alert.timestamp >= self.repeat_interval:
            return True
        return False

    async def record_alert(
        self, session: AsyncSession, address: str, risk_level: RiskLevel, message: str
    ) -> None:
        """Persist a new alert to the database."""
        await self.repository.log_alert(session, address, risk_level.value, message)