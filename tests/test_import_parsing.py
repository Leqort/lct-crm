"""Parser, validators and mapping — no database involved."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.errors import (
    ImportFileTooLargeError,
    ImportInvalidFormatError,
    ImportMappingIncompleteError,
    ValidationError,
)
from app.imports.mapping import suggest_mapping, validate_mapping
from app.imports.parser import detect_format, parse_file
from app.imports.validators import parse_date, parse_email, parse_license_years, parse_text
from app.models.enums import ImportTarget
from tests.factories import CATALOG_HEADERS, catalog_row, make_xlsx


def test_detects_real_xlsx_regardless_of_name():
    content = make_xlsx([catalog_row("Вуз")])
    assert detect_format(content, "catalog.txt") == "xlsx"


def test_rejects_a_file_that_is_not_excel():
    with pytest.raises(ImportInvalidFormatError):
        detect_format(b"just some text", "catalog.xlsx")


def test_rejects_a_zip_that_is_not_a_workbook():
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<xml/>")
    with pytest.raises(ImportInvalidFormatError):
        detect_format(buffer.getvalue(), "catalog.docx")


def test_rejects_file_without_data_rows():
    with pytest.raises(ImportInvalidFormatError):
        parse_file(make_xlsx([]), "empty.xlsx")


def test_rejects_oversized_file(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "import_max_file_size", 10)
    with pytest.raises(ImportFileTooLargeError):
        parse_file(make_xlsx([catalog_row("Вуз")]), "big.xlsx")


def test_parses_headers_and_skips_blank_rows():
    content = make_xlsx([catalog_row("Вуз А"), [None] * len(CATALOG_HEADERS), catalog_row("Вуз Б")])
    parsed = parse_file(content, "catalog.xlsx")
    assert parsed.headers == CATALOG_HEADERS
    assert [row.row_number for row in parsed.rows] == [2, 4]
    assert parsed.rows[0].cells["Название ВУЗа"] == "Вуз А"


def test_duplicate_headers_are_made_unique():
    content = make_xlsx([["a", "b"]], headers=["Колонка", "Колонка"])
    parsed = parse_file(content, "catalog.xlsx")
    assert parsed.headers == ["Колонка", "Колонка (2)"]


@pytest.mark.parametrize(
    "value",
    ["17.02.2026", "2026-02-17", 46070, dt.date(2026, 2, 17), dt.datetime(2026, 2, 17, 12)],
)
def test_date_formats_from_spec(value):
    """SPEC §11: excel-serial, ДД.ММ.ГГГГ and ГГГГ-ММ-ДД must all parse."""
    parsed, message = parse_date(value, "date")
    assert message is None
    assert parsed == dt.date(2026, 2, 17)


def test_unparseable_date_is_a_warning_not_a_failure():
    parsed, message = parse_date("когда-нибудь", "date")
    assert parsed is None
    assert message is not None and message.level.value == "warning"


@pytest.mark.parametrize("value,expected", [(1, 1), (10, 10), ("5", 5), ("3 года", 3)])
def test_license_years_accepted(value, expected):
    parsed, message = parse_license_years(value, "years")
    assert message is None
    assert parsed == expected


@pytest.mark.parametrize("value", [0, 11, -2, "непонятно", 2.5])
def test_license_years_out_of_range_is_warning_with_empty_value(value):
    parsed, message = parse_license_years(value, "years")
    assert parsed is None
    assert message is not None and message.level.value == "warning"


def test_text_is_trimmed_and_collapsed():
    parsed, message = parse_text("  много   пробелов ", "text")
    assert parsed == "много пробелов"
    assert message is None


def test_overlong_text_is_truncated_with_warning():
    parsed, message = parse_text("x" * 50, "text", max_length=10)
    assert parsed is not None and len(parsed) == 10
    assert message is not None


def test_suspicious_email_is_kept_with_warning():
    parsed, message = parse_email("через секретаря", "email")
    assert parsed == "через секретаря"
    assert message is not None


def test_mapping_is_suggested_for_customer_headers():
    suggestion = {
        item["column"]: item["field"]
        for item in suggest_mapping(CATALOG_HEADERS, ImportTarget.INTERACTIONS)
    }
    assert suggestion["Название ВУЗа"] == "universities.name"
    assert suggestion["ФИО Менеджера"] == "interactions.responsible_user_id"
    assert suggestion["Ответственные от ВУЗа"] == "university_contacts.full_name"


def test_mapping_without_required_field_is_rejected():
    with pytest.raises(ImportMappingIncompleteError):
        validate_mapping({"Вендор": "vendors.name"}, ImportTarget.INTERACTIONS, ["Вендор"])


def test_mapping_to_unknown_column_is_rejected():
    with pytest.raises(ValidationError):
        validate_mapping(
            {"Нет такой": "universities.name"}, ImportTarget.INTERACTIONS, CATALOG_HEADERS
        )


def test_one_field_cannot_take_two_columns():
    with pytest.raises(ValidationError):
        validate_mapping(
            {"Название ВУЗа": "universities.name", "Вендор": "universities.name"},
            ImportTarget.INTERACTIONS,
            CATALOG_HEADERS,
        )


def test_detects_and_parses_legacy_xls():
    """The .xls branch uses xlrd, a completely separate code path from .xlsx."""
    from tests.factories import make_xls

    content = make_xls([catalog_row("Вуз из старого файла", contract="ДЛ-XLS")])
    assert detect_format(content, "catalog.xls") == "xls"

    parsed = parse_file(content, "catalog.xls")
    assert parsed.file_format == "xls"
    assert parsed.headers == CATALOG_HEADERS
    assert len(parsed.rows) == 1
    assert parsed.rows[0].cells["Название ВУЗа"] == "Вуз из старого файла"
    assert parsed.rows[0].cells["Номер договора"] == "ДЛ-XLS"


def test_xls_dates_are_converted_not_left_as_serials():
    """xlrd returns dates as floats plus a type flag; the parser must convert them."""
    from tests.factories import make_xls

    content = make_xls([catalog_row("Вуз", signed=dt.date(2026, 2, 17))])
    parsed = parse_file(content, "catalog.xls")
    value = parsed.rows[0].cells["Подписание лицензии"]
    assert str(value).startswith("2026-02-17")
    assert parse_date(value, "date")[0] == dt.date(2026, 2, 17)
