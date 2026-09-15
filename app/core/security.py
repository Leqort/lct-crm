"""Authentication abstraction (SPEC §8).

Two interchangeable back-ends selected by `AUTH_MODE`:

* `dev`      — the identity comes from the `X-Debug-User` header. Refused
               outright when `ENV=production` (the app will not even start,
               see `app.core.config`).
* `keycloak` — the identity comes from a Bearer JWT verified against the
               realm's JWKS. Keycloak itself is wired up by another team; this
               module already speaks the protocol so their work is limited to
               configuration.

Whatever the back-end, the rest of the application only ever sees `CurrentUser`.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

import httpx
from fastapi import Depends, Request
from jose import jwt
from jose.exceptions import JWTError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import AccessDeniedError
from app.models.enums import UserRole
from app.models.user import User
from app.services.text import normalize_name

logger = logging.getLogger(__name__)

Role = Literal["user", "manager", "admin"]

# Keycloak realm roles we care about, most privileged first.
_ROLE_PRIORITY: tuple[UserRole, ...] = (UserRole.ADMIN, UserRole.MANAGER, UserRole.USER)


class CurrentUser(BaseModel):
    id: uuid.UUID
    keycloak_id: str
    full_name: str
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_privileged(self) -> bool:
        """`manager` and `admin` see every university (SPEC §8)."""
        return self.role in ("manager", "admin")


class _JWKSCache:
    """Tiny TTL cache so every request does not hit Keycloak's JWKS endpoint."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._value: dict[str, Any] | None = None
        self._fetched_at: float = 0.0

    async def get(self, url: str) -> dict[str, Any]:
        now = time.monotonic()
        if self._value is not None and now - self._fetched_at < self._ttl:
            return self._value
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            self._value = response.json()
            self._fetched_at = now
        return self._value


_jwks_cache = _JWKSCache()


def _role_from_claims(claims: dict[str, Any]) -> UserRole:
    """Pick the strongest known realm role; default to the least privileged."""
    realm_roles = set(claims.get("realm_access", {}).get("roles", []))
    for role in _ROLE_PRIORITY:
        if role.value in realm_roles:
            return role
    return UserRole.USER


async def _decode_keycloak_token(token: str) -> dict[str, Any]:
    if not settings.keycloak_jwks_url:
        raise AccessDeniedError("Проверка токена невозможна: не настроен KEYCLOAK_JWKS_URL")
    try:
        jwks = await _jwks_cache.get(settings.keycloak_jwks_url)
        claims: dict[str, Any] = jwt.decode(
            token,
            jwks,
            audience=settings.keycloak_audience,
            issuer=settings.keycloak_issuer,
            options={"verify_aud": settings.keycloak_audience is not None},
        )
    except (JWTError, httpx.HTTPError) as exc:
        logger.warning("token verification failed: %s", exc)
        raise AccessDeniedError("Токен доступа недействителен") from None
    return claims


async def _sync_user_projection(
    session: AsyncSession, *, keycloak_id: str, full_name: str, email: str | None, role: UserRole
) -> User:
    """Keep the local `users` row in step with the token (SPEC §4.2).

    Keycloak is the master; this projection exists so foreign keys and full-name
    matching work without calling the IdP on every query.
    """
    user = await session.scalar(select(User).where(User.keycloak_id == keycloak_id))
    if user is None:
        user = User(
            keycloak_id=keycloak_id,
            full_name=full_name,
            full_name_normalized=normalize_name(full_name),
            email=email,
            role=role,
        )
        session.add(user)
        await session.flush()
        await session.commit()
        return user

    changed = False
    if full_name and user.full_name != full_name:
        user.full_name = full_name
        user.full_name_normalized = normalize_name(full_name)
        changed = True
    if email and user.email != email:
        user.email = email
        changed = True
    if user.role != role:
        user.role = role
        changed = True
    if changed:
        await session.commit()
    return user


async def _dev_user(request: Request, session: AsyncSession) -> CurrentUser:
    """Resolve `X-Debug-User` to a real row in `users`.

    Accepted values, tried in order: user UUID, `keycloak_id`, email. Full names
    are deliberately not accepted: HTTP header values are ASCII-only, so a
    Cyrillic ФИО cannot survive the trip. The header must reference an existing,
    active user — the stub hands out no identities of its own.
    """
    raw = request.headers.get("X-Debug-User")
    if not raw:
        raise AccessDeniedError(
            "Не передан заголовок X-Debug-User (dev-режим авторизации)",
            details={"header": "X-Debug-User"},
        )
    raw = raw.strip()

    user: User | None = None
    try:
        user_id = uuid.UUID(raw)
    except ValueError:
        user_id = None

    if user_id is not None:
        user = await session.scalar(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
    if user is None:
        user = await session.scalar(
            select(User).where(User.keycloak_id == raw, User.deleted_at.is_(None))
        )
    if user is None:
        user = await session.scalar(
            select(User).where(User.email == raw, User.deleted_at.is_(None))
        )
    if user is None or not user.is_active:
        raise AccessDeniedError(
            "Пользователь из заголовка X-Debug-User не найден или неактивен",
            details={"x_debug_user": raw},
        )
    return CurrentUser(
        id=user.id,
        keycloak_id=user.keycloak_id,
        full_name=user.full_name,
        role=user.role.value,  # type: ignore[arg-type]
    )


async def _keycloak_user(request: Request, session: AsyncSession) -> CurrentUser:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AccessDeniedError("Требуется Bearer-токен в заголовке Authorization")

    claims = await _decode_keycloak_token(token)
    keycloak_id = claims.get("sub")
    if not keycloak_id:
        raise AccessDeniedError("В токене отсутствует обязательный claim `sub`")

    full_name = (
        claims.get("name")
        or " ".join(filter(None, [claims.get("family_name"), claims.get("given_name")]))
        or claims.get("preferred_username")
        or keycloak_id
    )
    user = await _sync_user_projection(
        session,
        keycloak_id=keycloak_id,
        full_name=full_name,
        email=claims.get("email"),
        role=_role_from_claims(claims),
    )
    if not user.is_active:
        raise AccessDeniedError("Учётная запись деактивирована")
    return CurrentUser(
        id=user.id,
        keycloak_id=user.keycloak_id,
        full_name=user.full_name,
        role=user.role.value,  # type: ignore[arg-type]
    )


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """The one entry point every protected endpoint depends on."""
    if settings.auth_mode == "keycloak":
        user = await _keycloak_user(request, session)
    else:
        user = await _dev_user(request, session)

    # Make the actor available to the audit machinery for the rest of the
    # request without threading it through every call.
    from app.core.audit import ensure_audit_context

    ctx = ensure_audit_context()
    ctx.actor_id = user.id
    ctx.actor_name = user.full_name
    return user


def require_roles(*roles: Role) -> Callable[..., Awaitable[CurrentUser]]:
    """Dependency factory guarding an endpoint by role."""
    allowed: Sequence[Role] = roles

    async def _dependency(
        user: CurrentUser = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> CurrentUser:
        if user.role not in allowed:
            from app.services.audit import log_access_denied

            await log_access_denied(
                session,
                entity_type="endpoint",
                reason="insufficient_role",
                required=list(allowed),
                actual=user.role,
            )
            raise AccessDeniedError(
                "Недостаточно прав для выполнения операции",
                details={"required_roles": list(allowed), "actual_role": user.role},
            )
        return user

    return _dependency


require_manager = require_roles("manager", "admin")
require_admin = require_roles("admin")
