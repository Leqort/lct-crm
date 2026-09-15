"""Seed demo data: three users (one per role), catalogs and a few interactions.

Idempotent — run it as many times as you like, it will not create duplicates.

    python -m scripts.seed
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.core.access import AccessScope
from app.core.audit import AuditContext, audit_context, register_audit_listeners
from app.core.db import SessionFactory, dispose_engine
from app.core.logging import configure_logging
from app.models.enums import UserRole
from app.schemas.university import AssignmentCreate
from app.schemas.user import UserCreate
from app.services.assignments import AssignmentService
from app.services.catalogs import (
    ITDirectionService,
    ITProductService,
    UniversityContactService,
    UniversityService,
    VendorService,
)
from app.services.interactions import InteractionService
from app.services.users import UserService
from app.services.workflow_presets import ensure_base_workflow

logger = logging.getLogger("seed")

DEMO_USERS = [
    ("seed-admin", "Администратор Системный Иванович", "admin@it-school.example", UserRole.ADMIN),
    ("seed-manager", "Менеджерова Ольга Сергеевна", "manager@it-school.example", UserRole.MANAGER),
    ("seed-user", "Камов Кирилл Андреевич", "kam@it-school.example", UserRole.USER),
]

DEMO_DIRECTIONS = [
    ("DevOps", "Практики непрерывной поставки и эксплуатации"),
    ("QA", "Тестирование и обеспечение качества"),
    ("Data Science", "Анализ данных и машинное обучение"),
    ("Информационная безопасность", "Защита информации и инфраструктуры"),
]

DEMO_VENDORS = ["Астра", "Ред Софт", "Postgres Professional", "БАЗАЛЬТ СПО"]

DEMO_PRODUCTS = [
    ("Astra Linux Special Edition", "Астра", ["DevOps", "Информационная безопасность"]),
    ("РЕД ОС", "Ред Софт", ["DevOps"]),
    ("Postgres Pro Enterprise", "Postgres Professional", ["Data Science"]),
    ("Альт Рабочая станция", "БАЗАЛЬТ СПО", ["QA"]),
]

DEMO_UNIVERSITIES = [
    ("МГТУ им. Н.Э. Баумана", "Бауманка", "г. Москва", "7701002520"),
    (
        "Санкт-Петербургский политехнический университет",
        "СПбПУ",
        "г. Санкт-Петербург",
        "7804040077",
    ),
    ("Казанский федеральный университет", "КФУ", "Республика Татарстан", "1655018018"),
]

DEMO_CONTACTS = [
    ("МГТУ им. Н.Э. Баумана", "Соколова Анна Викторовна", "Начальник учебного управления"),
    (
        "Санкт-Петербургский политехнический университет",
        "Орлов Дмитрий Петрович",
        "Проректор по ИТ",
    ),
]

DEMO_INTERACTIONS = [
    ("МГТУ им. Н.Э. Баумана", "DevOps", "Astra Linux Special Edition", "ДЛ-2026/001", 3),
    ("МГТУ им. Н.Э. Баумана", "QA", "Альт Рабочая станция", "ДЛ-2026/002", 1),
    (
        "Санкт-Петербургский политехнический университет",
        "Data Science",
        "Postgres Pro Enterprise",
        "ДЛ-2026/003",
        5,
    ),
]


async def seed() -> None:
    register_audit_listeners()
    scope = AccessScope.system()

    async with SessionFactory() as session:
        users = UserService(session, scope)
        created_users = {}
        for keycloak_id, full_name, email, role in DEMO_USERS:
            existing = await users.repo.find_by_keycloak_id(keycloak_id)
            if existing is None:
                existing = await users.create(
                    UserCreate(keycloak_id=keycloak_id, full_name=full_name, email=email, role=role)
                )
                logger.info("created user %s (%s)", full_name, role.value)
            created_users[role] = existing

        # Everything below is attributed to the seeded admin, so the audit log
        # of a fresh database is not full of anonymous system actions.
        admin = created_users[UserRole.ADMIN]
        ctx = AuditContext(actor_id=admin.id, actor_name=admin.full_name)

        with audit_context(ctx):
            # The 14-step base process must exist before any card is created:
            # new interactions join it automatically (SPEC-02 A2).
            workflow = await ensure_base_workflow(session, scope)
            logger.info("base workflow ready: %s", workflow.name)

            directions = ITDirectionService(session, scope)
            direction_by_name = {}
            for name, description in DEMO_DIRECTIONS:
                direction, created = await directions.find_or_create(name)
                if created:
                    direction.description = description
                direction_by_name[name] = direction

            vendors = VendorService(session, scope)
            vendor_by_name = {}
            for name in DEMO_VENDORS:
                vendor, _ = await vendors.find_or_create(name)
                vendor_by_name[name] = vendor

            products = ITProductService(session, scope)
            product_by_name = {}
            for name, vendor_name, direction_names in DEMO_PRODUCTS:
                product, _ = await products.find_or_create(
                    name, vendor_id=vendor_by_name[vendor_name].id
                )
                for direction_name in direction_names:
                    direction = direction_by_name[direction_name]
                    if direction not in product.directions:
                        product.directions.append(direction)
                product_by_name[name] = product

            universities = UniversityService(session, scope)
            university_by_name = {}
            for name, short_name, region, inn in DEMO_UNIVERSITIES:
                university, created = await universities.find_or_create(name)
                if created:
                    university.short_name = short_name
                    university.region = region
                    university.inn = inn
                university_by_name[name] = university

            contacts = UniversityContactService(session, scope)
            for university_name, full_name, position in DEMO_CONTACTS:
                contact, created = await contacts.find_or_create(
                    university_by_name[university_name].id, full_name
                )
                if created:
                    contact.position = position

            interactions = InteractionService(session, scope)
            for university_name, direction_name, product_name, contract, years in DEMO_INTERACTIONS:
                await interactions.upsert(
                    university_id=university_by_name[university_name].id,
                    it_direction_id=direction_by_name[direction_name].id,
                    it_product_id=product_by_name[product_name].id,
                    values={
                        "contract_number": contract,
                        "license_signed_at": dt.date(2026, 2, 17),
                        "license_years": years,
                        "transfer_status": "Передано в вуз",
                        "responsible_user_id": created_users[UserRole.USER].id,
                    },
                )

            await session.commit()

            # The `user` role only sees what it is assigned to — give the demo
            # KAM one university so the visibility rule is observable.
            assignments = AssignmentService(session, scope)
            bauman = university_by_name["МГТУ им. Н.Э. Баумана"]
            existing_assignment = await assignments.repo.find_overlapping(
                bauman.id, dt.date(2026, 1, 1), None
            )
            if existing_assignment is None:
                await assignments.create(
                    bauman.id,
                    AssignmentCreate(
                        user_id=created_users[UserRole.USER].id,
                        assigned_from=dt.date(2026, 1, 1),
                        assigned_to=None,
                    ),
                )
                logger.info("assigned КАМ to %s", bauman.name)

    await dispose_engine()
    logger.info("seed finished")


def main() -> None:
    configure_logging()
    asyncio.run(seed())


if __name__ == "__main__":
    main()
