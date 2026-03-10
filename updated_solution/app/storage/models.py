"""
Database models.

Defines SQLAlchemy ORM models used by the application. Currently the
database contains only a single table for alert history, but the
schema can be extended in the future to store additional state such
as the latest position snapshot per address.
"""

import datetime
from sqlalchemy import Column, Integer, String, DateTime
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