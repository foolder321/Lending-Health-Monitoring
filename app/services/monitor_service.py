"""Monitoring service."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MonitorService:
    def __init__(
        self,
        addresses,
        adapter,
        risk_engine,
        alert_service,
        telegram_service,
        session_factory,
        position_cache,
    ) -> None:
        self.addresses = addresses or []
        self.adapter = adapter
        self.risk_engine = risk_engine
        self.alert_service = alert_service
        self.telegram_service = telegram_service
        self.session_factory = session_factory
        self.position_cache = position_cache

    async def _get_addresses_to_monitor(self) -> list[str]:
        async with self.session_factory() as session:
            await self.alert_service.repository.ensure_schema(session)
            db_addresses = await self.alert_service.repository.get_all_active_wallets(session)

        addresses = list({addr.lower() for addr in (db_addresses or []) if addr})
        if not addresses:
            addresses = list({addr.lower() for addr in self.addresses if addr})
        return addresses

    async def poll_addresses(self) -> None:
        addresses = await self._get_addresses_to_monitor()

        for address in addresses:
            try:
                position = await self.adapter.get_position(address)
                if position is None:
                    continue

                self.position_cache[address] = position
                hf = getattr(position, "health_factor", None)

                async with self.session_factory() as session:
                    await self.alert_service.repository.ensure_schema(session)
                    decision = await self.alert_service.evaluate(session, address, hf)

                if not decision.should_alert:
                    logger.info(
                        "No alert for %s. Bucket: %s",
                        address,
                        decision.current_bucket,
                    )
                    continue

                collateral = getattr(position, "collateral_value_usd", None)
                debt = getattr(position, "debt_value_usd", None)
                network = getattr(position, "network", "Unknown")
                protocol = getattr(position, "protocol", "Unknown")
                liquidation_distance_pct = getattr(position, "liquidation_distance_pct", None)
                risk_status = getattr(position, "risk_status", "UNKNOWN")

                hf_text = f"{hf:.4f}" if hf is not None else "n/a"
                collateral_text = f"${collateral:,.2f}" if collateral is not None else "n/a"
                debt_text = f"${debt:,.2f}" if debt is not None else "n/a"
                ld_text = (
                    f"{liquidation_distance_pct:.2f}%"
                    if liquidation_distance_pct is not None
                    else "n/a"
                )

                if decision.is_critical_repeat:
                    message = (
                        f"☠️ <b>{protocol.upper()} CRITICAL HF ALERT</b>\n\n"
                        f"⛓ <b>Chain:</b> {network}\n"
                        f"🏦 <b>Address:</b> <code>{address}</code>\n"
                        f"❤️ <b>HF:</b> {hf_text}\n"
                        f"📍 <b>Below:</b> 1.1\n"
                        f"🛡 <b>Collateral:</b> {collateral_text}\n"
                        f"💸 <b>Debt:</b> {debt_text}\n"
                        f"⚠️ <b>Liquidation Distance:</b> {ld_text}\n"
                        f"📌 <b>Status:</b> {risk_status}\n\n"
                        f"Бот будет напоминать каждые 10 минут, пока позиция не выйдет из критической зоны."
                    )
                else:
                    direction_emoji = "✅" if decision.direction == "up" else "🚨"
                    direction_text = "HF LEVEL UP" if decision.direction == "up" else "HF LEVEL DOWN"
                    crossed_text = "Crossed above" if decision.direction == "up" else "Crossed below"

                    message = (
                        f"{direction_emoji} <b>{protocol.upper()} {direction_text}</b>\n\n"
                        f"⛓ <b>Chain:</b> {network}\n"
                        f"🏦 <b>Address:</b> <code>{address}</code>\n"
                        f"❤️ <b>HF:</b> {hf_text}\n"
                        f"📍 <b>{crossed_text}:</b> {decision.crossed_level}\n"
                        f"🛡 <b>Collateral:</b> {collateral_text}\n"
                        f"💸 <b>Debt:</b> {debt_text}\n"
                        f"⚠️ <b>Liquidation Distance:</b> {ld_text}\n"
                        f"📌 <b>Status:</b> {risk_status}"
                    )

                await self.telegram_service.send_message(message)
                logger.info(
                    "Alert sent for %s: %s -> %s",
                    address,
                    decision.previous_bucket,
                    decision.current_bucket,
                )

            except Exception as exc:
                logger.exception("Failed to monitor address %s: %s", address, exc)
