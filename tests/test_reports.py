"""Reports use the same filters and visibility rules as interaction lists."""

from __future__ import annotations

import json

from app.schemas.interaction import InteractionCreate
from app.schemas.university import UniversityCreate
from app.services.catalogs import UniversityService
from app.services.interactions import InteractionService
from tests.conftest import auth


async def test_report_exports_selected_columns_in_all_formats(session, client, manager_user, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="МГТУ"))
    await InteractionService(session, scope).create(InteractionCreate(university_id=university.id))

    for format_ in ("xls", "xlsx", "pdf", "json"):
        response = await client.post(
            "/api/v1/reports/export",
            json={"format": format_, "columns": ["university", "workflow_stage"]},
            headers=auth(manager_user),
        )
        assert response.status_code == 200
        assert f"interactions-report.{format_}" in response.headers["content-disposition"]
        assert response.content

    json_response = await client.post(
        "/api/v1/reports/export",
        json={"format": "json", "columns": ["university"]},
        headers=auth(manager_user),
    )
    assert json.loads(json_response.content) == [{"university": "МГТУ"}]


async def test_statistics_exports_png_and_pdf(session, client, manager_user, scope):
    university = await UniversityService(session, scope).create(UniversityCreate(name="МГТУ"))
    await InteractionService(session, scope).create(InteractionCreate(university_id=university.id))

    png = await client.post(
        "/api/v1/reports/statistics",
        json={"format": "png", "group_by": "university"},
        headers=auth(manager_user),
    )
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG")

    pdf = await client.post(
        "/api/v1/reports/statistics",
        json={"format": "pdf", "group_by": "university"},
        headers=auth(manager_user),
    )
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
