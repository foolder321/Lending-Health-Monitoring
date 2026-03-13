"""
Application entry point.

This module wires together all components of the monitoring system and
starts the scheduler. It reads configuration from environment
variables, initialises logging, database connections and protocol
adapters, and schedules periodic polling tasks. When the script is
invoked directly (e.g. ``python app/main.py``), it runs the async
``run`` function which blocks until interrupted.
"""

import asyncio
import logging
import signal
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import AppSettings
from app.core.logging import init_logging
from app.adapters.aave_v3_arbitrum import AaveV3ArbitrumAdapter
from app.services.risk_engine import RiskEngine
from app.services.alert_service import AlertService
from app.services.telegram_service import TelegramService
from app.services.monitor_service import MonitorService
from app.storage.db import create_engine_and_session
from app.storage.repository import AlertRepository
from app.storage.models import Base


async def _create_tables(engine) -> None:
    """Create database tables if they don't exist."""
logger = logging.getLogger(__name__)


async def _create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def run() -> None:
    """Asynchronous entry point for the monitoring application."""
    settings = AppSettings()  # Load configuration from env / .env
    init_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("Starting monitoring application")
    # Create DB engine and session factory
    engine, session_factory = create_engine_and_session(settings)
    await _create_tables(engine)

    # Instantiate components
def _format_asset_lines(assets) -> str:
    if not assets:
        return "• -"

    lines = []
    for asset in assets:
        symbol = getattr(asset, "token_symbol", None) or getattr(asset, "token_address", "")[:6]
        amount = getattr(asset, "amount", 0.0)
        usd_value = getattr(asset, "usd_value", 0.0)
        lines.append(f"• {symbol} — {amount:,.6f} (${usd_value:,.2f})")
    return "\n".join(lines)

async def build_status_message(address: str, adapter) -> str:
    """Fetch current position and build a Telegram-friendly status message."""
    try:
        position = await adapter.get_position(address)

        if position is None:
            return f"Адрес <code>{address}</code>\n\nПозиция не найдена."

        hf = getattr(position, "health_factor", None)
        collateral = getattr(position, "collateral_value_usd", None)
        debt = getattr(position, "debt_value_usd", None)
        ltv = getattr(position, "ltv", None)
        network = getattr(position, "network", "Unknown")
        protocol = getattr(position, "protocol", "Unknown")
        supplied = getattr(position, "supplied", [])
        borrowed = getattr(position, "borrowed", [])

        liquidation_distance_pct = getattr(position, "liquidation_distance_pct", None)
        estimated_liquidation_price = getattr(position, "estimated_liquidation_price", None)
        position_risk_status = getattr(position, "risk_status", None)

        nft_id = "-"

        if position_risk_status:
            risk_status = position_risk_status
        elif hf is None:
            risk_status = "UNKNOWN"
        elif hf > 1.5:
            risk_status = "SAFE"
        elif hf > 1.3:
            risk_status = "OK"
        elif hf > 1.2:
            risk_status = "WARNING"
        elif hf >= 1.0:
            risk_status = "DANGER"
        else:
            risk_status = "LIQUIDATION"

        hf_text = f"{hf:.4f}" if hf is not None else "n/a"
        collateral_text = f"${collateral:,.2f}" if collateral is not None else "n/a"
        debt_text = f"${debt:,.2f}" if debt is not None else "n/a"
        ltv_text = f"{ltv:.2f}%" if ltv is not None else "n/a"

        liquidation_distance_text = (
            f"{liquidation_distance_pct:.2f}%"
            if liquidation_distance_pct is not None
            else "n/a"
        )

        liquidation_price_text = (
            f"${estimated_liquidation_price:,.2f}"
            if estimated_liquidation_price is not None
            else "-"
        )

        net_worth = None
        if collateral is not None and debt is not None:
            net_worth = collateral - debt
        net_worth_text = f"${net_worth:,.2f}" if net_worth is not None else "n/a"

        supplied_text = _format_asset_lines(supplied)
        borrowed_text = _format_asset_lines(borrowed)

        return (
            f"📊 <b>{protocol.upper()} POSITION</b>\n"
            f"⛓ <b>Chain:</b> {network}\n"
            f"🏦 <b>Address:</b> <code>{address}</code>\n"
            f"🧩 <b>NFT id:</b> {nft_id}\n\n"
            f"❤️ <b>Health Factor:</b> {hf_text}\n"
            f"🛡 <b>Collateral Total:</b> {collateral_text}\n"
            f"💸 <b>Borrowed Total:</b> {debt_text}\n"
            f"📉 <b>LTV:</b> {ltv_text}\n"
            f"⚠️ <b>Liquidation Distance:</b> {liquidation_distance_text}\n"
            f"☠️ <b>Liquidation Price:</b> {liquidation_price_text}\n"
            f"💰 <b>Net Worth:</b> {net_worth_text}\n\n"
            f"🛡 <b>Collateral Assets:</b>\n{supplied_text}\n\n"
            f"💸 <b>Borrowed Assets:</b>\n{borrowed_text}\n\n"
            f"📍 <b>Status:</b> {risk_status}"
        )
    except Exception as exc:
        logger.exception("Failed to build status message: %s", exc)
        return f"Не удалось получить текущую позицию для <code>{address}</code>."

async def telegram_command_loop(
    settings: AppSettings,
    telegram_service: TelegramService,
    adapter,
) -> None:
    """Listen for Telegram commands."""
    logger.info("Starting Telegram command loop")
    primary_address = settings.addresses[0] if settings.addresses else None

    while True:
        updates = await telegram_service.get_updates()

        for update in updates:
            message = update.get("message", {})
            chat = message.get("chat", {})
            text = message.get("text", "")

            if str(chat.get("id")) != str(settings.telegram_chat_id):
                continue

            if text == "/start":
                reply = (
                    "Привет 👋\n\n"
                    "Я бот мониторинга lending-позиций.\n\n"
                    "Команды:\n"
                    "/start — показать это сообщение\n"
                    "/status — показать текущее здоровье позиции"
                )
                await telegram_service.send_message(reply)

            elif text == "/status":
                if not primary_address:
                    await telegram_service.send_message("В конфиге не указан ни один адрес.")
                    continue

                status_message = await build_status_message(primary_address, adapter)
                await telegram_service.send_message(status_message)

        await asyncio.sleep(2)


async def run() -> None:
    settings = AppSettings()
    init_logging(settings.log_level)
    logger.info("Starting monitoring application")

    engine, session_factory = create_engine_and_session(settings)
    await _create_tables(engine)

    adapter = AaveV3ArbitrumAdapter(settings)
    risk_engine = RiskEngine(
        warning_threshold=1.20,
        critical_threshold=1.10,
        liquidation_threshold=1.00,
    )
    repository = AlertRepository()
    alert_service = AlertService(repository, repeat_minutes=settings.alert_repeat_minutes)
    telegram_service = TelegramService(settings)

    monitor_service = MonitorService(
        addresses=settings.addresses,
        adapter=adapter,
        risk_engine=risk_engine,
        alert_service=alert_service,
        telegram_service=telegram_service,
        session_factory=session_factory,
    )

    # Setup APScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        monitor_service.poll_addresses,
        trigger=IntervalTrigger(seconds=settings.poll_interval_seconds),
        name="monitor_addresses",
        coalesce=True,
    )
    scheduler.start()

    # Graceful shutdown handling
    telegram_task = asyncio.create_task(
        telegram_command_loop(settings, telegram_service, adapter)
    )

    stop_event = asyncio.Event()

    def _handle_signal(*_: int) -> None:
        logger.info("Received shutdown signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    # Wait until signal
    await stop_event.wait()
    logger.info("Shutting down…")
    await stop_event.wait()
    logger.info("Shutting down...")

    telegram_task.cancel()
    scheduler.shutdown(wait=False)
    await adapter.close()
    await telegram_service.close()
    await engine.dispose()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:
        logging.getLogger(__name__).exception("Application exited with error: %s", exc)
        logging.getLogger(__name__).exception("Application exited with error: %s", exc)
