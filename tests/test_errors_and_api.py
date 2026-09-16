"""The single error contract (SPEC §10) and generic list behaviour (SPEC §7)."""

from __future__ import annotations

import uuid

from app.core.access import AccessScope
from app.schemas.university import UniversityCreate
from app.services.catalogs import UniversityService
from tests.conftest import auth


async def test_error_envelope_shape(session, client, admin_user):
    response = await client.get(f"/api/v1/universities/{uuid.uuid4()}", headers=auth(admin_user))
    assert response.status_code == 404
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["code"] == "NOT_FOUND"
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_validation_errors_use_the_same_envelope(session, client, manager_user):
    response = await client.post("/api/v1/universities", json={}, headers=auth(manager_user))
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"]


async def test_unknown_route_uses_the_same_envelope(client):
    response = await client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_bad_debug_user_is_access_denied(client):
    response = await client.get(
        "/api/v1/universities", headers={"X-Debug-User": "nobody@example.test"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


async def test_pagination_and_sorting(session, client, admin_user):
    service = UniversityService(session, AccessScope.system())
    for name in ("Альфа", "Бета", "Гамма"):
        await service.create(UniversityCreate(name=name))

    first_page = await client.get(
        "/api/v1/universities",
        params={"page": 1, "size": 2, "sort": "name"},
        headers=auth(admin_user),
    )
    body = first_page.json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert [item["name"] for item in body["items"]] == ["Альфа", "Бета"]

    descending = await client.get(
        "/api/v1/universities", params={"sort": "-name"}, headers=auth(admin_user)
    )
    assert next(item["name"] for item in descending.json()["items"]) == "Гамма"


async def test_unsupported_sort_field_is_a_clear_error(session, client, admin_user):
    response = await client.get(
        "/api/v1/universities", params={"sort": "secret"}, headers=auth(admin_user)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "allowed" in response.json()["error"]["details"]


async def test_page_size_is_capped(session, client, admin_user):
    response = await client.get(
        "/api/v1/universities", params={"size": 1000}, headers=auth(admin_user)
    )
    assert response.status_code == 422


async def test_search_matches_substring(session, client, admin_user):
    service = UniversityService(session, AccessScope.system())
    await service.create(UniversityCreate(name="МГТУ им. Баумана"))
    await service.create(UniversityCreate(name="СПбПУ"))

    response = await client.get(
        "/api/v1/universities", params={"search": "Бауман"}, headers=auth(admin_user)
    )
    assert response.json()["total"] == 1


async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_metrics_endpoint_exposes_prometheus_metrics(client):
    await client.get("/health")
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "crm_http_requests_total" in response.text
    assert 'path="/health"' in response.text


async def test_openapi_documents_every_endpoint(client):
    schema = (await client.get("/openapi.json")).json()
    operations = [
        operation
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "patch", "delete", "put"}
    ]
    assert operations
    missing = [
        operation.get("operationId")
        for operation in operations
        if not operation.get("summary") or not operation.get("description")
    ]
    assert missing == []
    assert "DebugUser" in schema["components"]["securitySchemes"]


async def test_me_endpoint_reports_role(session, client, kam_user):
    response = await client.get("/api/v1/users/me", headers=auth(kam_user))
    assert response.status_code == 200
    assert response.json()["role"] == "user"
    assert response.json()["full_name"] == kam_user.full_name


async def test_user_can_be_identified_by_email_or_keycloak_id(session, client, admin_user):
    by_email = await client.get(
        "/api/v1/users/me", headers={"X-Debug-User": admin_user.email or ""}
    )
    assert by_email.json()["id"] == str(admin_user.id)

    by_keycloak_id = await client.get(
        "/api/v1/users/me", headers={"X-Debug-User": admin_user.keycloak_id}
    )
    assert by_keycloak_id.json()["id"] == str(admin_user.id)


async def test_admin_can_create_user(session, client, admin_user):
    response = await client.post(
        "/api/v1/users",
        json={"keycloak_id": "new-1", "full_name": "Новый Сотрудник", "role": "manager"},
        headers=auth(admin_user),
    )
    assert response.status_code == 201
    assert response.json()["role"] == "manager"

    duplicate = await client.post(
        "/api/v1/users",
        json={"keycloak_id": "new-1", "full_name": "Ещё Один"},
        headers=auth(admin_user),
    )
    assert duplicate.status_code == 409
