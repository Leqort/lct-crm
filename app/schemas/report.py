"""Request contracts for reports and statistics."""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ReportColumn(StrEnum):
    UNIVERSITY = "university"
    IT_DIRECTION = "it_direction"
    IT_PRODUCT = "it_product"
    WORKFLOW_STAGE = "workflow_stage"
    RESPONSIBLE = "responsible"


class ReportFormat(StrEnum):
    XLS = "xls"
    XLSX = "xlsx"
    PDF = "pdf"
    JSON = "json"


class ChartFormat(StrEnum):
    PNG = "png"
    PDF = "pdf"


class ReportFilters(BaseModel):
    """The same combinable filters used by the interactions list."""

    university_id: uuid.UUID | None = None
    it_direction_id: uuid.UUID | None = None
    it_product_id: uuid.UUID | None = None
    responsible_user_id: uuid.UUID | None = None
    period_from: dt.date | None = None
    period_to: dt.date | None = None

    @model_validator(mode="after")
    def validate_period(self) -> ReportFilters:
        if self.period_from and self.period_to and self.period_from > self.period_to:
            raise ValueError("period_from must not be after period_to")
        return self


class ReportRequest(ReportFilters):
    columns: list[ReportColumn] = Field(min_length=1)
    format: ReportFormat

    @model_validator(mode="after")
    def validate_columns(self) -> ReportRequest:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("columns must not contain duplicates")
        return self


class StatisticsRequest(ReportFilters):
    group_by: ReportColumn = ReportColumn.WORKFLOW_STAGE
    format: ChartFormat
