from bson import ObjectId
from fastapi.testclient import TestClient

from backend.scripts import migrate_question_library as migration


PASSWORD = "Correct horse 42!"


def register_and_login(client: TestClient, username: str) -> None:
    assert client.post(
        "/api/auth/register", json={"username": username, "password": PASSWORD}
    ).status_code == 201
    assert client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    ).status_code == 200


def make_legacy_survey(client: TestClient) -> tuple[dict, dict, dict]:
    created = client.post(
        "/api/surveys",
        json={
            "title": "Legacy survey",
            "description": "Before question library",
            "anonymous": True,
            "allow_multiple_submissions": True,
        },
    ).json()
    survey_id = created["id"]
    first = client.post(
        f"/api/surveys/{survey_id}/questions",
        json={
            "type": "single_choice",
            "prompt": "Choose a path",
            "required": True,
            "options": [{"label": "A"}, {"label": "B"}],
        },
    ).json()["questions"][-1]
    second = client.post(
        f"/api/surveys/{survey_id}/questions",
        json={"type": "text", "prompt": "Explain", "required": False},
    ).json()["questions"][-1]
    rule_payload = {
        "type": first["type"],
        "prompt": first["prompt"],
        "required": first["required"],
        "options": first["options"],
        "jump_rules": [
            {
                "priority": 1,
                "condition": {
                    "operator": "equals",
                    "option_id": first["options"][0]["option_id"],
                },
                "target_question_id": second["question_id"],
            }
        ],
    }
    saved = client.patch(
        f"/api/surveys/{survey_id}/questions/{first['question_id']}",
        json=rule_payload,
    )
    assert saved.status_code == 200, saved.text
    return saved.json(), saved.json()["questions"][0], saved.json()["questions"][1]


def test_migration_dry_run_apply_and_rerun_are_safe(
    authenticated_api_client: TestClient, isolated_test_database
) -> None:
    client = authenticated_api_client
    register_and_login(client, "migration-owner")
    survey, original_first, original_second = make_legacy_survey(client)
    survey_id = survey["id"]
    published = client.post(f"/api/surveys/{survey_id}/publish")
    assert published.status_code == 200, published.text
    started = client.post(f"/api/fill/{survey_id}/responses")
    assert started.status_code == 201, started.text
    response = started.json()
    saved_first = client.put(
        f"/api/fill/{survey_id}/responses/{response['id']}/steps/{original_first['question_id']}",
        json={
            "revision": response["revision"],
            "answer": {"option_id": original_first["options"][0]["option_id"]},
        },
    )
    assert saved_first.status_code == 200, saved_first.text
    response = saved_first.json()
    saved_second = client.put(
        f"/api/fill/{survey_id}/responses/{response['id']}/steps/{original_second['question_id']}",
        json={"revision": response["revision"], "answer": {"text": "historic answer"}},
    )
    assert saved_second.status_code == 200, saved_second.text
    submitted = client.post(
        f"/api/fill/{survey_id}/responses/{response['id']}/submit",
        json={"revision": saved_second.json()["revision"]},
    )
    assert submitted.status_code == 200, submitted.text
    statistics_before = client.get(f"/api/statistics/surveys/{survey_id}").json()
    before = isolated_test_database["surveys"].find_one(
        {"_id": ObjectId(survey["id"])}
    )
    original_questions = before["questions"]
    responses_before = list(isolated_test_database["responses"].find({"survey_id": before["_id"]}))

    dry = migration.migrate_question_library(isolated_test_database, dry_run=True)
    assert dry["would_link"] == 2
    assert dry["linked"] == 0
    assert isolated_test_database["question_families"].count_documents({}) == 0
    after_dry = isolated_test_database["surveys"].find_one({"_id": before["_id"]})
    assert after_dry["questions"] == original_questions

    applied = migration.migrate_question_library(isolated_test_database, dry_run=False)
    assert applied["linked"] == 2
    assert applied["failed"] == 0
    migrated = isolated_test_database["surveys"].find_one({"_id": before["_id"]})
    assert [q["question_id"] for q in migrated["questions"]] == [
        original_first["question_id"],
        original_second["question_id"],
    ]
    assert migrated["questions"][0]["jump_rules"] == original_questions[0]["jump_rules"]
    assert all("library_ref" in q for q in migrated["questions"])
    assert isolated_test_database["question_families"].count_documents({}) == 2
    assert isolated_test_database["question_versions"].count_documents({}) == 2
    assert isolated_test_database["question_bank_items"].count_documents({}) == 2
    responses_after = list(isolated_test_database["responses"].find({"survey_id": before["_id"]}))
    assert responses_after == responses_before
    statistics_after = client.get(f"/api/statistics/surveys/{survey_id}").json()
    assert statistics_after == statistics_before
    assert isolated_test_database["schema_migrations"].count_documents(
        {"migration_key": "question-library-v1"}
    ) == 1

    rerun = migration.migrate_question_library(isolated_test_database, dry_run=False)
    assert rerun["already_linked"] == 2
    assert rerun["linked"] == 0
    assert isolated_test_database["question_families"].count_documents({}) == 2
    assert isolated_test_database["question_versions"].count_documents({}) == 2


def test_migration_skips_unmappable_question_without_changing_it(
    authenticated_api_client: TestClient, isolated_test_database
) -> None:
    client = authenticated_api_client
    register_and_login(client, "migration-invalid-owner")
    owner_id = ObjectId(client.get("/api/auth/me").json()["id"])
    survey_id = ObjectId()
    legacy = {
        "_id": survey_id,
        "owner_id": owner_id,
        "title": "Malformed legacy survey",
        "status": "published",
        "questions": [
            {"question_id": "bad-1", "type": "unknown", "prompt": "Keep me"}
        ],
    }
    isolated_test_database["surveys"].insert_one(legacy)

    result = migration.migrate_question_library(isolated_test_database, dry_run=False)
    assert result["failed"] == 1
    assert "question content is invalid" in result["failures"][0]["reason"]
    after = isolated_test_database["surveys"].find_one({"_id": survey_id})
    assert after["questions"] == legacy["questions"]
    assert isolated_test_database["question_families"].count_documents(
        {"origin_survey_id": survey_id}
    ) == 0


def test_migration_reports_compare_and_set_conflict_and_can_resume(
    authenticated_api_client: TestClient,
    isolated_test_database,
    monkeypatch,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "migration-retry-owner")
    survey, _, _ = make_legacy_survey(client)
    survey_id = ObjectId(survey["id"])
    original_create_version = migration._find_or_create_version
    changed_once = {"done": False}

    def race_with_editor(database, library, family, content, owner_id, now):
        version = original_create_version(
            database, library, family, content, owner_id, now
        )
        if not changed_once["done"]:
            database["surveys"].update_one(
                {"_id": survey_id},
                {"$set": {"questions.0.prompt": "Concurrent draft edit"}},
            )
            changed_once["done"] = True
        return version

    monkeypatch.setattr(migration, "_find_or_create_version", race_with_editor)
    failed = migration.migrate_question_library(isolated_test_database, dry_run=False)
    assert failed["failed"] == 1
    assert "survey changed after preflight" in failed["failures"][0]["reason"]
    unchanged_ref = isolated_test_database["surveys"].find_one({"_id": survey_id})
    assert "library_ref" not in unchanged_ref["questions"][0]
    assert unchanged_ref["questions"][0]["prompt"] == "Concurrent draft edit"

    monkeypatch.setattr(migration, "_find_or_create_version", original_create_version)
    resumed = migration.migrate_question_library(isolated_test_database, dry_run=False)
    assert resumed["linked"] == 1
    assert resumed["already_linked"] == 1
    after_resume = isolated_test_database["surveys"].find_one({"_id": survey_id})
    assert after_resume["questions"][0]["prompt"] == "Concurrent draft edit"
    assert after_resume["questions"][0]["library_ref"]["source_version_id"]


def test_migration_retry_repairs_version_inserted_before_latest_pointer_failure(
    authenticated_api_client: TestClient,
    isolated_test_database,
    monkeypatch,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "migration-pointer-retry")
    survey, first_question, _ = make_legacy_survey(client)
    survey_id = ObjectId(survey["id"])
    original_set_latest = migration.QuestionLibraryRepository.set_latest_version
    interrupted = {"done": False}

    def fail_before_pointer_update(repository, family_id, version_number, version_id):
        if not interrupted["done"]:
            interrupted["done"] = True
            raise RuntimeError("simulated interruption after version insert")
        return original_set_latest(repository, family_id, version_number, version_id)

    monkeypatch.setattr(
        migration.QuestionLibraryRepository,
        "set_latest_version",
        fail_before_pointer_update,
    )
    partial = migration.migrate_question_library(
        isolated_test_database, dry_run=False
    )
    assert partial["failed"] == 1
    assert "simulated interruption" in partial["failures"][0]["reason"]

    family = isolated_test_database["question_families"].find_one(
        {"origin_survey_id": survey_id, "origin_question_id": first_question["question_id"]}
    )
    assert family is not None
    version = isolated_test_database["question_versions"].find_one(
        {"family_id": family["_id"]}
    )
    assert version is not None
    assert family["latest_version_id"] is None

    resumed = migration.migrate_question_library(
        isolated_test_database, dry_run=False
    )
    assert resumed["failed"] == 0
    repaired_family = isolated_test_database["question_families"].find_one(
        {"_id": family["_id"]}
    )
    assert repaired_family["latest_version_id"] == version["_id"]
    linked = isolated_test_database["surveys"].find_one(
        {"_id": survey_id}, {"questions": 1}
    )
    assert linked["questions"][0]["library_ref"]["source_version_id"] == version[
        "_id"
    ]

