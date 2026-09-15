"""Helpers for building test fixtures: xlsx files and catalog entities."""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook

CATALOG_HEADERS = [
    "Название ВУЗа",
    "Вендор",
    "ПО",
    "Номер договора",
    "Подписание лицензии",
    "Срок действия лицензии (год)",
    "Статус по передаче",
    "ФИО Менеджера",
    "Ответственные от ВУЗа",
    "Комментарий",
]


def make_xlsx(rows: list[list[Any]], headers: list[str] | None = None) -> bytes:
    """Build an in-memory .xlsx with the customer's column titles."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Каталог"
    sheet.append(headers or CATALOG_HEADERS)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def catalog_row(
    university: str,
    vendor: str | None = "Астра",
    product: str | None = "Astra Linux",
    contract: str | None = "ДЛ-001",
    signed: Any = "17.02.2026",
    years: Any = 3,
    status: str | None = "Передано",
    manager: str | None = None,
    contacts: str | None = None,
    comment: str | None = None,
) -> list[Any]:
    return [
        university,
        vendor,
        product,
        contract,
        signed,
        years,
        status,
        manager,
        contacts,
        comment,
    ]


def make_xls(rows: list[list[Any]], headers: list[str] | None = None) -> bytes:
    """Build an in-memory legacy .xls so the xlrd branch of the parser is covered.

    `xlwt` is a test-only dependency: the application reads .xls, never writes it.
    """
    import datetime as dt

    import xlwt

    book = xlwt.Workbook(encoding="utf-8")
    sheet = book.add_sheet("Каталог")
    date_style = xlwt.easyxf(num_format_str="DD.MM.YYYY")
    for column, title in enumerate(headers or CATALOG_HEADERS):
        sheet.write(0, column, title)
    for row_index, row in enumerate(rows, start=1):
        for column, value in enumerate(row):
            if isinstance(value, dt.date):
                sheet.write(row_index, column, value, date_style)
            else:
                sheet.write(row_index, column, value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
