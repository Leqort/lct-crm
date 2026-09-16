"""Assembly of every /api/v1 router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    audit,
    catalogs,
    imports,
    interactions,
    reports,
    route,
    universities,
    users,
    workflows,
)

api_router = APIRouter()
api_router.include_router(universities.router)
api_router.include_router(universities.contacts_router)
api_router.include_router(universities.assignments_router)
api_router.include_router(catalogs.vendors_router)
api_router.include_router(catalogs.directions_router)
api_router.include_router(catalogs.products_router)
api_router.include_router(interactions.router)
api_router.include_router(reports.router)
api_router.include_router(route.router)
api_router.include_router(route.attachments_router)
api_router.include_router(workflows.router)
api_router.include_router(workflows.stages_router)
api_router.include_router(workflows.transitions_router)
api_router.include_router(users.router)
api_router.include_router(imports.router)
api_router.include_router(audit.router)

__all__ = ["api_router"]
