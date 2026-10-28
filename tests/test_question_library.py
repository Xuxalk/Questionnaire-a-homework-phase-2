from bson import ObjectId
from fastapi.testclient import TestClient

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


def create_survey(client: TestClient, title: str = "Library survey") -> dict:
    response = client.post(
        "/api/surveys",
        json={
            "title": title,
            "description": "Question library test",
            "anonymous": True,
            "allow_multiple_submissions": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def single_choice(prompt: str = "Favourite colour") -> dict:
    return {
        "type": "single_choice",
        "prompt": prompt,
        "required": True,
        "options": [{"label": "Red"}, {"label": "Blue"}],
        "jump_rules": [],
    }


def create_library_question(client: TestClient, prompt: str = "Favourite colour") -> dict:
    response = client.post("/api/question-library/questions", json=single_choice(prompt))
    assert response.status_code == 201, response.text
    return response.json()


def test_question_library_creation_versions_history_and_restore(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "library-owner")
    created = create_library_question(client)
    family_id = created["family"]["id"]
    first = created["version"]
    assert first["version_number"] == 1
    assert first["parent_version_id"] is None

    changed = single_choice("Favourite colour (updated)")
    response = client.post(
        f"/api/question-library/questions/{family_id}/versions",
        params={"parent_version_id": first["id"]},
        json=changed,
    )
    assert response.status_code == 201, response.text
    second = response.json()
    assert second["version_number"] == 2
    assert second["parent_version_id"] == first["id"]
    assert second["prompt"] == "Favourite colour (updated)"
    assert first["prompt"] == "Favourite colour"

    history = client.get(f"/api/question-library/questions/{family_id}/versions")
    assert history.status_code == 200
    assert [item["version_number"] for item in history.json()] == [1, 2]

    restored = client.post(
        f"/api/question-library/questions/{family_id}/versions/{first['id']}/restore"
    )
    assert restored.status_code == 201, restored.text
    third = restored.json()
    assert third["version_number"] == 3
    assert third["parent_version_id"] == second["id"]
    assert third["restored_from_version_id"] == first["id"]
    assert third["prompt"] == first["prompt"]
    final_history = client.get(
        f"/api/question-library/questions/{family_id}/versions"
    ).json()
    assert len(final_history) == 3
    assert final_history[0]["prompt"] == "Favourite colour"
    assert final_history[1]["prompt"] == "Favourite colour (updated)"


def test_question_creation_recovers_if_latest_pointer_write_is_interrupted(
    authenticated_api_client: TestClient,
    isolated_test_database,
    monkeypatch,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "library-pointer-retry")
    original_set_latest = QuestionLibraryRepository.set_latest_version
    interrupted = {"done": False}

    def fail_before_pointer_update(repository, family_id, version_number, version_id):
        if not interrupted["done"]:
            interrupted["done"] = True
            raise RuntimeError("simulated interruption after version insert")
        return original_set_latest(repository, family_id, version_number, version_id)

    monkeypatch.setattr(
        QuestionLibraryRepository, "set_latest_version", fail_before_pointer_update
    )
    response = client.post(
        "/api/question-library/questions", json=single_choice("Recovered question")
    )

    assert response.status_code == 201, response.text
    created = response.json()
    family_id = ObjectId(created["family"]["id"])
    version_id = ObjectId(created["version"]["id"])
    family = isolated_test_database["question_families"].find_one({"_id": family_id})
    assert family["latest_version_id"] == version_id
    assert isolated_test_database["question_versions"].count_documents(
        {"family_id": family_id}
    ) == 1
    assert isolated_test_database["question_bank_items"].count_documents(
        {"family_id": family_id}
    ) == 1


def test_personal_banks_add_update_and_remove_question(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "bank-owner")
    created = create_library_question(client)
    family_id = created["family"]["id"]
    version = created["version"]

    banks = client.get("/api/question-library/banks")
    assert banks.status_code == 200
    assert len(banks.json()) == 1
    default_bank = banks.json()[0]
    assert default_bank["is_default"] is True
    assert default_bank["visibility"] == "private"

    custom = client.post(
        "/api/question-library/banks", json={"name": "常用问题"}
    )
    assert custom.status_code == 201, custom.text
    bank_id = custom.json()["id"]
    added = client.post(
        f"/api/question-library/banks/{bank_id}/items",
        json={"family_id": family_id, "version_id": version["id"]},
    )
    assert added.status_code == 201, added.text
    assert added.json()["version"]["id"] == version["id"]
    duplicate = client.post(
        f"/api/question-library/banks/{bank_id}/items",
        json={"family_id": family_id, "version_id": version["id"]},
    )
    assert duplicate.status_code == 409

    changed = client.post(
        f"/api/question-library/questions/{family_id}/versions",
        params={"parent_version_id": version["id"]},
        json=single_choice("A newer version"),
    )
    assert changed.status_code == 201
    updated_item = client.put(
        f"/api/question-library/banks/{bank_id}/items/{family_id}",
        json={"version_id": changed.json()["id"]},
    )
    assert updated_item.status_code == 200
    assert updated_item.json()["version"]["id"] == changed.json()["id"]

    listed = client.get(f"/api/question-library/banks/{bank_id}/items")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["latest_version"]["id"] == changed.json()["id"]
    removed = client.delete(
        f"/api/question-library/banks/{bank_id}/items/{family_id}"
    )
    assert removed.status_code == 204
    assert client.get(f"/api/question-library/banks/{bank_id}/items").json() == []


def test_reuse_has_local_question_ids_and_survey_specific_jump_rules(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "reuse-owner")
    source_survey = create_survey(client, "Source survey")
    source_id = source_survey["id"]
    source_question = client.post(
        f"/api/surveys/{source_id}/questions", json=single_choice("Do you agree?")
    )
    assert source_question.status_code == 201, source_question.text
    source_question = source_question.json()["questions"][-1]
    source_target = client.post(
        f"/api/surveys/{source_id}/questions",
        json={"type": "text", "prompt": "Why?", "required": False},
    ).json()["questions"][-1]
    source_payload = single_choice("Do you agree?")
    source_payload["options"] = source_question["options"]
    source_payload["jump_rules"] = [
        {
            "priority": 1,
            "condition": {
                "operator": "equals",
                "option_id": source_question["options"][0]["option_id"],
            },
            "target_question_id": source_target["question_id"],
        }
    ]
    saved_source = client.patch(
        f"/api/surveys/{source_id}/questions/{source_question['question_id']}",
        json=source_payload,
    )
    assert saved_source.status_code == 200, saved_source.text

    imported = client.post(
        f"/api/question-library/from-survey/{source_id}/{source_question['question_id']}",
        json={},
    )
    assert imported.status_code == 201, imported.text
    family_id = imported.json()["family"]["id"]
    version = imported.json()["version"]
    assert "jump_rules" not in version

    target_survey = create_survey(client, "Target survey")
    target_id = target_survey["id"]
    reused = client.post(
        f"/api/surveys/{target_id}/questions/from-library",
        json={
            "source_question_id": family_id,
            "source_version_id": version["id"],
        },
    )
    assert reused.status_code == 201, reused.text
    local_question = reused.json()["questions"][-1]
    assert local_question["question_id"] != source_question["question_id"]
    assert local_question["library_ref"] == {
        "source_question_id": family_id,
        "source_version_id": version["id"],
    }
    assert local_question["jump_rules"] == []

    target = client.post(
        f"/api/surveys/{target_id}/questions",
        json={"type": "text", "prompt": "Explain your choice", "required": False},
    ).json()["questions"][-1]
    target_payload = {
        key: local_question[key]
        for key in ("type", "prompt", "required", "options", "jump_rules")
    }
    target_payload["jump_rules"] = [
        {
            "priority": 1,
            "condition": {
                "operator": "equals",
                "option_id": local_question["options"][0]["option_id"],
            },
            "target_question_id": target["question_id"],
        }
    ]
    updated = client.patch(
        f"/api/surveys/{target_id}/questions/{local_question['question_id']}",
        json=target_payload,
    )
    assert updated.status_code == 200, updated.text
    local_question = updated.json()["questions"][0]
    assert local_question["jump_rules"][0]["target_question_id"] == target["question_id"]

    published = client.post(f"/api/surveys/{target_id}/publish")
    assert published.status_code == 200, published.text
    share = client.get(f"/api/fill/{target_id}")
    assert share.status_code == 200
    assert share.json()["questions"][0]["jump_rules"][0]["target_question_id"] == target[
        "question_id"
    ]

    next_version = client.post(
        f"/api/question-library/questions/{family_id}/versions",
        params={"parent_version_id": version["id"]},
        json=single_choice("A changed catalogue prompt"),
    )
    assert next_version.status_code == 201
    unchanged_survey = client.get(f"/api/surveys/{target_id}").json()
    assert unchanged_survey["questions"][0]["prompt"] == "Do you agree?"
    assert unchanged_survey["questions"][0]["jump_rules"][0]["target_question_id"] == target[
        "question_id"
    ]


def test_editing_a_reused_draft_question_appends_a_version(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "edit-version-owner")
    resource = create_library_question(client)
    family_id = resource["family"]["id"]
    original = resource["version"]
    survey_id = create_survey(client)["id"]
    inserted = client.post(
        f"/api/surveys/{survey_id}/questions/from-library",
        json={
            "source_question_id": family_id,
            "source_version_id": original["id"],
        },
    ).json()["questions"][0]

    edit = {
        "type": inserted["type"],
        "prompt": "Changed inside a draft",
        "required": inserted["required"],
        "options": inserted["options"],
        "jump_rules": [],
    }
    updated = client.patch(
        f"/api/surveys/{survey_id}/questions/{inserted['question_id']}", json=edit
    )
    assert updated.status_code == 200, updated.text
    updated_question = updated.json()["questions"][0]
    assert updated_question["prompt"] == "Changed inside a draft"
    assert updated_question["library_ref"]["source_version_id"] != original["id"]

    history = client.get(
        f"/api/question-library/questions/{family_id}/versions"
    ).json()
    assert [item["version_number"] for item in history] == [1, 2]
    assert history[0]["prompt"] == "Favourite colour"
    assert history[1]["prompt"] == "Changed inside a draft"


def test_private_question_library_cannot_be_read_or_used_by_another_user(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "private-library-owner")
    resource = create_library_question(client)
    family_id = resource["family"]["id"]
    version_id = resource["version"]["id"]
    client.post("/api/auth/logout")
    register_and_login(client, "private-library-visitor")

    history = client.get(f"/api/question-library/questions/{family_id}/versions")
    assert history.status_code == 404
    survey_id = create_survey(client)["id"]
    used = client.post(
        f"/api/surveys/{survey_id}/questions/from-library",
        json={"source_question_id": family_id, "source_version_id": version_id},
    )
    assert used.status_code == 404

