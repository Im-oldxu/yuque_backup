from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

os.environ.setdefault("APP_MASTER_KEY", "00" * 32)
os.environ.setdefault("DATA_ROOT", str(Path(os.environ.get("TEMP", ".")) / "yuque-openapi-test"))

from app.api.router import router
from app.modules.repositories.router import router as repositories_router

app = FastAPI()
app.include_router(router)

OperationKey = tuple[str, str]

EXPECTED_RESPONSES: dict[OperationKey, set[int]] = {
    ("GET", "/health/live"): {200, 500},
    ("GET", "/health/ready"): {200, 500, 503},
    ("GET", "/api/v1/system/initialization"): {200, 500},
    ("POST", "/api/v1/system/initialize"): {201, 400, 403, 409, 422, 500},
    ("POST", "/api/v1/auth/login"): {200, 401, 403, 422, 429, 500},
    ("GET", "/api/v1/auth/me"): {200, 401, 500},
    ("POST", "/api/v1/auth/logout"): {204, 401, 403, 422, 500},
    ("PUT", "/api/v1/auth/password"): {204, 400, 401, 403, 422, 500},
    ("GET", "/api/v1/operations/{operation_id}"): {200, 401, 404, 422, 500},
    ("GET", "/api/v1/credentials"): {200, 401, 422, 500},
    ("POST", "/api/v1/credentials"): {202, 401, 403, 409, 422, 500},
    ("GET", "/api/v1/credentials/{credential_id}"): {200, 401, 404, 422, 500},
    ("PATCH", "/api/v1/credentials/{credential_id}"): {200, 401, 403, 404, 409, 422, 500},
    ("POST", "/api/v1/credentials/{credential_id}/verify"): {
        202,
        401,
        403,
        404,
        409,
        422,
        500,
    },
    ("POST", "/api/v1/credentials/{credential_id}/discover-repositories"): {
        202,
        401,
        403,
        404,
        409,
        422,
        500,
    },
    ("POST", "/api/v1/credentials/{credential_id}/enable"): {
        200,
        401,
        403,
        404,
        409,
        422,
        500,
    },
    ("POST", "/api/v1/credentials/{credential_id}/disable"): {200, 401, 403, 404, 422, 500},
    ("DELETE", "/api/v1/credentials/{credential_id}"): {204, 401, 403, 404, 422, 500},
    ("GET", "/api/v1/repositories"): {200, 401, 422, 500},
    ("GET", "/api/v1/repositories/{repository_id}"): {200, 401, 404, 422, 500},
    ("PATCH", "/api/v1/repositories/{repository_id}/selection"): {200, 401, 403, 404, 422, 500},
    ("PUT", "/api/v1/repositories/{repository_id}/primary-credential"): {
        200,
        401,
        403,
        404,
        409,
        422,
        500,
    },
    ("GET", "/api/v1/repositories/{repository_id}/toc"): {200, 401, 404, 422, 500},
    ("GET", "/api/v1/documents"): {200, 401, 422, 500},
    ("GET", "/api/v1/search"): {200, 401, 422, 500},
    ("GET", "/api/v1/documents/{document_id}"): {200, 401, 404, 422, 500},
    ("GET", "/api/v1/documents/{document_id}/versions"): {200, 401, 404, 422, 500},
    ("GET", "/api/v1/documents/{document_id}/versions/{version_id}"): {
        200,
        401,
        404,
        422,
        500,
    },
    ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/assets"): {
        200,
        401,
        404,
        422,
        500,
    },
    ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/issues"): {
        200,
        401,
        404,
        422,
        500,
    },
    ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/preview"): {
        200,
        206,
        401,
        404,
        409,
        410,
        422,
        500,
        503,
    },
    ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/downloads/raw-response"): {
        200,
        206,
        401,
        404,
        410,
        422,
        500,
        503,
    },
    ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/downloads/raw-body"): {
        200,
        206,
        401,
        404,
        410,
        422,
        500,
        503,
    },
    ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/downloads/offline-html"): {
        200,
        206,
        401,
        404,
        409,
        410,
        422,
        500,
        503,
    },
    ("GET", "/api/v1/assets/{asset_id}/content"): {200, 206, 401, 404, 410, 422, 500, 503},
    ("GET", "/api/v1/assets/{asset_id}/download"): {
        200,
        206,
        401,
        404,
        410,
        422,
        500,
        503,
    },
    ("POST", "/api/v1/backup-jobs"): {202, 400, 401, 403, 404, 409, 422, 500},
    ("POST", "/api/v1/backup-jobs/estimate"): {200, 401, 403, 404, 409, 422, 500},
    ("GET", "/api/v1/backup-jobs"): {200, 401, 422, 500},
    ("GET", "/api/v1/backup-jobs/{job_id}"): {200, 401, 404, 422, 500},
    ("GET", "/api/v1/backup-jobs/{job_id}/subtasks"): {200, 401, 404, 422, 500},
    ("GET", "/api/v1/backup-jobs/{job_id}/issues"): {200, 401, 404, 422, 500},
    ("POST", "/api/v1/backup-jobs/{job_id}/cancel"): {202, 401, 403, 404, 409, 422, 500},
    ("POST", "/api/v1/backup-jobs/{job_id}/rerun"): {202, 400, 401, 403, 404, 409, 422, 500},
    ("GET", "/api/v1/dashboard/summary"): {200, 401, 500, 503},
    ("GET", "/api/v1/settings/schedule"): {200, 401, 500, 503},
    ("PUT", "/api/v1/settings/schedule"): {200, 401, 403, 422, 500, 503},
    ("GET", "/api/v1/settings/retention"): {200, 401, 500, 503},
    ("PUT", "/api/v1/settings/retention"): {200, 401, 403, 422, 500, 503},
    ("GET", "/api/v1/settings/storage"): {200, 401, 500, 503},
    ("PUT", "/api/v1/settings/storage-limit"): {200, 401, 403, 422, 500, 503},
    ("GET", "/api/v1/deletion-tombstones"): {200, 401, 422, 500},
    ("GET", "/api/v1/deletion-tombstones/{tombstone_id}"): {200, 401, 404, 422, 500},
}

PUBLIC_OPERATIONS = {
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/v1/system/initialization"),
    ("POST", "/api/v1/system/initialize"),
    ("POST", "/api/v1/auth/login"),
}

CSRF_OPERATIONS = {
    ("POST", "/api/v1/auth/logout"),
    ("PUT", "/api/v1/auth/password"),
    ("POST", "/api/v1/credentials"),
    ("PATCH", "/api/v1/credentials/{credential_id}"),
    ("POST", "/api/v1/credentials/{credential_id}/verify"),
    ("POST", "/api/v1/credentials/{credential_id}/discover-repositories"),
    ("POST", "/api/v1/credentials/{credential_id}/enable"),
    ("POST", "/api/v1/credentials/{credential_id}/disable"),
    ("DELETE", "/api/v1/credentials/{credential_id}"),
    ("PATCH", "/api/v1/repositories/{repository_id}/selection"),
    ("PUT", "/api/v1/repositories/{repository_id}/primary-credential"),
    ("POST", "/api/v1/backup-jobs"),
    ("POST", "/api/v1/backup-jobs/estimate"),
    ("POST", "/api/v1/backup-jobs/{job_id}/cancel"),
    ("POST", "/api/v1/backup-jobs/{job_id}/rerun"),
    ("PUT", "/api/v1/settings/schedule"),
    ("PUT", "/api/v1/settings/retention"),
    ("PUT", "/api/v1/settings/storage-limit"),
}

IDEMPOTENCY_OPERATIONS = {
    ("POST", "/api/v1/system/initialize"),
    ("POST", "/api/v1/backup-jobs"),
    ("POST", "/api/v1/backup-jobs/{job_id}/rerun"),
}


def _operations(schema: dict[str, Any]) -> dict[OperationKey, dict[str, Any]]:
    return {
        (method.upper(), path): operation
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    }


def _parameter(operation: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [item for item in operation.get("parameters", []) if item.get("name") == name]


def test_openapi_exact_routes_statuses_errors_and_request_id_headers() -> None:
    schema = app.openapi()
    operations = _operations(schema)
    assert set(operations) == set(EXPECTED_RESPONSES)
    assert "ErrorResponse" in schema["components"]["schemas"]
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "ValidationError" not in schema["components"]["schemas"]

    for key, expected_statuses in EXPECTED_RESPONSES.items():
        responses = operations[key]["responses"]
        assert {int(status_code) for status_code in responses} == expected_statuses
        for status_code, response in responses.items():
            assert "X-Request-ID" in response["headers"], (key, status_code)
            if int(status_code) >= 400:
                error_schema = response["content"]["application/json"]["schema"]
                assert error_schema == {"$ref": "#/components/schemas/ErrorResponse"}


def test_openapi_cookie_auth_csrf_and_idempotency_headers() -> None:
    operations = _operations(app.openapi())
    for key, operation in operations.items():
        if key in PUBLIC_OPERATIONS:
            assert not operation.get("security")
        else:
            assert operation["security"] == [{"APIKeyCookie": []}]

        csrf = _parameter(operation, "X-CSRF-Token")
        assert bool(csrf) is (key in CSRF_OPERATIONS)
        if csrf:
            assert len(csrf) == 1
            assert csrf[0]["in"] == "header"
            assert csrf[0]["required"] is True
            assert csrf[0]["schema"] == {"type": "string"}

        idempotency = _parameter(operation, "Idempotency-Key")
        assert bool(idempotency) is (key in IDEMPOTENCY_OPERATIONS)
        if idempotency:
            assert len(idempotency) == 1
            assert idempotency[0]["in"] == "header"
            assert idempotency[0]["required"] is True
            assert idempotency[0]["schema"] == {"type": "string", "format": "uuid"}

        assert not _parameter(operation, "yb_csrf")


def test_openapi_request_and_response_field_contracts() -> None:
    schema = app.openapi()
    components = schema["components"]["schemas"]

    patch = components["CredentialPatch"]
    assert patch["minProperties"] == 1
    assert "required" not in patch
    for name in ("name", "base_url", "token"):
        field = patch["properties"][name]
        assert field["type"] == "string"
        assert "anyOf" not in field
        assert "default" not in field

    initialize_username = components["InitializeRequest"]["properties"]["username"]
    assert initialize_username["minLength"] == 3
    assert initialize_username["maxLength"] == 64

    for model_name in ("CredentialCreate", "CredentialPatch", "CredentialResponse"):
        base_url = components[model_name]["properties"]["base_url"]
        assert base_url["format"] == "uri"
        assert base_url["pattern"].startswith("^https://")

    assert components["CredentialScope"]["properties"]["credential_id"]["format"] == "uuid"
    assert components["RepositoryScope"]["properties"]["repository_id"]["format"] == "uuid"
    repositories_scope = components["RepositoriesScope"]["properties"]
    assert repositories_scope["credential_id"]["format"] == "uuid"
    assert repositories_scope["repository_ids"]["items"]["format"] == "uuid"
    assert components["PrimaryCredentialRequest"]["properties"]["credential_id"]["format"] == "uuid"
    assert "id" not in components["AssetReferenceResponse"]["properties"]

    local_id_fields = {
        "id",
        "active_operation_id",
        "asset_id",
        "cleanup_job_id",
        "credential_id",
        "document_id",
        "latest_version_id",
        "primary_credential_id",
        "repository_id",
        "source_job_id",
    }
    for model_name, model_schema in components.items():
        for field_name, field_schema in model_schema.get("properties", {}).items():
            if field_name not in local_id_fields:
                continue
            variants = field_schema.get("anyOf", [field_schema])
            assert any(variant.get("format") == "uuid" for variant in variants), (
                model_name,
                field_name,
            )

    for model_name in ("BackupJobResponse", "DashboardJobResponse"):
        scope = components[model_name]["properties"]["scope"]
        assert scope["discriminator"]["propertyName"] == "type"
        assert set(scope["discriminator"]["mapping"]) == {
            "all",
            "credential",
            "repositories",
            "repository",
        }
        assert {item["$ref"].rsplit("/", 1)[-1] for item in scope["oneOf"]} == {
            "AllScope",
            "CredentialScope",
            "RepositoriesScope",
            "RepositoryScope",
        }

    search_repositories = components["SearchResponse"]["properties"]["repositories"]
    assert search_repositories["items"] == {
        "$ref": "#/components/schemas/SearchRepositoryResponse"
    }
    assert set(components["SearchRepositoryResponse"]["required"]) == {
        "id",
        "name",
        "namespace",
        "selected",
    }

    repository_required = set(components["RepositoryResponse"]["required"])
    assert {
        "slug",
        "namespace",
        "primary_credential_id",
        "last_success_at",
        "content_updated_at",
    } <= repository_required
    assert "credentials" not in components["RepositoryResponse"]["properties"]
    repository_detail = components["RepositoryDetailResponse"]
    assert repository_detail["properties"]["credentials"]["type"] == "array"
    assert "credentials" in repository_detail["required"]

    repository_route = next(
        route
        for route in repositories_router.routes
        if isinstance(route, APIRoute) and route.path == "/api/v1/repositories" and "GET" in route.methods
    )
    assert not repository_route.response_model_exclude_none

    operations = _operations(schema)
    repository_detail_response = operations[
        ("GET", "/api/v1/repositories/{repository_id}")
    ]["responses"]["200"]["content"]["application/json"]["schema"]
    assert repository_detail_response == {
        "$ref": "#/components/schemas/RepositoryDetailResponse"
    }


def test_openapi_file_response_media_types_and_headers() -> None:
    operations = _operations(app.openapi())
    expected_media_types = {
        ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/preview"): {"text/html"},
        ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/downloads/raw-response"): {
            "application/json"
        },
        ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/downloads/raw-body"): {
            "text/plain",
            "text/html",
            "application/json",
        },
        ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/downloads/offline-html"): {
            "text/html"
        },
        ("GET", "/api/v1/assets/{asset_id}/content"): {"application/octet-stream", "image/*"},
        ("GET", "/api/v1/assets/{asset_id}/download"): {"application/octet-stream", "*/*"},
    }
    for key, media_types in expected_media_types.items():
        response = operations[key]["responses"]["200"]
        assert set(response["content"]) == media_types
        assert "Accept-Ranges" in response["headers"]
        partial = operations[key]["responses"]["206"]
        assert set(partial["content"]) == media_types
        assert "Content-Range" in partial["headers"]

    preview = operations[
        ("GET", "/api/v1/documents/{document_id}/versions/{version_id}/preview")
    ]["responses"]["200"]
    assert "Content-Security-Policy" in preview["headers"]

    for key in expected_media_types:
        if "/downloads/" in key[1] or key[1].endswith("/download"):
            assert "Content-Disposition" in operations[key]["responses"]["200"]["headers"]
