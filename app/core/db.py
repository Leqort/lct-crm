"""Async engine, session factory and the FastAPI session dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.audit import register_audit_listeners
from app.core.config import settings

# Importing the models package registers every table on the shared MetaData and
# is also what makes the audit listeners meaningful.
import app.models  # noqa: F401  isort:skip

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    future=True,
)

SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

register_audit_listeners()


async def get_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped session.

    Transaction boundaries belong to the service layer: services commit when a
    unit of work is complete. This dependency only guarantees that a failed
    request never leaves a half-open transaction behind.
    """
    session = SessionFactory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    await engine.dispose()
