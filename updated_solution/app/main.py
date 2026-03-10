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