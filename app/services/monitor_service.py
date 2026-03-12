"""
Monitoring service orchestrating position fetches, risk assessment and alerting.

This component brings together the adapter, risk engine, alert service and
Telegram service. It periodically retrieves the position for each
configured wallet address, evaluates the risk and sends alerts when
necessary. The actual scheduling is handled externally (in ``main.py``)
via APScheduler or a simple loop.
"""

import asyncio
import logging
from typing import List, Callable

from app.core.types import AssetPosition

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import LendingProtocolAdapter
from app.core.types import Position
from app.services.risk_engine import RiskEngine, RiskLevel
from app.services.alert_service import AlertService
from app.services.telegram_service import TelegramService
from app.storage.repository import AlertRepository


logger = logging.getLogger(__name__)


class MonitorService:
    """Orchestrate polling of multiple addresses and alerting logic."""

    def __init__(
        self,
        addresses: List[str],
        adapter: LendingProtocolAdapter,
        risk_engine: RiskEngine,
        alert_service: AlertService,
        telegram_service: TelegramService,
        session_factory: async_sessionmaker,
    ) -> None:
        self.addresses = addresses
        self.adapter = adapter
        self.risk_engine = risk_engine
        self.alert_service = alert_service
        self.telegram_service = telegram_service
        self.session_factory = session_factory

    async def poll_addresses(self) -> None:
        """Poll each configured address and send alerts if required."""
        logger.info("Starting monitoring cycle for %d addresses", len(self.addresses))
        async with self.session_factory() as session:
            for address in self.addresses:
                try:
                    position: Position = await self.adapter.get_position(address)
                except Exception as exc:
                    logger.exception("Failed to fetch position for %s: %s", address, exc)
                    continue

                risk_level, recommendation = self.risk_engine.assess(position)
                # Always evaluate whether to send, even if the risk is NONE (recovery)
                try:
                    if await self.alert_service.should_send(session, address, risk_level):
                        # Determine a default recommendation for recovery when none provided
                        rec = recommendation
                        if risk_level == RiskLevel.NONE and not recommendation:
                            rec = "Позиция находится в безопасной зоне."
                        message = self._format_message(position, risk_level, rec)
                        sent = await self.telegram_service.send_message(message)
                        if sent:
                            await self.alert_service.record_alert(session, address, risk_level, message)
                            logger.info(
                                "Sent %s alert for %s", risk_level.value, address
                            )
                        else:
                            logger.error(
                                "Failed to send alert to Telegram for %s", address
                            )
                    else:
                        logger.debug(
                            "Duplicate alert suppressed for %s at level %s",
                            address,
                            risk_level.value,
                        )
                except Exception as exc:
                    logger.exception("Error during alert logic for %s: %s", address, exc)

    def _format_message(
        self, position: Position, risk_level: RiskLevel, recommendation: str | None
    ) -> str:
        """Format the alert message according to the specification.

        The function generates a human‑readable summary of the user's position
        along with risk metrics and a suggested action. For recovery
        notifications (``risk_level == NONE``) the ``risk`` line is
        replaced with ``SAFE`` and a default recommendation can be passed
        in by the caller.
        """
        # Format USD values with two decimal places
        collateral = f"${position.collateral_value_usd:,.2f}"
        debt = f"${position.debt_value_usd:,.2f}"
        # Health factor: use infinity symbol when there is no debt, else format
        if position.debt_value_usd <= 0:
            hf_display = "∞"
        else:
            hf_display = (
                f"{position.health_factor:.2f}" if position.health_factor is not None else "N/A"
            )
        ltv_display = f"{position.ltv * 100:.2f}%" if position.ltv is not None else "N/A"
        collateral_ratio_display = (
            f"{position.collateral_ratio:.2f}"
            if position.collateral_ratio is not None
            else "N/A"
        )
        # Prepare per‑asset breakdown strings, including token amount and USD value
        def format_assets(assets: List[AssetPosition]) -> str:
            lines: List[str] = []
            for asset in assets:
                # Show token symbol if available, otherwise truncated address
                if asset.token_symbol:
                    token_display = asset.token_symbol
                else:
                    addr = asset.token_address or "?"
                    token_display = addr[:6] + "…" + addr[-4:]
                amount_str = f"{asset.amount:,.4f}"
                usd_str = f"${asset.usd_value:,.2f}"
                lines.append(f"- {token_display}: {amount_str} ({usd_str})")
            return "\n".join(lines) if lines else "-"
        supplied_str = format_assets(position.supplied)
        borrowed_str = format_assets(position.borrowed)
        # Use a friendly label for NONE risk level
        risk_label = "SAFE" if risk_level == RiskLevel.NONE else risk_level.value
        # Normalise recommendation
        recommendation = recommendation or ""
        message = (
            f"[Aave V3 / Arbitrum]\n"
            f"Address: {position.wallet_address}\n"
            f"Collateral: {collateral}\n"
            f"Debt: {debt}\n"
            f"Health Factor: {hf_display}\n"
            f"LTV: {ltv_display}\n"
            f"Collateral Ratio: {collateral_ratio_display}\n"
            f"Risk: {risk_label}\n\n"
            f"Supplied:\n{supplied_str}\n\n"
            f"Borrowed:\n{borrowed_str}\n\n"
            f"Action:\n{recommendation}"
        )
        return message