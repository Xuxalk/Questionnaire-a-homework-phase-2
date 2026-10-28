import os
from typing import Iterator
from uuid import uuid4

import pytest
from pymongo import MongoClient
from fastapi.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture(scope="session")
def mongodb_test_client() -> Iterator[MongoClient]:
    test_uri = os.getenv("MONGODB_TEST_URI")
    if not test_uri:
        pytest.skip("Set MONGODB_TEST_URI to run MongoDB integration tests")

    client = MongoClient(test_uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    try:
        client.admin.command("ping")
    except Exception:
        client.close()
        pytest.fail("MONGODB_TEST_URI did not connect to a MongoDB test server")

    yield client
    client.close()


@pytest.fixture
def isolated_test_database(mongodb_test_client: MongoClient):
    database_name = "survey_test_{}".format(uuid4().hex)
    database = mongodb_test_client[database_name]
    try:
        yield database
    finally:
        # This fixture drops only the unique database name it created above.
        mongodb_test_client.drop_database(database_name)


@pytest.fixture
def api_application(isolated_test_database):
    return create_app(
        database=isolated_test_database,
        session_secret_key="test-only-session-secret-key-0123456789",
        session_cookie_secure=False,
    )


@pytest.fixture
def authenticated_api_client(api_application):
    with TestClient(api_application) as client:
        yield client
