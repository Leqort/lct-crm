"""Two-phase XLSX import: dry-run then commit (SPEC §6).

Why two phases: the customer must see what will happen before anything is
written. `validate()` therefore resolves and checks everything but creates
nothing; `commit()` re-reads the stored per-row results and writes them inside a
single transaction.

Why the raw rows are stored at upload time: the preview, the commit and the
error report all need the original cells, and keeping them in `import_rows`
avoids having to store uploaded files anywhere on disk (which would be another
personal-data location to protect).
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.core.config import settings
from app.core.errors import (
    AppError,
    ImportJobWrongStateError,
    InternalError,
    ValidationError,
)
from app.imports import mapping as mapping_module
from app.imports.parser import ParsedFile, parse_file
from app.imports.validators import (
    RowMessage,
    error,
    parse_date,
    parse_email,
    parse_license_years,
    parse_text,
    warning,
)
from app.models.enums import (
    AuditAction,
    ImportJobStatus,
    ImportRowStatus,
    ImportTarget,
)
from app.models.import_job import ImportJob, ImportMappingPreset, ImportRow
from app.models.university import University
from app.models.user import User
from app.repositories.import_job import (
    ImportJobRepository,
    ImportMappingPresetRepository,
    ImportRowRepository,
)
from app.services.audit import log_event
from app.services.catalogs import (
    ITDirectionService,
    ITProductService,
    UniversityContactService,
    UniversityService,
    VendorService,
)
from app.services.interactions import InteractionService
from app.services.text import clean_text, normalize_name, normalize_person_name, split_multi_value
from app.services.users import UserService

logger = logging.getLogger(__name__)


@dataclass
class _ValidationCache:
    """Database lookups shared only by one dry-run validation."""

    universities: dict[str, University | None] = dataclass_field(default_factory=dict)
    university_suggestions: dict[str, tuple[University, float] | None] = dataclass_field(
        default_factory=dict
    )
    managers: dict[str, User | None] = dataclass_field(default_factory=dict)


def _empty_stats() -> dict[str, int]:
    return {
        "total": 0,
        "to_create": 0,
        "to_update": 0,
        "skipped": 0,
        "errors": 0,
        "warnings": 0,
        "created": 0,
        "updated": 0,
    }


class ImportService:
    def __init__(self, session: AsyncSession, scope: AccessScope | None = None) -> None:
        self.session = session
        self.scope = scope or AccessScope.system()
        self.jobs = ImportJobRepository(session, self.scope)
        self.rows = ImportRowRepository(session, self.scope)
        self.presets = ImportMappingPresetRepository(session, self.scope)

        self.universities = UniversityService(session, self.scope)
        self.vendors = VendorService(session, self.scope)
        self.products = ITProductService(session, self.scope)
        self.directions = ITDirectionService(session, self.scope)
        self.contacts = UniversityContactService(session, self.scope)
        self.interactions = InteractionService(session, self.scope)
        self.users = UserService(session, self.scope)

    # -- step 1: upload -----------------------------------------------------

    async def create_job(
        self, *, filename: str, content: bytes, target: ImportTarget
    ) -> dict[str, Any]:
        parsed: ParsedFile = parse_file(content, filename)

        previous = await self.jobs.find_previous_with_hash(parsed.file_hash)
        warnings: list[str] = []
        if previous is not None:
            # Not a blocker (SPEC §6 step 4): the upsert makes a repeat import
            # idempotent, the user just deserves to know.
            warnings.append(
                "Файл с таким же содержимым уже загружался "
                f"{previous.created_at:%d.%m.%Y %H:%M}. Повторный импорт не создаст дублей."
            )

        suggestion = mapping_module.suggest_mapping(parsed.headers, target)
        auto_mapping = {
            item["column"]: item["field"] for item in suggestion if item["field"] is not None
        }

        job = ImportJob(
            filename=filename,
            file_hash=parsed.file_hash,
            target=target,
            status=ImportJobStatus.PENDING,
            mapping=auto_mapping,
            stats={**_empty_stats(), "total": len(parsed.rows)},
            source_headers=parsed.headers,
        )
        self.jobs.add(job)
        await self.session.flush()

        for raw in parsed.rows:
            self.session.add(
                ImportRow(
                    job_id=job.id,
                    row_number=raw.row_number,
                    raw_data=raw.cells,
                    parsed_data={},
                    status=ImportRowStatus.OK,
                    messages=[],
                )
            )
        await self.session.flush()
        await self.session.commit()

        return {
            "job": job,
            "headers": parsed.headers,
            "suggested_mapping": suggestion,
            "duplicate_of": previous.id if previous else None,
            "warnings": warnings,
        }

    # -- step 2: mapping ----------------------------------------------------

    async def get_job(self, job_id: uuid.UUID) -> ImportJob:
        return await self.jobs.get_or_fail(job_id)

    async def set_mapping(
        self, job_id: uuid.UUID, mapping: dict[str, str], *, save_as_preset: str | None = None
    ) -> ImportJob:
        job = await self.jobs.get_or_fail(job_id)
        self._require_status(job, {ImportJobStatus.PENDING, ImportJobStatus.VALIDATED})

        headers = [str(h) for h in job.source_headers]
        job.mapping = mapping_module.validate_mapping(mapping, job.target, headers)
        # Changing the mapping invalidates any previous dry-run.
        job.status = ImportJobStatus.PENDING

        if save_as_preset:
            self.session.add(
                ImportMappingPreset(
                    name=clean_text(save_as_preset) or save_as_preset,
                    target=job.target,
                    mapping=job.mapping,
                )
            )
        await self.session.flush()
        await self.session.commit()
        return job

    async def list_presets(
        self, *, target: ImportTarget | None = None, page: int = 1, size: int = 50
    ) -> tuple[list[ImportMappingPreset], int]:
        filters = [ImportMappingPreset.target == target] if target else []
        return await self.presets.list(page=page, size=size, extra_filters=filters)

    async def create_preset(
        self, *, name: str, target: ImportTarget, mapping: dict[str, str]
    ) -> ImportMappingPreset:
        cleaned_name = clean_text(name)
        if not cleaned_name:
            raise ValidationError("Название пресета не может быть пустым")
        known = {item.path for item in mapping_module.fields_for(target)}
        unknown = sorted(set(mapping.values()) - known)
        if unknown:
            raise ValidationError(
                "Пресет ссылается на неизвестные поля: " + ", ".join(unknown),
                details={"unknown_fields": unknown},
            )
        preset = ImportMappingPreset(name=cleaned_name, target=target, mapping=mapping)
        self.presets.add(preset)
        await self.session.flush()
        await self.session.commit()
        return preset

    # -- step 3: dry-run ----------------------------------------------------

    async def validate(self, job_id: uuid.UUID) -> tuple[ImportJob, dict[str, int]]:
        job = await self.jobs.get_or_fail(job_id)
        self._require_status(job, {ImportJobStatus.PENDING, ImportJobStatus.VALIDATED})

        headers = [str(h) for h in job.source_headers]
        mapping = mapping_module.validate_mapping(dict(job.mapping), job.target, headers)

        rows = await self.rows.list_for_job(job_id, page=1, size=settings.import_max_rows)
        all_rows = rows[0]

        stats = _empty_stats()
        stats["total"] = len(all_rows)

        # First pass: parse and resolve every row. Repeated values in one file
        # share lookup results, avoiding N+1 queries on large dry-runs.
        cache = _ValidationCache()
        seen_keys: dict[str, int] = {}
        for row in all_rows:
            parsed, messages = await self._validate_row(job.target, row, mapping, cache)
            row.parsed_data = parsed
            row.status = self._status_from(messages)
            row.messages = [m.to_dict() for m in messages]
            row.resolved_entity_id = (
                uuid.UUID(parsed["existing_entity_id"])
                if parsed.get("existing_entity_id")
                else None
            )

        # Second pass: duplicates *within the file*. The last occurrence wins,
        # earlier ones are warned about and skipped at commit (SPEC §6 step 3).
        for row in all_rows:
            key = row.parsed_data.get("dedupe_key")
            if not key or row.status == ImportRowStatus.ERROR:
                continue
            seen_keys[key] = row.row_number
        for row in all_rows:
            key = row.parsed_data.get("dedupe_key")
            if not key or row.status == ImportRowStatus.ERROR:
                continue
            winner = seen_keys[key]
            if winner != row.row_number:
                row.parsed_data = {**row.parsed_data, "superseded_by_row": winner}
                row.messages = [
                    *row.messages,
                    warning(
                        f"Строка дублирует данные строки {winner}; будет применена последняя",
                    ).to_dict(),
                ]
                row.status = ImportRowStatus.WARNING

        for row in all_rows:
            if row.status == ImportRowStatus.ERROR:
                stats["errors"] += 1
                stats["skipped"] += 1
                continue
            if row.status == ImportRowStatus.WARNING:
                stats["warnings"] += 1
            if row.parsed_data.get("superseded_by_row"):
                stats["skipped"] += 1
            elif row.resolved_entity_id is not None:
                stats["to_update"] += 1
            else:
                stats["to_create"] += 1

        job.stats = stats
        job.status = ImportJobStatus.VALIDATED
        job.error_message = None
        await self.session.flush()
        await self.session.commit()
        return job, stats

    def _status_from(self, messages: list[RowMessage]) -> ImportRowStatus:
        if any(m.level == ImportRowStatus.ERROR for m in messages):
            return ImportRowStatus.ERROR
        if any(m.level == ImportRowStatus.WARNING for m in messages):
            return ImportRowStatus.WARNING
        return ImportRowStatus.OK

    def _cell(self, row: ImportRow, mapping: dict[str, str], path: str) -> Any:
        """Read the cell mapped to `path`, or None when the column is unmapped."""
        for column, target_path in mapping.items():
            if target_path == path:
                return row.raw_data.get(column)
        return None

    async def _validate_row(
        self,
        target: ImportTarget,
        row: ImportRow,
        mapping: dict[str, str],
        cache: _ValidationCache,
    ) -> tuple[dict[str, Any], list[RowMessage]]:
        handlers = {
            ImportTarget.INTERACTIONS: self._validate_interaction_row,
            ImportTarget.UNIVERSITIES: self._validate_university_row,
            ImportTarget.IT_PRODUCTS: self._validate_product_row,
            ImportTarget.CONTACTS: self._validate_contact_row,
        }
        return await handlers[target](row, mapping, cache)

    async def _resolve_university(
        self, name: Any, messages: list[RowMessage], cache: _ValidationCache
    ) -> tuple[str | None, uuid.UUID | None]:
        """Match a university name, suggesting near-misses without merging."""
        cleaned = clean_text(name)
        if not cleaned:
            messages.append(error("Не заполнено «Название ВУЗа»", "universities.name"))
            return None, None

        normalized = normalize_name(cleaned)
        if normalized not in cache.universities:
            cache.universities[normalized] = await self.universities.repo.find_by_normalized_name(
                normalized
            )
        existing = cache.universities[normalized]
        if existing is not None:
            return cleaned, existing.id

        # No exact match: offer the closest existing name but never merge
        # automatically (SPEC §6 step 3).
        threshold = settings.import_fuzzy_threshold / 100.0
        if normalized not in cache.university_suggestions:
            similar = await self.universities.suggest_similar(cleaned, limit=1)
            cache.university_suggestions[normalized] = similar[0] if similar else None
        suggestion = cache.university_suggestions[normalized]
        if suggestion is not None and suggestion[1] >= threshold:
            candidate, score = suggestion
            messages.append(
                warning(
                    f"Вуз «{cleaned}» не найден, но похож на «{candidate.name}» "
                    f"(совпадение {score:.0%}). Будет создан новый вуз — "
                    "объедините записи вручную, если это одно и то же.",
                    "universities.name",
                    university_id=str(candidate.id),
                    university_name=candidate.name,
                    score=round(score, 3),
                )
            )
        return cleaned, None

    async def _validate_interaction_row(
        self, row: ImportRow, mapping: dict[str, str], cache: _ValidationCache
    ) -> tuple[dict[str, Any], list[RowMessage]]:
        messages: list[RowMessage] = []
        parsed: dict[str, Any] = {}

        university_name, university_id = await self._resolve_university(
            self._cell(row, mapping, "universities.name"), messages, cache
        )
        parsed["university_name"] = university_name
        parsed["university_id"] = str(university_id) if university_id else None

        vendor_name, message = parse_text(self._cell(row, mapping, "vendors.name"), "vendors.name")
        _append(messages, message)
        parsed["vendor_name"] = vendor_name

        product_name, message = parse_text(
            self._cell(row, mapping, "it_products.name"), "it_products.name"
        )
        _append(messages, message)
        parsed["product_name"] = product_name

        direction_name, message = parse_text(
            self._cell(row, mapping, "it_directions.name"), "it_directions.name"
        )
        _append(messages, message)
        parsed["direction_name"] = direction_name

        contract_number, message = parse_text(
            self._cell(row, mapping, "interactions.contract_number"),
            "interactions.contract_number",
            max_length=200,
        )
        _append(messages, message)
        parsed["contract_number"] = contract_number

        signed_at, message = parse_date(
            self._cell(row, mapping, "interactions.license_signed_at"),
            "interactions.license_signed_at",
        )
        _append(messages, message)
        parsed["license_signed_at"] = signed_at.isoformat() if signed_at else None

        years, message = parse_license_years(
            self._cell(row, mapping, "interactions.license_years"),
            "interactions.license_years",
        )
        _append(messages, message)
        parsed["license_years"] = years

        transfer_status, message = parse_text(
            self._cell(row, mapping, "interactions.transfer_status"),
            "interactions.transfer_status",
            max_length=300,
        )
        _append(messages, message)
        parsed["transfer_status"] = transfer_status

        comment, message = parse_text(
            self._cell(row, mapping, "interactions.comment"), "interactions.comment"
        )
        _append(messages, message)
        parsed["comment"] = comment

        # Manager: unmatched or ambiguous name is a warning, the row still goes in.
        manager_raw = clean_text(self._cell(row, mapping, "interactions.responsible_user_id"))
        parsed["responsible_name"] = manager_raw
        parsed["responsible_user_id"] = None
        if manager_raw:
            normalized_manager = normalize_person_name(manager_raw)
            if normalized_manager not in cache.managers:
                cache.managers[normalized_manager] = await self.users.match_by_full_name(
                    manager_raw
                )
            user = cache.managers[normalized_manager]
            if user is None:
                messages.append(
                    warning(
                        f"Менеджер «{manager_raw}» не найден среди сотрудников; "
                        "поле «ответственный» останется пустым",
                        "interactions.responsible_user_id",
                    )
                )
            else:
                parsed["responsible_user_id"] = str(user.id)

        parsed["contact_names"] = split_multi_value(
            self._cell(row, mapping, "university_contacts.full_name")
        )

        # File-level dedupe key: names, because ids may not exist yet.
        if university_name:
            parsed["dedupe_key"] = "|".join(
                (
                    normalize_name(university_name),
                    normalize_name(direction_name),
                    normalize_name(product_name),
                )
            )

        # Preview: is this an update of an existing card or a new one?
        parsed["existing_entity_id"] = None
        if university_id is not None:
            existing = await self._find_existing_interaction(
                university_id, direction_name, product_name, vendor_name
            )
            if existing is not None:
                parsed["existing_entity_id"] = str(existing.id)
        return parsed, messages

    async def _find_existing_interaction(
        self,
        university_id: uuid.UUID,
        direction_name: str | None,
        product_name: str | None,
        vendor_name: str | None,
    ) -> Any:
        direction_id = None
        if direction_name:
            found = await self.directions.repo.find_by_normalized_name(
                normalize_name(direction_name)
            )
            if found is None:
                return None  # direction does not exist yet -> nothing to update
            direction_id = found.id

        product_id = None
        if product_name:
            vendor_id = None
            if vendor_name:
                vendor = await self.vendors.repo.find_by_normalized_name(
                    normalize_name(vendor_name)
                )
                if vendor is None:
                    return None
                vendor_id = vendor.id
            product = await self.products.repo.find_by_name_and_vendor(
                normalize_name(product_name), vendor_id
            )
            if product is None:
                return None
            product_id = product.id

        return await self.interactions.repo.find_by_business_key(
            university_id, direction_id, product_id
        )

    async def _validate_university_row(
        self, row: ImportRow, mapping: dict[str, str], cache: _ValidationCache
    ) -> tuple[dict[str, Any], list[RowMessage]]:
        messages: list[RowMessage] = []
        parsed: dict[str, Any] = {}

        name, university_id = await self._resolve_university(
            self._cell(row, mapping, "universities.name"), messages, cache
        )
        parsed["university_name"] = name
        parsed["existing_entity_id"] = str(university_id) if university_id else None
        if name:
            parsed["dedupe_key"] = normalize_name(name)

        for path, max_length in (
            ("universities.short_name", 200),
            ("universities.region", 200),
            ("universities.inn", 20),
            ("universities.external_id", 200),
            ("universities.comment", 10_000),
        ):
            value, message = parse_text(self._cell(row, mapping, path), path, max_length=max_length)
            _append(messages, message)
            parsed[path.split(".", 1)[1]] = value
        return parsed, messages

    async def _validate_product_row(
        self, row: ImportRow, mapping: dict[str, str], _cache: _ValidationCache
    ) -> tuple[dict[str, Any], list[RowMessage]]:
        messages: list[RowMessage] = []
        parsed: dict[str, Any] = {}

        product_name, message = parse_text(
            self._cell(row, mapping, "it_products.name"), "it_products.name", max_length=300
        )
        _append(messages, message)
        if not product_name:
            messages.append(error("Не заполнено название ПО", "it_products.name"))
        parsed["product_name"] = product_name

        vendor_name, message = parse_text(
            self._cell(row, mapping, "vendors.name"), "vendors.name", max_length=300
        )
        _append(messages, message)
        parsed["vendor_name"] = vendor_name

        description, message = parse_text(
            self._cell(row, mapping, "it_products.description"), "it_products.description"
        )
        _append(messages, message)
        parsed["description"] = description

        parsed["direction_names"] = split_multi_value(
            self._cell(row, mapping, "it_directions.name")
        )

        parsed["existing_entity_id"] = None
        if product_name:
            parsed["dedupe_key"] = f"{normalize_name(product_name)}|{normalize_name(vendor_name)}"
            vendor_id = None
            vendor_missing = False
            if vendor_name:
                vendor = await self.vendors.repo.find_by_normalized_name(
                    normalize_name(vendor_name)
                )
                vendor_missing = vendor is None
                vendor_id = vendor.id if vendor else None
            if not vendor_missing:
                existing = await self.products.repo.find_by_name_and_vendor(
                    normalize_name(product_name), vendor_id
                )
                if existing is not None:
                    parsed["existing_entity_id"] = str(existing.id)
        return parsed, messages

    async def _validate_contact_row(
        self, row: ImportRow, mapping: dict[str, str], cache: _ValidationCache
    ) -> tuple[dict[str, Any], list[RowMessage]]:
        messages: list[RowMessage] = []
        parsed: dict[str, Any] = {}

        university_name, university_id = await self._resolve_university(
            self._cell(row, mapping, "universities.name"), messages, cache
        )
        parsed["university_name"] = university_name
        parsed["university_id"] = str(university_id) if university_id else None

        full_name, message = parse_text(
            self._cell(row, mapping, "university_contacts.full_name"),
            "university_contacts.full_name",
            max_length=300,
        )
        _append(messages, message)
        if not full_name:
            messages.append(error("Не заполнено ФИО контакта", "university_contacts.full_name"))
        parsed["full_name"] = full_name

        position, message = parse_text(
            self._cell(row, mapping, "university_contacts.position"),
            "university_contacts.position",
            max_length=300,
        )
        _append(messages, message)
        parsed["position"] = position

        email, message = parse_email(
            self._cell(row, mapping, "university_contacts.email"), "university_contacts.email"
        )
        _append(messages, message)
        parsed["email"] = email

        phone, message = parse_text(
            self._cell(row, mapping, "university_contacts.phone"),
            "university_contacts.phone",
            max_length=50,
        )
        _append(messages, message)
        parsed["phone"] = phone

        parsed["existing_entity_id"] = None
        if university_name and full_name:
            parsed["dedupe_key"] = f"{normalize_name(university_name)}|{normalize_name(full_name)}"
            if university_id is not None:
                existing = await self.contacts.repo.find_by_name(university_id, full_name)
                if existing is not None:
                    parsed["existing_entity_id"] = str(existing.id)
        return parsed, messages

    # -- step 4: commit -----------------------------------------------------

    async def commit(self, job_id: uuid.UUID) -> tuple[ImportJob, dict[str, int]]:
        job = await self.jobs.get_or_fail(job_id)
        self._require_status(job, {ImportJobStatus.VALIDATED})

        rows = await self.rows.iter_importable(job_id)
        stats = dict(job.stats) if job.stats else _empty_stats()
        stats.update({"created": 0, "updated": 0})

        handlers = {
            ImportTarget.INTERACTIONS: self._commit_interaction_row,
            ImportTarget.UNIVERSITIES: self._commit_university_row,
            ImportTarget.IT_PRODUCTS: self._commit_product_row,
            ImportTarget.CONTACTS: self._commit_contact_row,
        }
        handler = handlers[job.target]

        try:
            for row in rows:
                if row.parsed_data.get("superseded_by_row"):
                    # An earlier duplicate of a later row: intentionally skipped.
                    continue
                entity_id, created = await handler(row)
                row.resolved_entity_id = entity_id
                if created:
                    stats["created"] += 1
                else:
                    stats["updated"] += 1

            job.status = ImportJobStatus.COMMITTED
            job.committed_at = dt.datetime.now(dt.UTC)
            job.stats = stats
            await self.session.flush()

            # One summary entry for the import itself, on top of the ordinary
            # create/update entries the ORM events produced (SPEC §6 step 4).
            await log_event(
                self.session,
                action=AuditAction.IMPORT,
                entity_type="import_jobs",
                entity_id=job.id,
                changes={
                    "filename": job.filename,
                    "file_hash": job.file_hash,
                    "target": job.target.value,
                    "stats": stats,
                },
            )
            await self.session.commit()
        except AppError:
            await self._mark_failed(job_id, "Импорт прерван из-за ошибки в данных")
            raise
        except Exception as exc:
            logger.exception("import commit failed for job %s", job_id)
            await self._mark_failed(job_id, str(exc))
            raise InternalError(
                "Импорт не выполнен: произошла ошибка, изменения отменены",
                details={"job_id": str(job_id)},
            ) from exc

        return job, stats

    async def _mark_failed(self, job_id: uuid.UUID, message: str) -> None:
        """Roll everything back, then persist the failure in a fresh transaction."""
        await self.session.rollback()
        job = await self.jobs.get(job_id)
        if job is None:  # pragma: no cover - the job was just read successfully
            return
        job.status = ImportJobStatus.FAILED
        job.error_message = message[:2000]
        await self.session.flush()
        await self.session.commit()

    async def _commit_interaction_row(self, row: ImportRow) -> tuple[uuid.UUID, bool]:
        data = row.parsed_data
        university, _ = await self.universities.find_or_create(data["university_name"])

        vendor_id = None
        if data.get("vendor_name"):
            vendor, _ = await self.vendors.find_or_create(data["vendor_name"])
            vendor_id = vendor.id

        product_id = None
        if data.get("product_name"):
            product, _ = await self.products.find_or_create(
                data["product_name"], vendor_id=vendor_id
            )
            product_id = product.id

        direction_id = None
        if data.get("direction_name"):
            direction, _ = await self.directions.find_or_create(data["direction_name"])
            direction_id = direction.id

        values: dict[str, Any] = {
            "contract_number": data.get("contract_number"),
            "license_signed_at": _as_date(data.get("license_signed_at")),
            "license_years": data.get("license_years"),
            "transfer_status": data.get("transfer_status"),
            "comment": data.get("comment"),
            "responsible_user_id": _as_uuid(data.get("responsible_user_id")),
        }
        interaction, created = await self.interactions.upsert(
            university_id=university.id,
            it_direction_id=direction_id,
            it_product_id=product_id,
            values=values,
        )

        # Contacts named in the row are attached to the university, not to the
        # interaction: the model keeps them on the university side.
        for contact_name in data.get("contact_names", []):
            await self.contacts.find_or_create(university.id, contact_name)

        return interaction.id, created

    async def _commit_university_row(self, row: ImportRow) -> tuple[uuid.UUID, bool]:
        data = row.parsed_data
        university, created = await self.universities.find_or_create(data["university_name"])
        for field in ("short_name", "region", "inn", "external_id", "comment"):
            value = data.get(field)
            if value is not None:  # empty cell never clears a stored value
                setattr(university, field, value)
        await self.session.flush()
        return university.id, created

    async def _commit_product_row(self, row: ImportRow) -> tuple[uuid.UUID, bool]:
        data = row.parsed_data
        vendor_id = None
        if data.get("vendor_name"):
            vendor, _ = await self.vendors.find_or_create(data["vendor_name"])
            vendor_id = vendor.id
        product, created = await self.products.find_or_create(
            data["product_name"], vendor_id=vendor_id
        )
        if data.get("description") is not None:
            product.description = data["description"]
        for direction_name in data.get("direction_names", []):
            direction, _ = await self.directions.find_or_create(direction_name)
            if direction not in product.directions:
                product.directions.append(direction)
        await self.session.flush()
        return product.id, created

    async def _commit_contact_row(self, row: ImportRow) -> tuple[uuid.UUID, bool]:
        data = row.parsed_data
        university, _ = await self.universities.find_or_create(data["university_name"])
        contact, created = await self.contacts.find_or_create(university.id, data["full_name"])
        for field in ("position", "email", "phone"):
            value = data.get(field)
            if value is not None:
                setattr(contact, field, value)
        await self.session.flush()
        return contact.id, created

    # -- listings -----------------------------------------------------------

    async def list_jobs(
        self, *, page: int = 1, size: int = 50, search: str | None = None, sort: str | None = None
    ) -> tuple[list[ImportJob], int]:
        return await self.jobs.list(page=page, size=size, search=search, sort=sort)

    async def list_rows(
        self,
        job_id: uuid.UUID,
        *,
        status: ImportRowStatus | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[ImportRow], int]:
        await self.jobs.get_or_fail(job_id)
        return await self.rows.list_for_job(job_id, status=status, page=page, size=size)

    async def all_rows(self, job_id: uuid.UUID) -> list[ImportRow]:
        rows, _ = await self.rows.list_for_job(job_id, page=1, size=settings.import_max_rows)
        return rows

    def _require_status(self, job: ImportJob, allowed: set[ImportJobStatus]) -> None:
        if job.status not in allowed:
            raise ImportJobWrongStateError(
                f"Задача импорта находится в статусе «{job.status.value}», "
                "операция для него недоступна",
                details={
                    "job_id": str(job.id),
                    "status": job.status.value,
                    "allowed": sorted(s.value for s in allowed),
                },
            )


def _append(messages: list[RowMessage], message: RowMessage | None) -> None:
    if message is not None:
        messages.append(message)


def _as_date(value: Any) -> dt.date | None:
    return dt.date.fromisoformat(value) if isinstance(value, str) and value else None


def _as_uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(value) if isinstance(value, str) and value else None


__all__ = ["ImportService"]
