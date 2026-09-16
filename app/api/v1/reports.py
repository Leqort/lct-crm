"""File reports and statistics exports (FR-02, FR-05, FR-09, SOL-04)."""

from __future__ import annotations

import asyncio
from collections import Counter

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.v1.deps import ScopeDep, SessionDep
from app.models.enums import AuditAction
from app.schemas.report import ChartFormat, ReportRequest, StatisticsRequest
from app.services.audit import log_event
from app.services.reporting import COLUMN_TITLES, ReportingService, render_chart, render_report

router = APIRouter(prefix="/reports", tags=["Reports and statistics"])

_REPORT_MEDIA_TYPES = {
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "json": "application/json",
}


@router.post(
    "/export",
    summary="Export interactions report",
    description="Generates an interactions report in XLS, XLSX, PDF, or JSON format.",
    response_class=Response,
)
async def export_report(data: ReportRequest, session: SessionDep, scope: ScopeDep) -> Response:
    rows = await ReportingService(session, scope).rows(data, data.columns)
    # Office/PDF rendering is CPU-bound. It runs outside the event loop so
    # concurrent report requests do not block route transitions or imports.
    content = await asyncio.to_thread(render_report, rows, data.columns, data.format)
    await log_event(
        session,
        action=AuditAction.EXPORT,
        entity_type="interaction_report",
        changes={"format": data.format, "columns": data.columns, "rows": len(rows)},
    )
    await session.commit()
    extension = data.format.value
    return Response(
        content=content,
        media_type=_REPORT_MEDIA_TYPES[extension],
        headers={"Content-Disposition": f'attachment; filename="interactions-report.{extension}"'},
    )


@router.post(
    "/statistics",
    summary="Export interaction statistics chart",
    description="Builds a grouped interaction statistics chart in PNG or PDF format.",
    response_class=Response,
)
async def export_statistics(
    data: StatisticsRequest, session: SessionDep, scope: ScopeDep
) -> Response:
    rows = await ReportingService(session, scope).rows(data, [data.group_by])
    groups = Counter(row[data.group_by.value] or "Not specified" for row in rows)
    content = await asyncio.to_thread(
        render_chart,
        groups,
        f"Interactions by {COLUMN_TITLES[data.group_by]}",
        as_pdf=data.format == ChartFormat.PDF,
    )
    await log_event(
        session,
        action=AuditAction.EXPORT,
        entity_type="interaction_statistics",
        changes={"format": data.format, "group_by": data.group_by, "rows": len(rows)},
    )
    await session.commit()
    extension = data.format.value
    return Response(
        content=content,
        media_type="application/pdf" if extension == "pdf" else "image/png",
        headers={
            "Content-Disposition": f'attachment; filename="interaction-statistics.{extension}"'
        },
    )
