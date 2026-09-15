"""Per-cell parsing and validation rules (SPEC §6 step 3).

Every function returns `(value, message_or_None)` instead of raising: a single
bad cell must degrade to a warning on one row, never abort the whole file.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass
from typing import Any

from app.models.enums import ImportRowStatus

# Excel's day 0. Windows Excel wrongly treats 1900 as a leap year, so serial 1
# is 1900-01-01 with an origin of 1899-12-30.
_EXCEL_EPOCH = dt.date(1899, 12, 30)

_DATE_PATTERNS = (
    ("%d.%m.%Y", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")),
    ("%Y-%m-%d", re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")),
    ("%d/%m/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
    ("%d.%m.%y", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2}$")),
)

LICENSE_YEARS_MIN = 1
LICENSE_YEARS_MAX = 10


@dataclass(slots=True)
class RowMessage:
    """A single note attached to an imported row."""

    level: ImportRowStatus
    text: str
    field: str | None = None
    suggestion: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["level"] = self.level.value
        return data


def warning(text: str, field: str | None = None, **suggestion: Any) -> RowMessage:
    return RowMessage(ImportRowStatus.WARNING, text, field, suggestion or None)


def error(text: str, field: str | None = None) -> RowMessage:
    return RowMessage(ImportRowStatus.ERROR, text, field)


def parse_date(value: Any, field: str) -> tuple[dt.date | None, RowMessage | None]:
    """Accept Excel serials, real date objects and the three text formats.

    SPEC §6 requires `ДД.ММ.ГГГГ` and `ГГГГ-ММ-ДД` plus Excel serials; `ДД/ММ/ГГГГ`
    and two-digit years are accepted as well because the customer's files
    contain both.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None

    if isinstance(value, dt.datetime):
        return value.date(), None
    if isinstance(value, dt.date):
        return value, None

    # Excel serial number.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        serial = float(value)
        if serial <= 0 or serial > 2_958_465:  # 9999-12-31
            return None, warning(f"Не удалось распознать дату «{value}»", field)
        return _EXCEL_EPOCH + dt.timedelta(days=int(serial)), None

    text = str(value).strip()
    # The parser stores dates as ISO strings; try that first.
    try:
        return dt.date.fromisoformat(text[:10]), None
    except ValueError:
        pass

    for fmt, pattern in _DATE_PATTERNS:
        if pattern.match(text):
            try:
                return dt.datetime.strptime(text, fmt).date(), None
            except ValueError:
                continue

    return None, warning(f"Не удалось распознать дату «{text}»", field)


def parse_license_years(value: Any, field: str) -> tuple[int | None, RowMessage | None]:
    """Licence term: an integer 1..10, otherwise a warning and an empty field."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None

    text = str(value).strip().replace(",", ".")
    # Tolerate "3 года", "3 г." and similar free-form suffixes.
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match is None:
        return None, warning(f"Не удалось распознать срок лицензии «{value}»", field)

    number = float(match.group())
    if number != int(number):
        return None, warning(f"Срок лицензии «{value}» не является целым числом", field)
    years = int(number)
    if not (LICENSE_YEARS_MIN <= years <= LICENSE_YEARS_MAX):
        return None, warning(
            f"Срок лицензии «{years}» вне допустимого диапазона "
            f"{LICENSE_YEARS_MIN}–{LICENSE_YEARS_MAX}",
            field,
        )
    return years, None


def parse_text(
    value: Any, field: str, *, max_length: int = 10_000
) -> tuple[str | None, RowMessage | None]:
    if value is None:
        return None, None
    text = " ".join(str(value).split())
    if not text:
        return None, None
    if len(text) > max_length:
        return text[:max_length], warning(f"Значение поля обрезано до {max_length} символов", field)
    return text, None


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_email(value: Any, field: str) -> tuple[str | None, RowMessage | None]:
    text, message = parse_text(value, field, max_length=320)
    if text is None or message is not None:
        return text, message
    if not _EMAIL_RE.match(text):
        # Kept, not dropped: the customer's catalogs contain "почта через
        # секретаря" style notes that are still useful to a human.
        return text, warning(f"Значение «{text}» не похоже на email", field)
    return text, None
