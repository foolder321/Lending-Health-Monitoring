"""
Application configuration module.

This module defines a Pydantic ``BaseSettings`` class which loads
configuration values from environment variables and, optionally, from
a ``.env`` file located in the project root. See ``.env.example`` in
the repository for a list of required variables.

The configuration object centralises all tunable parameters such as
Telegram credentials, polling intervals, database connection strings
and API keys. Having a single source of truth makes it easy to
override settings via environment variables when deploying the
application in different environments (development, staging,
production).
"""

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables or ``.env``.

    Fields correspond to the configuration options used throughout the
    application. Defaults are provided for most settings to simplify
    local development; however, API keys and Telegram credentials must
    always be supplied explicitly.
    """

    telegram_bot_token: str
    """Telegram bot token issued by BotFather. This token is required
    to send messages via the Telegram API."""

    telegram_chat_id: str
    """Identifier of the chat (user or group) where alerts should be sent.
    This should be supplied as a string to avoid accidental integer
    conversions in configuration parsing."""

    poll_interval_seconds: int = 300
    """Frequency, in seconds, at which the monitor service polls the
    lending protocol adapter for updated position data. Defaults to
    five minutes."""

    alert_repeat_minutes: int = 30
    """Minimum interval, in minutes, between repeated alerts of the same
    severity for a given address. This throttles identical messages
    to avoid spamming the user when the state hasn't changed."""

    addresses: List[str]
    """List of Ethereum/Arbitrum wallet addresses to monitor. Addresses
    should be provided as a comma-separated list in the ``ADDRESSES``
    environment variable."""

    database_url: str = "sqlite+aiosqlite:///data/alerts.db"
    """SQLAlchemy database URL. By default this points at a SQLite
    database under the ``data`` directory. When deploying to
    production you may wish to use PostgreSQL or another database.
    The ``+aiosqlite`` driver enables asynchronous access."""

    log_level: str = "INFO"
    """Logging level to initialise the root logger with. Accepts
    values such as ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR`` and
    ``CRITICAL``."""

    expand_network_api_key: str
    """API key for Expand.network. This key grants access to the
    lending protocol data endpoints used by the Aave V3 adapter. You
    can obtain a key by registering on the Expand.network website.
    """

    expand_network_base_url: str = "https://api.expand.network"
    """Base URL for Expand.network's API. This can be overridden to
    point at a proxy or mock server during testing."""

    lendborrow_id: int = 1204  # Aave V3 on Arbitrum
    """Identifier for Aave V3 on Arbitrum as defined by Expand.network.
    This value is documented on the Expand.network Lending Protocol
    IDs page and used when calling the API endpoints. The default
    corresponds to Aave V3 on Arbitrum.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Optional: RPC provider URI for on‑chain calls. If supplied, the Aave
    # adapter will use web3.py to fetch aggregated account data directly
    # from the Aave Pool contract. When omitted the adapter falls back
    # solely to the Expand.network API for price data.
    web3_provider_uri: str | None = None