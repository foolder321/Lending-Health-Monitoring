"""
Database setup and session management.

This module initialises an asynchronous SQLAlchemy engine and provides
a session factory for use throughout the application. The database URL
is taken from the application settings.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import AppSettings


def create_engine_and_session(settings: AppSettings) -> tuple:
    """Create an asynchronous SQLAlchemy engine and session factory.

    Parameters
    ----------
    settings: AppSettings
        Application configuration containing the database URL.

    Returns
    -------
    tuple
        A tuple ``(engine, session_factory)`` where ``engine`` is the
        ``AsyncEngine`` instance and ``session_factory`` is an
        ``async_sessionmaker`` producing ``AsyncSession`` objects.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, session_factory


async def get_session(session_factory: async_sessionmaker) -> AsyncGenerator[AsyncSession, None]:
    """Provide an async context manager yielding an ``AsyncSession``.

    This helper yields a new session and ensures it is closed when
    leaving the context. It can be used in ``async with`` blocks.
    """
    async with session_factory() as session:
        yield session