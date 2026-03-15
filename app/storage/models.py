"""
Database models.

Defines SQLAlchemy ORM models used by the application.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base


Base = declarative_base()


class Alert(Base):
    """Persistence model representing an alert that was sent to the user."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    address = Column(String, index=True, nullable=False)
    risk_level = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    message = Column(String, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Alert address={self.address} risk={self.risk_level} time={self.timestamp}>"


class TelegramUser(Base):
    """Telegram user registered in the bot."""

    __tablename__ = "telegram_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, unique=True, index=True, nullable=False)
    plan = Column(String, default="free", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TelegramUser chat_id={self.chat_id} plan={self.plan}>"


class UserWallet(Base):
    """Wallets attached to Telegram users."""

    __tablename__ = "user_wallets"
    __table_args__ = (
        UniqueConstraint("chat_id", "address", name="uq_user_wallet_chat_address"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String, index=True, nullable=False)
    address = Column(String, index=True, nullable=False)
    label = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserWallet chat_id={self.chat_id} address={self.address}>"
