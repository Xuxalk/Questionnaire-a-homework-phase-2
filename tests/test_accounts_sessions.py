from fastapi.testclient import TestClient
from backend.app.main import create_app


def create_account(client: TestClient, username: str = "alice") -> dict:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "Correct horse 42!"},
    )
    assert response.status_code == 201
    return response.json()


def test_login_creates_signed_session_and_me_requires_authentication(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    account = create_account(client, "LoginUser")

    anonymous_me = client.get("/api/auth/me")
    assert anonymous_me.status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"username": "  loginuser ", "password": "Correct horse 42!"},
    )
    assert login.status_code == 200
    assert login.json()["id"] == account["id"]
    assert "password_hash" not in login.json()
    cookie = client.cookies.get("survey_session")
    assert cookie
    set_cookie = login.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "max-age=604800" in set_cookie

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "LoginUser"


def test_invalid_login_has_generic_error(authenticated_api_client: TestClient) -> None:
    client = authenticated_api_client
    create_account(client)

    response = client.post(
        "/api/auth/login",
        json={"username": "ALICE", "password": "wrong password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_logout_clears_session(authenticated_api_client: TestClient) -> None:
    client = authenticated_api_client
    create_account(client)
    logged_in = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "Correct horse 42!"},
    )
    assert logged_in.status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    logout = client.post("/api/auth/logout")

    assert logout.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_cross_origin_authentication_and_mutations_are_rejected(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    create_account(client, "csrf-owner")

    cross_origin_login = client.post(
        "/api/auth/login",
        json={"username": "csrf-owner", "password": "Correct horse 42!"},
        headers={
            "Origin": "https://attacker.example",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert cross_origin_login.status_code == 403
    assert client.cookies.get("survey_session") is None

    assert client.post(
        "/api/auth/login",
        json={"username": "csrf-owner", "password": "Correct horse 42!"},
    ).status_code == 200

    cross_site_create = client.post(
        "/api/surveys",
        json={
            "title": "Must not be created cross-site",
            "description": "",
            "anonymous": True,
            "allow_multiple_submissions": False,
        },
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert cross_site_create.status_code == 403
    assert client.get("/api/surveys").json() == []

    same_origin_create = client.post(
        "/api/surveys",
        json={
            "title": "Same origin succeeds",
            "description": "",
            "anonymous": True,
            "allow_multiple_submissions": False,
        },
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert same_origin_create.status_code == 201


def test_login_is_disabled_without_a_session_secret(
    isolated_test_database, monkeypatch
) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    application = create_app(
        database=isolated_test_database,
        use_environment_database=False,
    )
    with TestClient(application) as client:
        create_account(client)
        response = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "Correct horse 42!"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Session authentication is not configured"
