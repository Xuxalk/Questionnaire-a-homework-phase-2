from fastapi.testclient import TestClient

from backend.app.main import create_app

app = create_app(use_environment_database=False)


def test_application_starts_and_health_endpoint_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_frontend_shell_and_assets_are_served_by_fastapi() -> None:
    with TestClient(app) as client:
        page = client.get("/")
        stylesheet = client.get("/assets/css/app.css")
        script = client.get("/assets/js/app.js")

    assert page.status_code == 200
    assert "问卷集" in page.text
    assert stylesheet.status_code == 200
    assert ".survey-grid" in stylesheet.text
    assert script.status_code == 200
    assert "async function loadDashboard" in script.text
