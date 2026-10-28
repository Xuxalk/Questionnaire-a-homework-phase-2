from typing import Any, Dict, List

from fastapi.testclient import TestClient
import pytest

from backend.app.modules.questionnaires.repository import SurveyRepository
from backend.app.modules.responses.repository import ResponseRepository

PASSWORD = "Correct horse 42!"


def register_and_login(client: TestClient, username: str) -> Dict[str, Any]:
    registered = client.post(
        "/api/auth/register", json={"username": username, "password": PASSWORD}
    )
    assert registered.status_code == 201, registered.text
    login = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return registered.json()


def create_library_question(client: TestClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    created = client.post("/api/question-library/questions", json=payload)
    assert created.status_code == 201, created.text
    return created.json()


def create_survey(client: TestClient, title: str) -> Dict[str, Any]:
    response = client.post(
        "/api/surveys",
        json={
            "title": title,
            "description": "Cross survey statistics test",
            "anonymous": True,
            "allow_multiple_submissions": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def reuse_question(
    client: TestClient, survey_id: str, family_id: str, version_id: str
) -> Dict[str, Any]:
    response = client.post(
        f"/api/surveys/{survey_id}/questions/from-library",
        json={"source_question_id": family_id, "source_version_id": version_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["questions"][-1]


def publish(client: TestClient, survey_id: str) -> None:
    response = client.post(f"/api/surveys/{survey_id}/publish")
    assert response.status_code == 200, response.text


def submit_answer(
    client: TestClient,
    survey_id: str,
    question_id: str,
    answer: Dict[str, Any],
) -> None:
    started = client.post(f"/api/fill/{survey_id}/responses")
    assert started.status_code == 201, started.text
    response = client.put(
        f"/api/fill/{survey_id}/responses/{started.json()['id']}/steps/{question_id}",
        json={"revision": started.json()["revision"], "answer": answer},
    )
    assert response.status_code == 200, response.text
    submitted = client.post(
        f"/api/fill/{survey_id}/responses/{started.json()['id']}/submit",
        json={"revision": response.json()["revision"]},
    )
    assert submitted.status_code == 200, submitted.text


def create_version_two(client: TestClient, resource: Dict[str, Any]) -> Dict[str, Any]:
    source = resource["version"]
    payload = {
        key: source[key]
        for key in ("type", "required", "options", "validation")
        if key in source
    }
    payload.update({"prompt": "Version two prompt", "jump_rules": []})
    response = client.post(
        f"/api/question-library/questions/{resource['family']['id']}/versions",
        params={"parent_version_id": source["id"]},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(
    ("question_payload", "answers"),
    [
        (
            {
                "type": "single_choice",
                "prompt": "Favorite color",
                "required": True,
                "options": [{"label": "Red"}, {"label": "Blue"}],
                "jump_rules": [],
            },
            [
                {"option_index": 0},
                {"option_index": 1},
                {"option_index": 0},
            ],
        ),
        (
            {
                "type": "multi_choice",
                "prompt": "Favorite fruit",
                "required": True,
                "options": [{"label": "Apple"}, {"label": "Pear"}],
                "validation": {"min_selected": 0, "max_selected": 2},
                "jump_rules": [],
            },
            [
                {"option_indexes": [0, 1]},
                {"option_indexes": [1]},
                {"option_indexes": [0]},
            ],
        ),
        (
            {
                "type": "text",
                "prompt": "Feedback",
                "required": True,
                "validation": {"min_length": 0},
                "jump_rules": [],
            },
            [{"text": "Great"}, {"text": "Helpful"}, {"text": "Clear"}],
        ),
        (
            {
                "type": "number",
                "prompt": "Age",
                "required": True,
                "validation": {"integer_only": True, "min_value": 0},
                "jump_rules": [],
            },
            [{"number": 18}, {"number": 30}, {"number": 21}],
        ),
    ],
)
def test_cross_survey_statistics_aggregate_exact_version_and_repeated_submissions(
    authenticated_api_client: TestClient,
    question_payload: Dict[str, Any],
    answers: List[Dict[str, Any]],
) -> None:
    client = authenticated_api_client
    register_and_login(client, "cross-types")
    resource = create_library_question(client, question_payload)
    family_id = resource["family"]["id"]
    version_one = resource["version"]
    version_two = create_version_two(client, resource)

    survey_ids = []
    qids = []
    for index, version in enumerate((version_one, version_one, version_two)):
        survey = create_survey(client, f"{question_payload['type']} survey {index}")
        question = reuse_question(client, survey["id"], family_id, version["id"])
        publish(client, survey["id"])
        survey_ids.append(survey["id"])
        qids.append(question)

    answer_values = []
    for answer in answers:
        if "option_index" in answer:
            answer_values.append(
                {"option_id": qids[0]["options"][answer["option_index"]]["option_id"]}
            )
        elif "option_indexes" in answer:
            answer_values.append(
                {
                    "option_ids": [
                        qids[0]["options"][index]["option_id"]
                        for index in answer["option_indexes"]
                    ]
                }
            )
        else:
            answer_values.append(answer)

    submit_answer(client, survey_ids[0], qids[0]["question_id"], answer_values[0])
    submit_answer(client, survey_ids[1], qids[1]["question_id"], answer_values[1])
    submit_answer(client, survey_ids[0], qids[0]["question_id"], answer_values[2])
    # A saved but unsubmitted draft does not contribute.
    draft = client.post(f"/api/fill/{survey_ids[1]}/responses")
    draft_answer = client.put(
        f"/api/fill/{survey_ids[1]}/responses/{draft.json()['id']}/steps/{qids[1]['question_id']}",
        json={"revision": 0, "answer": answer_values[0]},
    )
    assert draft_answer.status_code == 200, draft_answer.text

    versions = client.get(
        f"/api/statistics/question-library/{family_id}/versions"
    )
    assert versions.status_code == 200, versions.text
    assert [item["id"] for item in versions.json()["versions"]] == [
        version_one["id"], version_two["id"]
    ]
    result = client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version_one['id']}"
    )
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["version"]["id"] == version_one["id"]
    assert data["aggregate"]["matched_survey_count"] == 2
    assert data["aggregate"]["included_survey_count"] == 2
    assert data["aggregate"]["question_instance_count"] == 2
    assert data["aggregate"]["valid_answer_count"] == 3
    assert {survey["survey_id"] for survey in data["surveys"]} == set(survey_ids[:2])
    assert "respondent_id" not in result.text
    assert "username" not in result.text

    aggregate = data["aggregate"]
    if question_payload["type"] == "single_choice":
        assert [option["count"] for option in aggregate["options"]] == [2, 1]
        assert [round(option["percentage"], 2) for option in aggregate["options"]] == [
            66.67,
            33.33,
        ]
    elif question_payload["type"] == "multi_choice":
        assert [option["count"] for option in aggregate["options"]] == [2, 2]
        assert sum(option["percentage"] for option in aggregate["options"]) > 100
    elif question_payload["type"] == "text":
        assert {answer["text"] for answer in aggregate["answers"]} == {
            "Great",
            "Helpful",
            "Clear",
        }
    else:
        assert aggregate["average"] == 23
        assert aggregate["minimum"] == 18
        assert aggregate["maximum"] == 30
        assert [item["value"] for item in aggregate["distribution"]] == [18, 21, 30]


def test_cross_survey_statistics_keep_duplicate_instances_and_skip_unreached_questions(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "cross-jumps")
    resource = create_library_question(
        client,
        {
            "type": "single_choice",
            "prompt": "Branch selector",
            "required": True,
            "options": [{"label": "Skip middle"}, {"label": "Next"}],
            "jump_rules": [],
        },
    )
    family_id = resource["family"]["id"]
    version = resource["version"]
    survey = create_survey(client, "Repeated source instance with branch")
    first = reuse_question(client, survey["id"], family_id, version["id"])
    middle = reuse_question(client, survey["id"], family_id, version["id"])
    last = reuse_question(client, survey["id"], family_id, version["id"])
    assert len({first["question_id"], middle["question_id"], last["question_id"]}) == 3
    updated = client.patch(
        f"/api/surveys/{survey['id']}/questions/{first['question_id']}",
        json={
            **{key: first[key] for key in ("type", "prompt", "required", "options")},
            "jump_rules": [
                {
                    "priority": 1,
                    "condition": {
                        "operator": "equals",
                        "option_id": first["options"][0]["option_id"],
                    },
                    "target_question_id": last["question_id"],
                }
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    publish(client, survey["id"])
    started = client.post(f"/api/fill/{survey['id']}/responses")
    saved_first = client.put(
        f"/api/fill/{survey['id']}/responses/{started.json()['id']}/steps/{first['question_id']}",
        json={
            "revision": 0,
            "answer": {"option_id": first["options"][0]["option_id"]},
        },
    )
    assert saved_first.status_code == 200, saved_first.text
    saved_last = client.put(
        f"/api/fill/{survey['id']}/responses/{started.json()['id']}/steps/{last['question_id']}",
        json={
            "revision": saved_first.json()["revision"],
            "answer": {"option_id": last["options"][1]["option_id"]},
        },
    )
    assert saved_last.status_code == 200, saved_last.text
    submitted = client.post(
        f"/api/fill/{survey['id']}/responses/{started.json()['id']}/submit",
        json={"revision": saved_last.json()["revision"]},
    )
    assert submitted.status_code == 200, submitted.text

    response = client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version['id']}"
    )
    assert response.status_code == 200, response.text
    instances = response.json()["surveys"][0]["question_instances"]
    by_id = {item["question_id"]: item for item in instances}
    assert len(instances) == 3
    assert by_id[first["question_id"]]["valid_answer_count"] == 1
    assert by_id[middle["question_id"]]["valid_answer_count"] == 0
    assert by_id[last["question_id"]]["valid_answer_count"] == 1
    assert response.json()["aggregate"]["question_instance_count"] == 3
    assert response.json()["aggregate"]["valid_answer_count"] == 2


def test_cross_survey_statistics_share_scope_only_includes_owned_surveys_and_flags_incompatible_copy(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "cross-owner")
    resource = create_library_question(
        client,
        {
            "type": "single_choice",
            "prompt": "Private answer data",
            "required": True,
            "options": [{"label": "Yes"}, {"label": "No"}],
            "jump_rules": [],
        },
    )
    family_id = resource["family"]["id"]
    version_one = resource["version"]
    version_two = create_version_two(client, resource)
    owner_survey = create_survey(client, "Owner-only private result")
    owner_question = reuse_question(client, owner_survey["id"], family_id, version_one["id"])
    publish(client, owner_survey["id"])
    submit_answer(
        client,
        owner_survey["id"],
        owner_question["question_id"],
        {"option_id": owner_question["options"][0]["option_id"]},
    )
    reader = client.post(
        "/api/auth/register", json={"username": "cross-reader", "password": PASSWORD}
    )
    assert reader.status_code == 201
    granted = client.post(
        f"/api/question-library/questions/{family_id}/versions/{version_one['id']}/shares",
        json={"recipient_username": "cross-reader"},
    )
    assert granted.status_code == 200, granted.text

    client.post("/api/auth/logout")
    login = client.post(
        "/api/auth/login", json={"username": "cross-reader", "password": PASSWORD}
    )
    assert login.status_code == 200
    available = client.get(
        f"/api/statistics/question-library/{family_id}/versions"
    )
    assert available.status_code == 200, available.text
    assert [version["id"] for version in available.json()["versions"]] == [
        version_one["id"]
    ]
    assert version_two["id"] not in available.text

    reader_survey = create_survey(client, "Reader's own survey")
    reader_question = reuse_question(
        client, reader_survey["id"], family_id, version_one["id"]
    )
    publish(client, reader_survey["id"])
    submit_answer(
        client,
        reader_survey["id"],
        reader_question["question_id"],
        {"option_id": reader_question["options"][1]["option_id"]},
    )
    own_result = client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version_one['id']}"
    )
    assert own_result.status_code == 200, own_result.text
    assert [survey["survey_id"] for survey in own_result.json()["surveys"]] == [
        reader_survey["id"]
    ]
    assert "Owner-only private result" not in own_result.text
    assert owner_survey["id"] not in own_result.text
    denied_other_version = client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version_two['id']}"
    )
    assert denied_other_version.status_code == 404

    incompatible_survey = create_survey(client, "Incompatible local copy")
    incompatible_question = reuse_question(
        client, incompatible_survey["id"], family_id, version_one["id"]
    )
    changed_options = [dict(option) for option in incompatible_question["options"]]
    changed_options[0]["label"] = "Changed meaning"
    changed = client.patch(
        f"/api/surveys/{incompatible_survey['id']}/questions/{incompatible_question['question_id']}",
        json={
            "type": incompatible_question["type"],
            "prompt": incompatible_question["prompt"],
            "required": incompatible_question["required"],
            "options": changed_options,
            "jump_rules": [],
        },
    )
    assert changed.status_code == 200, changed.text
    publish(client, incompatible_survey["id"])
    submit_answer(
        client,
        incompatible_survey["id"],
        incompatible_question["question_id"],
        {"option_id": changed_options[0]["option_id"]},
    )
    with_incompatible = client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version_one['id']}"
    ).json()
    assert with_incompatible["aggregate"]["matched_survey_count"] == 2
    assert with_incompatible["aggregate"]["included_survey_count"] == 1
    assert with_incompatible["aggregate"]["incompatible_survey_count"] == 1
    assert with_incompatible["aggregate"]["valid_answer_count"] == 1

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login", json={"username": "cross-owner", "password": PASSWORD}
    )
    owner_stats = client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version_one['id']}"
    )
    assert [survey["survey_id"] for survey in owner_stats.json()["surveys"]] == [
        owner_survey["id"]
    ]
    revoked = client.delete(
        f"/api/question-library/questions/{family_id}/versions/{version_one['id']}/shares/{reader.json()['id']}"
    )
    assert revoked.status_code == 200, revoked.text
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login", json={"username": "cross-reader", "password": PASSWORD}
    )
    assert client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version_one['id']}"
    ).status_code == 404


def test_cross_survey_statistics_empty_closed_and_single_survey_view(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "cross-empty")
    resource = create_library_question(
        client,
        {
            "type": "number",
            "prompt": "Age",
            "required": True,
            "validation": {"integer_only": True},
            "jump_rules": [],
        },
    )
    family_id = resource["family"]["id"]
    version = resource["version"]
    empty = client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version['id']}"
    )
    assert empty.status_code == 200, empty.text
    assert empty.json()["surveys"] == []
    assert empty.json()["aggregate"]["valid_answer_count"] == 0
    assert empty.json()["aggregate"]["average"] is None
    assert empty.json()["aggregate"]["distribution"] == []

    survey = create_survey(client, "Closed historical survey")
    question = reuse_question(client, survey["id"], family_id, version["id"])
    publish(client, survey["id"])
    submit_answer(
        client, survey["id"], question["question_id"], {"number": 42}
    )
    closed = client.post(f"/api/surveys/{survey['id']}/close")
    assert closed.status_code == 200, closed.text
    per_survey = client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version['id']}",
        params={"survey_id": survey["id"]},
    )
    assert per_survey.status_code == 200, per_survey.text
    assert len(per_survey.json()["surveys"]) == 1
    assert per_survey.json()["surveys"][0]["status"] == "closed"
    assert per_survey.json()["aggregate"]["valid_answer_count"] == 1
    assert per_survey.json()["aggregate"]["minimum"] == 42
    draft = create_survey(client, "Draft is not a statistics scope")
    draft_question = reuse_question(client, draft["id"], family_id, version["id"])
    assert draft_question["question_id"]
    denied_draft = client.get(
        f"/api/statistics/question-library/{family_id}/versions/{version['id']}",
        params={"survey_id": draft["id"]},
    )
    assert denied_draft.status_code == 404


def test_cross_survey_statistics_query_indexes_are_present(isolated_test_database) -> None:
    SurveyRepository(isolated_test_database["surveys"]).ensure_indexes()
    ResponseRepository(isolated_test_database["responses"]).ensure_indexes()
    survey_indexes = isolated_test_database["surveys"].index_information()
    response_indexes = isolated_test_database["responses"].index_information()
    assert survey_indexes["ix_surveys_owner_library_version"]["key"] == [
        ("owner_id", 1),
        ("questions.library_ref.source_question_id", 1),
        ("questions.library_ref.source_version_id", 1),
    ]
    assert response_indexes["ix_responses_survey_status_submitted_at"]["key"] == [
        ("survey_id", 1),
        ("status", 1),
        ("submitted_at", -1),
    ]
