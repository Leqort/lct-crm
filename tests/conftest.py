"""Test fixtures.

A real PostgreSQL database is mandatory: the schema relies on partial unique
indexes, `jsonb`, native enums, trigram search and a GiST exclusion constraint,
none of which SQLite can emulate. The target database is created and migrated
once per session and truncated between tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

# Must happen before anything imports app.core.config — the Settings object is
# built once at import time.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("POSTGRES_DB", "crm_test")
os.environ.setdefault("POSTGRES_USER", os.environ.get("TEST_POSTGRES_USER", "crm"))
os.environ.setdefault("POSTGRES_PASSWORD", os.environ.get("TEST_POSTGRES_PASSWORD", "crm"))
os.environ.setdefault("POSTGRES_HOST", os.environ.get("TEST_POSTGRES_HOST", "localhost"))
os.environ.setdefault("POSTGRES_PORT", os.environ.get("TEST_POSTGRES_PORT", "5432"))
os.environ.setdefault("ENV", "local")
os.environ.setdefault("AUTH_MODE", "dev")

import pytest  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.access import AccessScope  # noqa: E402
from app.core.audit import AuditContext, audit_context  # noqa: E402
from app.core.db import SessionFactory, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.schemas.user import UserCreate  # noqa: E402
from app.services.users import UserService  # noqa: E402

# Tables Alembic owns; never truncated between tests.
_SKIP_TABLES = {"alembic_version"}


@pytest.fixture(scope="session", autouse=True)
def migrate_database() -> None:
    """Bring the test database to `head` exactly once per test session."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env={**os.environ},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(autouse=True)
async def clean_tables(migrate_database: None) -> AsyncIterator[None]:
    """Start every test from an empty database."""
    tables = [t.name for t in Base.metadata.sorted_tables if t.name not in _SKIP_TABLES]
    statement = sa.text(
        "TRUNCATE TABLE " + ", ".join(f'"{name}"' for name in tables) + " RESTART IDENTITY CASCADE"
    )
    async with engine.begin() as connection:
        await connection.execute(statement)
    yield


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as db_session:
        yield db_session


@pytest.fixture
def scope() -> AccessScope:
    return AccessScope.system()


@pytest.fixture(autouse=True)
def default_audit_context() -> AsyncIterator[None]:
    """Give service-level tests a request context, as middleware would."""
    with audit_context(AuditContext(request_id=uuid.uuid4())):
        yield


async def _make_user(session: AsyncSession, keycloak_id: str, name: str, role: UserRole):
    return await UserService(session, AccessScope.system()).create(
        UserCreate(
            keycloak_id=keycloak_id,
            full_name=name,
            email=f"{keycloak_id}@example.test",
            role=role,
        )
    )


@pytest.fixture
async def admin_user(session: AsyncSession):
    return await _make_user(session, "admin-1", "Админов Админ Админович", UserRole.ADMIN)


@pytest.fixture
async def manager_user(session: AsyncSession):
    return await _make_user(session, "manager-1", "Менеджеров Пётр Иванович", UserRole.MANAGER)


@pytest.fixture
async def kam_user(session: AsyncSession):
    return await _make_user(session, "kam-1", "Камов Кирилл Андреевич", UserRole.USER)


@pytest.fixture
async def other_kam_user(session: AsyncSession):
    return await _make_user(session, "kam-2", "Второй Кам Камович", UserRole.USER)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client that opens its **own** database session.

    Deliberately not the test's session. Sharing one lets the identity map
    satisfy relationship loads that a real request has to fetch from the
    database, which hides "unloaded relationship" bugs entirely — the endpoint
    passes in tests and returns 500 in production. Fixture data is visible here
    because services commit.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


def auth(user) -> dict[str, str]:
    """Headers identifying `user` to the dev auth back-end."""
    return {"X-Debug-User": str(user.id)}
