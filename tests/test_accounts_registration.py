from concurrent.futures import ThreadPoolExecutor

import pytest
from argon2 import PasswordHasher
from bson import ObjectId
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.modules.accounts.repository import UserRepository


@pytest.fixture
def registration_client(isolated_test_database):
    application = create_app(database=isolated_test_database)
    with TestClient(application) as client:
        yield client


def test_register_creates_user_and_returns_public_fields(
    registration_client: TestClient,
    isolated_test_database,
) -> None:
    response = registration_client.post(
        "/api/auth/register",
        json={"username": "  Alice  ", "password": "Correct horse 42!"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["username"] == "Alice"
    assert set(payload) == {"id", "username", "created_at", "updated_at"}
    assert ObjectId.is_valid(payload["id"])

    user = isolated_test_database["users"].find_one({"_id": ObjectId(payload["id"])})
    assert user is not None
    assert user["username"] == "Alice"
    assert user["username_normalized"] == "alice"
    assert user["created_at"] == user["updated_at"]

    indexes = list(isolated_test_database["users"].list_indexes())
    normalized_index = next(
        index for index in indexes if index["name"] == "uq_users_username_normalized"
    )
    assert normalized_index["unique"] is True


def test_duplicate_username_returns_conflict(registration_client: TestClient) -> None:
    first = registration_client.post(
        "/api/auth/register",
        json={"username": "sora", "password": "Correct horse 42!"},
    )
    second = registration_client.post(
        "/api/auth/register",
        json={"username": "sora", "password": "Another secure 43!"},
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "Username is already registered"


def test_username_uniqueness_ignores_case_and_outer_whitespace(
    registration_client: TestClient,
    isolated_test_database,
) -> None:
    first = registration_client.post(
        "/api/auth/register",
        json={"username": "  MiXeD  ", "password": "Correct horse 42!"},
    )
    second = registration_client.post(
        "/api/auth/register",
        json={"username": "mixed", "password": "Another secure 43!"},
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert isolated_test_database["users"].count_documents({}) == 1


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"username": "", "password": "Correct horse 42!"}, id="empty-username"),
        pytest.param({"username": "   ", "password": "Correct horse 42!"}, id="blank-username"),
        pytest.param({"username": "alice", "password": "short"}, id="short-password"),
        pytest.param({"username": 42, "password": "Correct horse 42!"}, id="wrong-type"),
        pytest.param({"username": "alice"}, id="missing-password"),
    ],
)
def test_invalid_registration_input_returns_unprocessable_entity(
    registration_client: TestClient,
    payload: dict,
) -> None:
    response = registration_client.post("/api/auth/register", json=payload)

    assert response.status_code == 422


def test_password_is_stored_as_argon2id_hash_not_plaintext(
    registration_client: TestClient,
    isolated_test_database,
) -> None:
    password = "Correct horse 42!"
    response = registration_client.post(
        "/api/auth/register",
        json={"username": "hash-check", "password": password},
    )

    assert response.status_code == 201
    stored = isolated_test_database["users"].find_one(
        {"username_normalized": "hash-check"}
    )
    assert stored is not None
    assert stored["password_hash"] != password
    assert stored["password_hash"].startswith("$argon2id$")
    assert PasswordHasher().verify(stored["password_hash"], password)
    assert "password_hash" not in response.json()


def test_concurrent_same_username_has_one_success_and_one_conflict(
    isolated_test_database,
) -> None:
    UserRepository(isolated_test_database["users"]).ensure_indexes()

    def register_once() -> int:
        application = create_app(database=isolated_test_database)
        with TestClient(application) as client:
            response = client.post(
                "/api/auth/register",
                json={"username": "concurrent", "password": "Correct horse 42!"},
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: register_once(), range(2)))

    assert sorted(statuses) == [201, 409]
    assert isolated_test_database["users"].count_documents({}) == 1
