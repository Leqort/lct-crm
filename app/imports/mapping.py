"""Column-mapping presets and auto-suggestion (SPEC §6 step 2).

The customer's column titles are fixed and must not be renamed, so they are
listed verbatim as the primary alias of each target field.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from app.core.errors import ImportMappingIncompleteError, ValidationError
from app.models.enums import ImportTarget
from app.services.text import normalize_name


@dataclass(frozen=True, slots=True)
class TargetField:
    """One writable destination for a spreadsheet column."""

    path: str  # "table.column", e.g. "interactions.contract_number"
    title: str  # human-readable label for the mapping UI
    aliases: tuple[str, ...] = field(default=())
    required: bool = False


# --- Field catalogues per import target -----------------------------------
# «Название ВУЗа», «Вендор», ... are the customer's literal column titles.

_UNIVERSITY_NAME = TargetField(
    path="universities.name",
    title="Название ВУЗа",
    aliases=("Название ВУЗа", "ВУЗ", "Вуз", "Наименование вуза", "Университет"),
    required=True,
)

INTERACTION_FIELDS: tuple[TargetField, ...] = (
    _UNIVERSITY_NAME,
    TargetField(
        path="vendors.name",
        title="Вендор",
        aliases=("Вендор", "Производитель", "Вендор ПО"),
    ),
    TargetField(
        path="it_products.name",
        title="ПО",
        aliases=("ПО", "Продукт", "Программное обеспечение", "Название ПО"),
    ),
    TargetField(
        path="it_directions.name",
        title="ИТ-направление",
        aliases=("ИТ-направление", "Направление", "ИТ направление"),
    ),
    TargetField(
        path="interactions.contract_number",
        title="Номер договора",
        aliases=("Номер договора", "Договор", "№ договора"),
    ),
    TargetField(
        path="interactions.license_signed_at",
        title="Подписание лицензии",
        aliases=("Подписание лицензии", "Дата подписания лицензии", "Дата подписания"),
    ),
    TargetField(
        path="interactions.license_years",
        title="Срок действия лицензии (год)",
        aliases=("Срок действия лицензии (год)", "Срок действия лицензии", "Срок лицензии"),
    ),
    TargetField(
        path="interactions.transfer_status",
        title="Статус по передаче",
        # The customer's own ТЗ spells this column «Статус по передачи»; both
        # spellings occur in real files.
        aliases=("Статус по передаче", "Статус по передачи", "Статус передачи", "Статус"),
    ),
    TargetField(
        path="interactions.responsible_user_id",
        title="ФИО Менеджера",
        aliases=("ФИО Менеджера", "Менеджер", "Ответственный менеджер", "КАМ"),
    ),
    TargetField(
        path="university_contacts.full_name",
        title="Ответственные от ВУЗа",
        aliases=("Ответственные от ВУЗа", "Ответственный от ВУЗа", "Контакты вуза"),
    ),
    TargetField(
        path="interactions.comment",
        title="Комментарий",
        aliases=("Комментарий", "Примечание", "Комментарии"),
    ),
)

UNIVERSITY_FIELDS: tuple[TargetField, ...] = (
    _UNIVERSITY_NAME,
    TargetField(
        path="universities.short_name",
        title="Сокращённое название",
        aliases=("Сокращённое название", "Аббревиатура", "Краткое название"),
    ),
    TargetField(path="universities.region", title="Регион", aliases=("Регион", "Субъект РФ")),
    TargetField(path="universities.inn", title="ИНН", aliases=("ИНН",)),
    TargetField(
        path="universities.external_id",
        title="Внешний ID",
        aliases=("Внешний ID", "ID в LMS", "external_id"),
    ),
    TargetField(
        path="universities.comment", title="Комментарий", aliases=("Комментарий", "Примечание")
    ),
)

IT_PRODUCT_FIELDS: tuple[TargetField, ...] = (
    TargetField(
        path="it_products.name",
        title="ПО",
        aliases=("ПО", "Продукт", "Название ПО", "Программное обеспечение"),
        required=True,
    ),
    TargetField(path="vendors.name", title="Вендор", aliases=("Вендор", "Производитель")),
    TargetField(
        path="it_directions.name",
        title="ИТ-направление",
        aliases=("ИТ-направление", "Направление"),
    ),
    TargetField(
        path="it_products.description", title="Описание", aliases=("Описание", "Комментарий")
    ),
)

CONTACT_FIELDS: tuple[TargetField, ...] = (
    _UNIVERSITY_NAME,
    TargetField(
        path="university_contacts.full_name",
        title="ФИО",
        aliases=("ФИО", "Ответственные от ВУЗа", "Контактное лицо", "ФИО контакта"),
        required=True,
    ),
    TargetField(path="university_contacts.position", title="Должность", aliases=("Должность",)),
    TargetField(
        path="university_contacts.email",
        title="Email",
        aliases=("Email", "E-mail", "Почта", "Электронная почта"),
    ),
    TargetField(
        path="university_contacts.phone",
        title="Телефон",
        aliases=("Телефон", "Тел.", "Контактный телефон"),
    ),
)

FIELDS_BY_TARGET: dict[ImportTarget, tuple[TargetField, ...]] = {
    ImportTarget.INTERACTIONS: INTERACTION_FIELDS,
    ImportTarget.UNIVERSITIES: UNIVERSITY_FIELDS,
    ImportTarget.IT_PRODUCTS: IT_PRODUCT_FIELDS,
    ImportTarget.CONTACTS: CONTACT_FIELDS,
}

# Below this score a fuzzy header match is not offered at all: a wrong
# pre-selection is worse than an empty one, because users tend to confirm
# blindly.
_SUGGEST_THRESHOLD = 75


def fields_for(target: ImportTarget) -> tuple[TargetField, ...]:
    return FIELDS_BY_TARGET[target]


def suggest_mapping(headers: list[str], target: ImportTarget) -> list[dict[str, object]]:
    """Propose «column → field» pairs by fuzzy-matching header text.

    Each target field is used at most once: the best-scoring header wins, the
    rest stay unmapped so the user can decide.
    """
    fields = fields_for(target)
    # alias (normalised) -> field path
    alias_index: dict[str, str] = {}
    for item in fields:
        for alias in (item.title, *item.aliases):
            alias_index[normalize_name(alias)] = item.path

    choices = list(alias_index.keys())
    taken: set[str] = set()
    scored: list[tuple[str, str | None, float]] = []

    for header in headers:
        normalized = normalize_name(header)
        match = process.extractOne(normalized, choices, scorer=fuzz.WRatio)
        if match is None or match[1] < _SUGGEST_THRESHOLD:
            scored.append((header, None, 0.0))
            continue
        scored.append((header, alias_index[match[0]], float(match[1])))

    # Resolve collisions: the highest-confidence header keeps the field.
    scored.sort(key=lambda row: row[2], reverse=True)
    resolved: dict[str, tuple[str | None, float]] = {}
    for header, path, score in scored:
        if path is not None and path in taken:
            resolved[header] = (None, 0.0)
            continue
        if path is not None:
            taken.add(path)
        resolved[header] = (path, score)

    return [
        {
            "column": header,
            "field": resolved[header][0],
            "confidence": round(resolved[header][1] / 100.0, 3),
        }
        for header in headers
    ]


def validate_mapping(
    mapping: dict[str, str], target: ImportTarget, headers: list[str]
) -> dict[str, str]:
    """Check a user-confirmed mapping before anything is written.

    Rejects unknown columns, unknown fields, one field mapped twice, and a
    missing required field.
    """
    known_paths = {item.path for item in fields_for(target)}
    header_set = set(headers)
    cleaned: dict[str, str] = {}
    seen_paths: set[str] = set()

    for column, path in mapping.items():
        if not path:
            continue
        if column not in header_set:
            raise ValidationError(
                f"Колонка «{column}» отсутствует в загруженном файле",
                details={"column": column, "headers": headers},
            )
        if path not in known_paths:
            raise ValidationError(
                f"Поле «{path}» не поддерживается для этого типа импорта",
                details={"field": path, "allowed": sorted(known_paths)},
            )
        if path in seen_paths:
            raise ValidationError(
                f"Поле «{path}» сопоставлено более чем одной колонке",
                details={"field": path},
            )
        seen_paths.add(path)
        cleaned[column] = path

    missing = [
        item.path for item in fields_for(target) if item.required and item.path not in seen_paths
    ]
    if missing:
        raise ImportMappingIncompleteError(
            "Не сопоставлены обязательные поля: " + ", ".join(missing),
            details={"missing_fields": missing},
        )
    return cleaned
