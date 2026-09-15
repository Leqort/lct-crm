"""The customer's base workflow, WF-BASE (SPEC-02 A2).

The 14 steps are a product requirement, not a test fixture, so they live in the
application rather than in a seed script: a fresh installation must come with
the process already drawn, and it must stay editable afterwards (FR-07).

Creating it is idempotent — running the seed twice does not produce a second
copy, and re-running it never touches a process someone has already edited.
"""

from __future__ import annotations

from itertools import pairwise

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessScope
from app.models.workflow import Workflow
from app.services.workflow import WorkflowService

BASE_WORKFLOW_NAME = "Базовый процесс работы с вузом"

# (code, name) in order. Wording follows the ТЗ verbatim.
BASE_WORKFLOW_STAGES: tuple[tuple[str, str], ...] = (
    ("WF-01", "Поиск контактов ответственного в вузе"),
    ("WF-02", "Коммуникация и уточнение актуальности программ по ИТ-направлениям"),
    ("WF-03", "Организация встречи с представителями вуза"),
    ("WF-04", "Обмен пакетом документов для подписания"),
    ("WF-05", "Корректировка документов перед подписанием"),
    ("WF-06", "Подписание документов"),
    ("WF-07", "Передача материалов, лицензии и документации в вуз"),
    ("WF-08", "Сопровождение внедрения ИТ-продуктов в вузе"),
    ("WF-09", "Обучение преподавателей"),
    ("WF-10", "Актуализация учебной программы по ИТ-направлению"),
    ("WF-11", "Ведение занятий"),
    ("WF-12", "Актуализация документации по продукту и материалам"),
    ("WF-13", "Повышение квалификации преподавателей"),
    ("WF-14", "Контроль за исполнением каждого этапа"),
)

# WF-05 is marked optional in the ТЗ, so the route also allows going straight
# from the document exchange to signing.
OPTIONAL_SKIPS: tuple[tuple[str, str], ...] = (("WF-04", "WF-06"),)


async def ensure_base_workflow(session: AsyncSession, scope: AccessScope | None = None) -> Workflow:
    """Create WF-BASE with a published version, or return the existing one."""
    service = WorkflowService(session, scope or AccessScope.system())

    from app.services.text import normalize_name

    existing = await service.repo.find_by_normalized_name(normalize_name(BASE_WORKFLOW_NAME))
    if existing is not None:
        return existing

    workflow = await service.create(
        name=BASE_WORKFLOW_NAME,
        description=(
            "Эталонный цикл взаимодействия с вузом из 14 шагов. "
            "Предзаполнен и доступен для изменения: правки вносятся копированием "
            "в новую версию."
        ),
        is_default=True,
    )
    draft = await service.create_draft(workflow.id)

    stages_by_code = {}
    for index, (code, name) in enumerate(BASE_WORKFLOW_STAGES):
        stage = await service.add_stage(
            draft.id,
            name=name,
            code=code,
            order_index=(index + 1) * 10,
            is_initial=index == 0,
            is_terminal=index == len(BASE_WORKFLOW_STAGES) - 1,
            is_final_success=index == len(BASE_WORKFLOW_STAGES) - 1,
        )
        stages_by_code[code] = stage

    codes = [code for code, _ in BASE_WORKFLOW_STAGES]
    for current, following in pairwise(codes):
        await service.add_transition(
            draft.id,
            from_stage_id=stages_by_code[current].id,
            to_stage_id=stages_by_code[following].id,
            name="Дальше",
        )
        # Работа возвращается на шаг назад чаще, чем хотелось бы: без обратного
        # перехода единственным способом исправить ошибку был бы новый workflow.
        await service.add_transition(
            draft.id,
            from_stage_id=stages_by_code[following].id,
            to_stage_id=stages_by_code[current].id,
            name="Вернуть на предыдущий этап",
        )

    for source, target in OPTIONAL_SKIPS:
        await service.add_transition(
            draft.id,
            from_stage_id=stages_by_code[source].id,
            to_stage_id=stages_by_code[target].id,
            name="Пропустить корректировку документов",
        )

    await service.publish(draft.id)
    return workflow
