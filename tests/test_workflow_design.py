"""Design-time workflow: base process, versioning, stages, transitions, graph."""

from __future__ import annotations

import pytest

from app.core.errors import (
    DuplicateEntityError,
    ValidationError,
    WorkflowVersionLockedError,
)
from app.models.enums import WorkflowVersionStatus
from app.services.workflow import WorkflowService
from app.services.workflow_presets import BASE_WORKFLOW_STAGES, ensure_base_workflow


async def test_base_workflow_has_all_fourteen_steps(session, scope):
    """WF-BASE: предзаполнен, опубликован и является workflow по умолчанию (A2)."""
    workflow = await ensure_base_workflow(session, scope)
    assert workflow.is_default is True

    service = WorkflowService(session, scope)
    versions = await service.list_versions(workflow.id)
    assert len(versions) == 1
    assert versions[0].status == WorkflowVersionStatus.PUBLISHED

    stages = await service.list_stages(versions[0].id)
    assert len(stages) == 14
    assert [stage.code for stage in stages] == [code for code, _ in BASE_WORKFLOW_STAGES]
    assert stages[0].is_initial is True
    assert stages[-1].is_terminal is True


async def test_base_workflow_transitions_allow_forward_back_and_skip(session, scope):
    workflow = await ensure_base_workflow(session, scope)
    service = WorkflowService(session, scope)
    version = (await service.list_versions(workflow.id))[0]
    stages = {stage.code: stage for stage in await service.list_stages(version.id)}
    transitions = await service.list_transitions(version.id)

    pairs = {(item.from_stage_id, item.to_stage_id) for item in transitions}
    assert (stages["WF-01"].id, stages["WF-02"].id) in pairs, "шаг вперёд"
    assert (stages["WF-02"].id, stages["WF-01"].id) in pairs, "возврат на шаг назад"
    # WF-05 отмечен в ТЗ как опциональный — маршрут разрешает его пропустить.
    assert (stages["WF-04"].id, stages["WF-06"].id) in pairs


async def test_ensure_base_workflow_is_idempotent(session, scope):
    first = await ensure_base_workflow(session, scope)
    second = await ensure_base_workflow(session, scope)
    assert first.id == second.id

    service = WorkflowService(session, scope)
    _, total = await service.list()
    assert total == 1


async def test_stage_can_be_renamed_on_a_published_version(session, scope):
    """FR-03: переименование статуса разрешено и в опубликованной версии (A4)."""
    workflow = await ensure_base_workflow(session, scope)
    service = WorkflowService(session, scope)
    version = (await service.list_versions(workflow.id))[0]
    stage = (await service.list_stages(version.id))[0]

    renamed = await service.rename_stage(
        stage.id, {"name": "Поиск ответственного лица", "description": "уточнили формулировку"}
    )
    assert renamed.name == "Поиск ответственного лица"
    assert renamed.id == stage.id, "идентификатор не меняется — карточки не ломаются"


async def test_structural_edit_of_published_version_is_refused(session, scope):
    workflow = await ensure_base_workflow(session, scope)
    service = WorkflowService(session, scope)
    version = (await service.list_versions(workflow.id))[0]
    stages = await service.list_stages(version.id)

    with pytest.raises(WorkflowVersionLockedError):
        await service.add_stage(version.id, name="Лишний этап")

    with pytest.raises(WorkflowVersionLockedError):
        await service.update_stage_structure(stages[0].id, {"order_index": 999})

    with pytest.raises(WorkflowVersionLockedError):
        await service.delete_stage(stages[-1].id)

    with pytest.raises(WorkflowVersionLockedError):
        await service.add_transition(
            version.id, from_stage_id=stages[0].id, to_stage_id=stages[5].id
        )


async def test_rename_endpoint_refuses_structural_fields(session, scope):
    workflow = await ensure_base_workflow(session, scope)
    service = WorkflowService(session, scope)
    version = (await service.list_versions(workflow.id))[0]
    stage = (await service.list_stages(version.id))[0]

    with pytest.raises(WorkflowVersionLockedError):
        await service.rename_stage(stage.id, {"is_initial": False})


async def test_clone_produces_an_independent_draft(session, scope):
    """A6: копия получает новые id этапов, поэтому правка её не трогает оригинал."""
    workflow = await ensure_base_workflow(session, scope)
    service = WorkflowService(session, scope)
    published = (await service.list_versions(workflow.id))[0]

    draft = await service.create_draft(workflow.id, clone_from_id=published.id)
    assert draft.status == WorkflowVersionStatus.DRAFT
    assert draft.version == published.version + 1

    original_stages = await service.list_stages(published.id)
    cloned_stages = await service.list_stages(draft.id)
    assert len(cloned_stages) == len(original_stages)
    assert [s.code for s in cloned_stages] == [s.code for s in original_stages]
    assert {s.id for s in cloned_stages}.isdisjoint({s.id for s in original_stages})

    original_transitions = await service.list_transitions(published.id)
    cloned_transitions = await service.list_transitions(draft.id)
    assert len(cloned_transitions) == len(original_transitions)


async def test_publishing_archives_the_previous_version(session, scope):
    workflow = await ensure_base_workflow(session, scope)
    service = WorkflowService(session, scope)
    first = (await service.list_versions(workflow.id))[0]

    draft = await service.create_draft(workflow.id, clone_from_id=first.id)
    await service.publish(draft.id)

    versions = {item.version: item.status for item in await service.list_versions(workflow.id)}
    assert versions[first.version] == WorkflowVersionStatus.ARCHIVED
    assert versions[draft.version] == WorkflowVersionStatus.PUBLISHED


async def test_publishing_requires_stages_and_an_entry_point(session, scope):
    service = WorkflowService(session, scope)
    workflow = await service.create(name="Пустой процесс")
    draft = await service.create_draft(workflow.id)

    with pytest.raises(ValidationError):
        await service.publish(draft.id)

    await service.add_stage(draft.id, name="Этап без начала")
    with pytest.raises(ValidationError):
        await service.publish(draft.id)


async def test_only_one_initial_stage_survives(session, scope):
    service = WorkflowService(session, scope)
    workflow = await service.create(name="Процесс с двумя началами")
    draft = await service.create_draft(workflow.id)

    first = await service.add_stage(draft.id, name="Первый", is_initial=True)
    second = await service.add_stage(draft.id, name="Второй", is_initial=True)

    stages = {stage.id: stage for stage in await service.list_stages(draft.id)}
    assert stages[second.id].is_initial is True
    assert stages[first.id].is_initial is False


async def test_transition_rules_are_validated(session, scope):
    service = WorkflowService(session, scope)
    workflow = await service.create(name="Процесс для проверки переходов")
    draft = await service.create_draft(workflow.id)
    first = await service.add_stage(draft.id, name="Первый", is_initial=True)
    second = await service.add_stage(draft.id, name="Второй")

    with pytest.raises(ValidationError):
        await service.add_transition(draft.id, from_stage_id=first.id, to_stage_id=first.id)

    await service.add_transition(draft.id, from_stage_id=first.id, to_stage_id=second.id)
    with pytest.raises(DuplicateEntityError):
        await service.add_transition(draft.id, from_stage_id=first.id, to_stage_id=second.id)


async def test_duplicate_workflow_name_is_refused(session, scope):
    service = WorkflowService(session, scope)
    await service.create(name="Процесс")
    with pytest.raises(DuplicateEntityError):
        await service.create(name="процесс")


async def test_graph_returns_stages_transitions_and_counts(session, scope):
    """A7: одним ответом — всё, что нужно для отрисовки схемы."""
    workflow = await ensure_base_workflow(session, scope)
    graph = await WorkflowService(session, scope).build_published_graph(workflow.id)

    assert graph["workflow"].id == workflow.id
    assert len(graph["stages"]) == 14
    assert len(graph["transitions"]) == 27  # 13 вперёд + 13 назад + 1 пропуск
    assert graph["cards_per_stage"] == {}, "карточек ещё нет"
