from bson import ObjectId
from fastapi.testclient import TestClient
import pytest

from backend.app.modules.question_library.repository import QuestionLibraryRepository


PASSWORD = "Correct horse 42!"


def register_and_login(client: TestClient, username: str) -> None:
    created = client.post(
        "/api/auth/register", json={"username": username, "password": PASSWORD}
    )
    assert created.status_code == 201, created.text
    logged_in = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert logged_in.status_code == 200, logged_in.text


def login(client: TestClient, username: str) -> None:
    result = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert result.status_code == 200, result.text


def create_survey(client: TestClient, title: str) -> dict:
    response = client.post(
        "/api/surveys",
        json={
            "title": title,
            "description": "Sharing and usage test",
            "anonymous": True,
            "allow_multiple_submissions": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def question(prompt: str) -> dict:
    return {
        "type": "single_choice",
        "prompt": prompt,
        "required": True,
        "options": [{"label": "Yes"}, {"label": "No"}],
        "jump_rules": [],
    }


def create_question(client: TestClient, prompt: str = "Did this help?") -> dict:
    response = client.post("/api/question-library/questions", json=question(prompt))
    assert response.status_code == 201, response.text
    return response.json()


def share_url(family_id: str, version_id: str) -> str:
    return f"/api/question-library/questions/{family_id}/versions/{version_id}/shares"


def use_version(client: TestClient, survey_id: str, family_id: str, version_id: str):
    return client.post(
        f"/api/surveys/{survey_id}/questions/from-library",
        json={"source_question_id": family_id, "source_version_id": version_id},
    )


def test_shares_are_version_scoped_authorized_and_idempotent(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    assert client.get("/api/question-library/shares/received").status_code == 401

    register_and_login(client, "share-owner")
    first = create_question(client)
    family_id = first["family"]["id"]
    version_one = first["version"]
    version_two_response = client.post(
        f"/api/question-library/questions/{family_id}/versions",
        params={"parent_version_id": version_one["id"]},
        json=question("Did this help after the update?"),
    )
    assert version_two_response.status_code == 201, version_two_response.text
    version_two = version_two_response.json()
    version_three_response = client.post(
        f"/api/question-library/questions/{family_id}/versions",
        params={"parent_version_id": version_two["id"]},
        json=question("A private third version"),
    )
    assert version_three_response.status_code == 201
    version_three = version_three_response.json()
    client.post("/api/auth/logout")
    assert client.post(
        share_url(family_id, version_one["id"]),
        json={"recipient_username": "version-reader"},
    ).status_code == 401
    login(client, "share-owner")
    registered = client.post(
        "/api/auth/register",
        json={"username": "Version-Reader", "password": PASSWORD},
    )
    assert registered.status_code == 201, registered.text

    missing_version = client.post(
        share_url(family_id, str(ObjectId())),
        json={"recipient_username": "version-reader"},
    )
    assert missing_version.status_code == 404
    missing_user = client.post(
        share_url(family_id, version_one["id"]),
        json={"recipient_username": "unknown-user"},
    )
    assert missing_user.status_code == 404

    client.post("/api/auth/logout")
    login(client, "version-reader")
    non_owner_share = client.post(
        share_url(family_id, version_one["id"]),
        json={"recipient_username": "share-owner"},
    )
    assert non_owner_share.status_code == 404
    assert client.get(
        f"/api/question-library/questions/{family_id}/versions"
    ).status_code == 404
    assert client.get("/api/question-library/shares/received").json() == []

    client.post("/api/auth/logout")
    login(client, "share-owner")
    first_grant = client.post(
        share_url(family_id, version_one["id"]),
        json={"recipient_username": " VERSION-READER "},
    )
    assert first_grant.status_code == 200, first_grant.text
    assert first_grant.json()["status"] == "active"
    duplicate_grant = client.post(
        share_url(family_id, version_one["id"]),
        json={"recipient_username": "version-reader"},
    )
    assert duplicate_grant.status_code == 200
    assert duplicate_grant.json()["id"] == first_grant.json()["id"]
    second_grant = client.post(
        share_url(family_id, version_two["id"]),
        json={"recipient_username": "version-reader"},
    )
    assert second_grant.status_code == 200
    assert second_grant.json()["id"] != first_grant.json()["id"]
    grants = client.get(share_url(family_id, version_one["id"])).json()
    assert len(grants) == 1
    assert grants[0]["recipient_username"] == "Version-Reader"
    assert grants[0]["status"] == "active"

    client.post("/api/auth/logout")
    login(client, "version-reader")
    received = client.get("/api/question-library/shares/received")
    assert received.status_code == 200
    assert {item["version"]["id"] for item in received.json()} == {
        version_one["id"], version_two["id"]
    }
    # Shared views omit parent/restoration metadata and any other version IDs.
    assert all("parent_version_id" not in item["version"] for item in received.json())
    assert all("latest_version_id" not in item for item in received.json())

    target = create_survey(client, "Shared version copies")
    used_one = use_version(client, target["id"], family_id, version_one["id"])
    used_two = use_version(client, target["id"], family_id, version_two["id"])
    assert used_one.status_code == 201, used_one.text
    assert used_two.status_code == 201, used_two.text
    local_questions = used_two.json()["questions"]
    assert local_questions[0]["question_id"] != local_questions[1]["question_id"]
    assert all(not item["jump_rules"] for item in local_questions)
    denied_v3 = use_version(client, target["id"], family_id, version_three["id"])
    assert denied_v3.status_code == 404
    assert client.post(
        f"/api/question-library/questions/{family_id}/versions",
        json=question("Reader must not edit the source"),
    ).status_code == 404
    assert client.post(
        f"/api/question-library/questions/{family_id}/versions/{version_one['id']}/restore"
    ).status_code == 404


def test_revoke_is_idempotent_and_keeps_existing_published_data(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "revoke-owner")
    resource = create_question(client, "Should this stay available?")
    family_id = resource["family"]["id"]
    version = resource["version"]
    registered = client.post(
        "/api/auth/register", json={"username": "revoke-reader", "password": PASSWORD}
    )
    assert registered.status_code == 201
    assert client.post(
        share_url(family_id, version["id"]),
        json={"recipient_username": "revoke-reader"},
    ).status_code == 200

    client.post("/api/auth/logout")
    login(client, "revoke-reader")
    survey = create_survey(client, "Published copy survives revoke")
    inserted = use_version(client, survey["id"], family_id, version["id"])
    assert inserted.status_code == 201, inserted.text
    question_id = inserted.json()["questions"][0]["question_id"]
    assert client.post(f"/api/surveys/{survey['id']}/publish").status_code == 200

    started = client.post(f"/api/fill/{survey['id']}/responses")
    assert started.status_code == 201, started.text
    response_id = started.json()["id"]
    saved = client.put(
        f"/api/fill/{survey['id']}/responses/{response_id}/steps/{question_id}",
        json={
            "revision": 0,
            "answer": {"option_id": inserted.json()["questions"][0]["options"][0]["option_id"]},
        },
    )
    assert saved.status_code == 200, saved.text
    submitted = client.post(
        f"/api/fill/{survey['id']}/responses/{response_id}/submit",
        json={"revision": saved.json()["revision"]},
    )
    assert submitted.status_code == 200, submitted.text
    stats_before = client.get(f"/api/statistics/surveys/{survey['id']}")
    assert stats_before.status_code == 200
    assert stats_before.json()["submitted_count"] == 1

    client.post("/api/auth/logout")
    login(client, "revoke-owner")
    recipient_id = registered.json()["id"]
    revoke_path = (
        f"{share_url(family_id, version['id'])}/{recipient_id}"
    )
    first_revoke = client.delete(revoke_path)
    second_revoke = client.delete(revoke_path)
    assert first_revoke.status_code == 200, first_revoke.text
    assert second_revoke.status_code == 200, second_revoke.text
    assert first_revoke.json()["status"] == second_revoke.json()["status"] == "revoked"
    owner_status = client.get(share_url(family_id, version["id"])).json()
    assert owner_status[0]["status"] == "revoked"

    client.post("/api/auth/logout")
    login(client, "revoke-reader")
    assert client.get("/api/question-library/shares/received").json() == []
    new_draft = create_survey(client, "Cannot reuse after revoke")
    denied = use_version(client, new_draft["id"], family_id, version["id"])
    assert denied.status_code == 404
    denied_usage = client.get(
        f"/api/question-library/questions/{family_id}/usages",
        params={"version_id": version["id"]},
    )
    assert denied_usage.status_code == 404
    assert client.get(f"/api/fill/{survey['id']}").json()["questions"][0]["prompt"] == version["prompt"]
    stats_after = client.get(f"/api/statistics/surveys/{survey['id']}")
    assert stats_after.status_code == 200
    assert stats_after.json()["submitted_count"] == stats_before.json()["submitted_count"]
    assert stats_after.json()["questions"] == stats_before.json()["questions"]


def test_usage_listing_filters_owner_details_tracks_edits_and_versions(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "usage-owner")
    resource = create_question(client, "Exact source prompt")
    family_id = resource["family"]["id"]
    version_one = resource["version"]
    recipient = client.post(
        "/api/auth/register", json={"username": "usage-reader", "password": PASSWORD}
    )
    assert recipient.status_code == 201
    client.post(
        share_url(family_id, version_one["id"]),
        json={"recipient_username": "usage-reader"},
    )

    client.post("/api/auth/logout")
    login(client, "usage-reader")
    other_survey = create_survey(client, "Reader private title")
    imported = use_version(client, other_survey["id"], family_id, version_one["id"])
    assert imported.status_code == 201, imported.text
    local_question = imported.json()["questions"][0]
    own_usage = client.get(
        f"/api/question-library/questions/{family_id}/usages",
        params={"version_id": version_one["id"]},
    )
    assert own_usage.status_code == 200, own_usage.text
    assert own_usage.json()["access_scope"] == "shared_version"
    assert len(own_usage.json()["own_surveys"]) == 1
    assert own_usage.json()["own_surveys"][0]["survey_id"] == other_survey["id"]
    assert own_usage.json()["own_surveys"][0]["owner_id"] == recipient.json()["id"]
    assert own_usage.json()["own_surveys"][0]["question_id"] == local_question["question_id"]
    assert "other_survey_usage_by_version" not in own_usage.json()
    family_usage_denied = client.get(f"/api/question-library/questions/{family_id}/usages")
    assert family_usage_denied.status_code == 422

    renamed = client.patch(
        f"/api/surveys/{other_survey['id']}", json={"title": "Renamed private survey"}
    )
    assert renamed.status_code == 200
    refreshed_own_usage = client.get(
        f"/api/question-library/questions/{family_id}/usages",
        params={"version_id": version_one["id"]},
    ).json()
    assert refreshed_own_usage["own_surveys"][0]["title"] == "Renamed private survey"
    assert refreshed_own_usage["own_surveys"][0]["exact_version_match"] is True

    client.post("/api/auth/logout")
    login(client, "usage-owner")
    version_two_response = client.post(
        f"/api/question-library/questions/{family_id}/versions",
        params={"parent_version_id": version_one["id"]},
        json=question("Second exact version"),
    )
    assert version_two_response.status_code == 201
    version_two = version_two_response.json()
    owner_survey = create_survey(client, "Owner survey private detail")
    own_import = use_version(client, owner_survey["id"], family_id, version_two["id"])
    assert own_import.status_code == 201
    family_usage = client.get(f"/api/question-library/questions/{family_id}/usages")
    assert family_usage.status_code == 200
    data = family_usage.json()
    assert {item["version_id"] for item in data["own_surveys"]} == {version_two["id"]}
    assert data["own_surveys"][0]["question_id"] == own_import.json()["questions"][0]["question_id"]
    assert data["other_survey_usage_by_version"] == [
        {"version_id": version_one["id"], "exact_survey_count": 1, "adapted_survey_count": 0}
    ]
    serialized = str(data["other_survey_usage_by_version"])
    assert other_survey["id"] not in serialized
    assert "Reader private title" not in serialized
    assert recipient.json()["id"] not in serialized
    # A recipient cannot inspect another version's usage just by guessing its ID.
    client.post("/api/auth/logout")
    login(client, "usage-reader")
    assert client.get(
        f"/api/question-library/questions/{family_id}/usages",
        params={"version_id": version_two["id"]},
    ).status_code == 404

    # Editing the survey-local copy does not append or alter the owner's version.
    edit = {
        "type": local_question["type"],
        "prompt": "Reader-local adaptation",
        "required": local_question["required"],
        "options": local_question["options"],
        "jump_rules": [],
    }
    changed = client.patch(
        f"/api/surveys/{other_survey['id']}/questions/{local_question['question_id']}",
        json=edit,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["questions"][0]["library_ref"]["source_version_id"] == version_one["id"]
    assert client.get(
        f"/api/question-library/questions/{family_id}/usages",
        params={"version_id": version_one["id"]},
    ).json()["own_surveys"][0]["exact_version_match"] is False
    assert client.get(
        f"/api/question-library/questions/{family_id}/versions"
    ).status_code == 404
    client.post("/api/auth/logout")
    login(client, "usage-owner")
    history = client.get(f"/api/question-library/questions/{family_id}/versions").json()
    assert len(history) == 2
    assert history[0]["prompt"] == version_one["prompt"]
    adapted_usage = client.get(
        f"/api/question-library/questions/{family_id}/usages",
        params={"version_id": version_one["id"]},
    ).json()
    assert adapted_usage["own_surveys"] == []
    assert adapted_usage["other_survey_usage_by_version"] == [
        {"version_id": version_one["id"], "exact_survey_count": 0, "adapted_survey_count": 1}
    ]

    client.post("/api/auth/logout")
    login(client, "usage-reader")

    deleted = client.delete(
        f"/api/surveys/{other_survey['id']}/questions/{local_question['question_id']}"
    )
    assert deleted.status_code == 200, deleted.text
    after_delete = client.get(
        f"/api/question-library/questions/{family_id}/usages",
        params={"version_id": version_one["id"]},
    )
    assert after_delete.status_code == 200
    assert after_delete.json()["own_surveys"] == []


def test_share_index_upgrade_replaces_empty_first_batch_reservation(
    isolated_test_database,
) -> None:
    shares = isolated_test_database["question_shares"]
    shares.create_index(
        [("family_id", 1), ("recipient_user_id", 1)],
        unique=True,
        name="uq_question_shares_family_recipient",
    )
    QuestionLibraryRepository(isolated_test_database).ensure_indexes()
    indexes = shares.index_information()
    assert "uq_question_shares_family_recipient" not in indexes
    assert indexes["uq_question_shares_family_version_recipient"]["key"] == [
        ("family_id", 1),
        ("version_id", 1),
        ("recipient_user_id", 1),
    ]


def test_ambiguous_legacy_share_records_fail_closed(isolated_test_database) -> None:
    isolated_test_database["question_shares"].create_index(
        [("family_id", 1), ("recipient_user_id", 1)],
        unique=True,
        name="uq_question_shares_family_recipient",
    )
    isolated_test_database["question_shares"].insert_one(
        {"family_id": ObjectId(), "recipient_user_id": ObjectId()}
    )
    with pytest.raises(RuntimeError, match="Legacy family-wide question shares"):
        QuestionLibraryRepository(isolated_test_database).ensure_indexes()

