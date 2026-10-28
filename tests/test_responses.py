from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from backend.app.modules.responses.engine import InvalidAnswer, evaluate_path, validate_answer


def register_and_login(client: TestClient, username: str, password: str = "Correct horse 42!") -> None:
    registered = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert logged_in.status_code == 200, logged_in.text


def login(client: TestClient, username: str, password: str = "Correct horse 42!") -> None:
    logged_in = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert logged_in.status_code == 200, logged_in.text


def create_survey(client: TestClient, *, allow_multiple: bool = False) -> str:
    response = client.post(
        "/api/surveys",
        json={
            "title": "Fill test",
            "description": "Published survey",
            "anonymous": True,
            "allow_multiple_submissions": allow_multiple,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_question(client: TestClient, survey_id: str, payload: dict) -> dict:
    response = client.post(f"/api/surveys/{survey_id}/questions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["questions"][-1]


def publish(client: TestClient, survey_id: str) -> None:
    response = client.post(f"/api/surveys/{survey_id}/publish")
    assert response.status_code == 200, response.text


def start(client: TestClient, survey_id: str) -> dict:
    response = client.post(f"/api/fill/{survey_id}/responses")
    assert response.status_code == 201, response.text
    return response.json()


def save_answer(
    client: TestClient,
    survey_id: str,
    response: dict,
    question_id: str,
    answer: dict,
) -> dict:
    result = client.put(
        f"/api/fill/{survey_id}/responses/{response['id']}/steps/{question_id}",
        json={"revision": response["revision"], "answer": answer},
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_share_access_hides_private_surveys_and_requires_login(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "share-owner")
    draft_id = create_survey(client)

    unauthenticated = client.post("/api/auth/logout")
    assert unauthenticated.status_code == 204
    assert client.get(f"/api/fill/{draft_id}").status_code == 401

    register_and_login(client, "share-visitor")
    assert client.get(f"/api/fill/{draft_id}").status_code == 404
    assert client.post(f"/api/fill/{draft_id}/responses").status_code == 404


def test_answer_validation_resume_skip_and_submit(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "validation-user")
    survey_id = create_survey(client)

    single = add_question(
        client,
        survey_id,
        {
            "type": "single_choice",
            "prompt": "One",
            "required": True,
            "options": [{"label": "A"}, {"label": "B"}],
        },
    )
    multi = add_question(
        client,
        survey_id,
        {
            "type": "multi_choice",
            "prompt": "Several",
            "required": True,
            "options": [{"label": "X"}, {"label": "Y"}, {"label": "Z"}],
            "validation": {"min_selected": 1, "max_selected": 2},
        },
    )
    text = add_question(
        client,
        survey_id,
        {
            "type": "text",
            "prompt": "Text",
            "required": True,
            "validation": {"min_length": 2, "max_length": 4},
        },
    )
    number = add_question(
        client,
        survey_id,
        {
            "type": "number",
            "prompt": "Number",
            "required": True,
            "validation": {"min_value": 1, "max_value": 10, "integer_only": True},
        },
    )
    optional = add_question(
        client,
        survey_id,
        {
            "type": "text",
            "prompt": "Optional",
            "required": False,
            "validation": {"min_length": 0, "max_length": 20},
        },
    )
    publish(client, survey_id)

    shared = client.get(f"/api/fill/{survey_id}")
    assert shared.status_code == 200
    public_survey = shared.json()
    assert public_survey["accepting_responses"] is True
    assert "owner_id" not in public_survey
    assert "allow_multiple_submissions" not in public_survey

    response = start(client, survey_id)
    response_id = response["id"]
    assert response["revision"] == 0
    assert response["next_question_id"] == single["question_id"]

    missing_required = client.put(
        f"/api/fill/{survey_id}/responses/{response_id}/steps/{single['question_id']}",
        json={"revision": 0, "skip": True},
    )
    assert missing_required.status_code == 422

    invalid_single = client.put(
        f"/api/fill/{survey_id}/responses/{response_id}/steps/{single['question_id']}",
        json={"revision": 0, "answer": {"option_id": "not-an-option"}},
    )
    assert invalid_single.status_code == 422

    response = save_answer(
        client,
        survey_id,
        response,
        single["question_id"],
        {"option_id": single["options"][0]["option_id"]},
    )
    assert response["revision"] == 1
    assert response["next_question_id"] == multi["question_id"]

    # Starting again resumes the one existing draft and preserves revision/steps.
    restored = start(client, survey_id)
    assert restored["id"] == response_id
    assert restored["revision"] == response["revision"]
    assert restored["steps"] == response["steps"]

    duplicate_options = client.put(
        f"/api/fill/{survey_id}/responses/{response_id}/steps/{multi['question_id']}",
        json={
            "revision": response["revision"],
            "answer": {"option_ids": [multi["options"][0]["option_id"]] * 2},
        },
    )
    assert duplicate_options.status_code == 422
    too_few = client.put(
        f"/api/fill/{survey_id}/responses/{response_id}/steps/{multi['question_id']}",
        json={"revision": response["revision"], "answer": {"option_ids": []}},
    )
    assert too_few.status_code == 422
    too_many = client.put(
        f"/api/fill/{survey_id}/responses/{response_id}/steps/{multi['question_id']}",
        json={
            "revision": response["revision"],
            "answer": {
                "option_ids": [
                    option["option_id"] for option in multi["options"]
                ]
            },
        },
    )
    assert too_many.status_code == 422

    response = save_answer(
        client,
        survey_id,
        response,
        multi["question_id"],
        {"option_ids": [multi["options"][0]["option_id"]]},
    )
    assert response["next_question_id"] == text["question_id"]

    invalid_text = client.put(
        f"/api/fill/{survey_id}/responses/{response_id}/steps/{text['question_id']}",
        json={"revision": response["revision"], "answer": {"text": "x"}},
    )
    assert invalid_text.status_code == 422
    text_too_long = client.put(
        f"/api/fill/{survey_id}/responses/{response_id}/steps/{text['question_id']}",
        json={"revision": response["revision"], "answer": {"text": "toolong"}},
    )
    assert text_too_long.status_code == 422
    response = save_answer(
        client, survey_id, response, text["question_id"], {"text": "okay"}
    )

    for invalid_number in (0, 11, 2.5, True):
        invalid = client.put(
            f"/api/fill/{survey_id}/responses/{response_id}/steps/{number['question_id']}",
            json={"revision": response["revision"], "answer": {"number": invalid_number}},
        )
        assert invalid.status_code == 422
    response = save_answer(
        client, survey_id, response, number["question_id"], {"number": 5}
    )
    assert response["next_question_id"] == optional["question_id"]

    incomplete = client.post(
        f"/api/fill/{survey_id}/responses/{response_id}/submit",
        json={"revision": response["revision"]},
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["detail"]["next_question_id"] == optional["question_id"]

    skipped = client.put(
        f"/api/fill/{survey_id}/responses/{response_id}/steps/{optional['question_id']}",
        json={"revision": response["revision"], "skip": True},
    )
    assert skipped.status_code == 200
    assert skipped.json()["steps"][-1] == {
        "question_id": optional["question_id"],
        "status": "skipped",
    }
    submitted = client.post(
        f"/api/fill/{survey_id}/responses/{response_id}/submit",
        json={"revision": skipped.json()["revision"]},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "submitted"
    assert submitted.json()["revision"] == skipped.json()["revision"] + 1
    assert "respondent_id" not in submitted.json()

    cannot_edit_submitted = client.put(
        f"/api/fill/{survey_id}/responses/{response_id}/steps/{single['question_id']}",
        json={
            "revision": submitted.json()["revision"],
            "answer": {"option_id": single["options"][1]["option_id"]},
        },
    )
    assert cannot_edit_submitted.status_code == 409


def test_priority_dynamic_jumps_and_history_change_cleans_unreachable_steps(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "branch-owner")
    survey_id = create_survey(client, allow_multiple=True)

    single = add_question(
        client,
        survey_id,
        {
            "type": "single_choice",
            "prompt": "Branch",
            "required": True,
            "options": [{"label": "A"}, {"label": "B"}],
        },
    )
    multi = add_question(
        client,
        survey_id,
        {
            "type": "multi_choice",
            "prompt": "Multi branch",
            "required": True,
            "options": [{"label": "X"}, {"label": "Y"}, {"label": "Z"}],
            "validation": {"min_selected": 1, "max_selected": 3},
        },
    )
    text_branch = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "Text branch", "required": False},
    )
    number = add_question(
        client,
        survey_id,
        {
            "type": "number",
            "prompt": "Age branch",
            "required": True,
            "validation": {"min_value": 0, "max_value": 100, "integer_only": True},
        },
    )
    text_default = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "Default branch", "required": False},
    )
    text_end = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "End", "required": False},
    )

    def update_question(question: dict, payload: dict):
        return client.patch(
            f"/api/surveys/{survey_id}/questions/{question['question_id']}",
            json=payload,
        )

    q1_update = update_question(
        single,
        {
            "type": "single_choice",
            "prompt": "Branch",
            "required": True,
            "options": single["options"],
            "jump_rules": [
                {
                    "priority": 2,
                    "condition": {"operator": "equals", "option_id": single["options"][0]["option_id"]},
                    "target_question_id": text_branch["question_id"],
                },
                {
                    "priority": 1,
                    "condition": {"operator": "equals", "option_id": single["options"][0]["option_id"]},
                    "target_question_id": multi["question_id"],
                },
                {
                    "priority": 3,
                    "condition": {"operator": "equals", "option_id": single["options"][1]["option_id"]},
                    "target_question_id": text_branch["question_id"],
                },
            ],
        },
    )
    assert q1_update.status_code == 200

    q2_update = update_question(
        multi,
        {
            "type": "multi_choice",
            "prompt": "Multi branch",
            "required": True,
            "options": multi["options"],
            "validation": {"min_selected": 1, "max_selected": 3},
            "jump_rules": [
                {
                    "priority": 2,
                    "condition": {
                        "match": "ALL",
                        "option_ids": [multi["options"][0]["option_id"], multi["options"][1]["option_id"]],
                    },
                    "target_question_id": text_end["question_id"],
                },
                {
                    "priority": 1,
                    "condition": {"match": "ANY", "option_ids": [multi["options"][2]["option_id"]]},
                    "target_question_id": number["question_id"],
                },
            ],
        },
    )
    assert q2_update.status_code == 200

    q4_update = update_question(
        number,
        {
            "type": "number",
            "prompt": "Age branch",
            "required": True,
            "validation": {"min_value": 0, "max_value": 100, "integer_only": True},
            "jump_rules": [
                {
                    "priority": 1,
                    "condition": {"operator": "gte", "value": 18},
                    "target_question_id": text_end["question_id"],
                }
            ],
        },
    )
    assert q4_update.status_code == 200
    publish(client, survey_id)

    share = client.get(f"/api/fill/{survey_id}")
    assert share.status_code == 200
    assert "owner_id" not in share.json()

    response = start(client, survey_id)
    response = save_answer(
        client,
        survey_id,
        response,
        single["question_id"],
        {"option_id": single["options"][0]["option_id"]},
    )
    assert response["next_question_id"] == multi["question_id"]
    response = save_answer(
        client,
        survey_id,
        response,
        multi["question_id"],
        {
            "option_ids": [
                multi["options"][0]["option_id"],
                multi["options"][1]["option_id"],
                multi["options"][2]["option_id"],
            ]
        },
    )
    # ANY and ALL both match; the smaller priority wins.
    assert response["next_question_id"] == number["question_id"]
    response = save_answer(
        client, survey_id, response, number["question_id"], {"number": 25}
    )
    assert response["next_question_id"] == text_end["question_id"]
    response = save_answer(
        client, survey_id, response, text_end["question_id"], {"text": "done"}
    )
    assert response["complete"] is True

    # Editing an earlier answer switches to another branch and atomically
    # removes all now-unreachable saved answers.
    response = save_answer(
        client,
        survey_id,
        response,
        single["question_id"],
        {"option_id": single["options"][1]["option_id"]},
    )
    assert response["next_question_id"] == text_branch["question_id"]
    assert [step["question_id"] for step in response["steps"]] == [single["question_id"]]

    unreachable_edit = client.put(
        f"/api/fill/{survey_id}/responses/{response['id']}/steps/{number['question_id']}",
        json={"revision": response["revision"], "answer": {"number": 30}},
    )
    assert unreachable_edit.status_code == 409

    response = client.put(
        f"/api/fill/{survey_id}/responses/{response['id']}/steps/{text_branch['question_id']}",
        json={"revision": response["revision"], "skip": True},
    )
    assert response.status_code == 200
    state = response.json()
    state = save_answer(client, survey_id, state, number["question_id"], {"number": 10})
    assert state["next_question_id"] == text_default["question_id"]
    skipped = client.put(
        f"/api/fill/{survey_id}/responses/{state['id']}/steps/{text_default['question_id']}",
        json={"revision": state["revision"], "skip": True},
    )
    assert skipped.status_code == 200
    state = skipped.json()
    skipped_end = client.put(
        f"/api/fill/{survey_id}/responses/{state['id']}/steps/{text_end['question_id']}",
        json={"revision": state["revision"], "skip": True},
    )
    assert skipped_end.status_code == 200
    final = client.post(
        f"/api/fill/{survey_id}/responses/{state['id']}/submit",
        json={"revision": skipped_end.json()["revision"]},
    )
    assert final.status_code == 200

    # A second respondent can take the ALL branch without seeing identity data.
    client.post("/api/auth/logout")
    register_and_login(client, "all-branch-user")
    all_response = start(client, survey_id)
    all_response = save_answer(
        client,
        survey_id,
        all_response,
        single["question_id"],
        {"option_id": single["options"][0]["option_id"]},
    )
    all_response = save_answer(
        client,
        survey_id,
        all_response,
        multi["question_id"],
        {"option_ids": [multi["options"][0]["option_id"], multi["options"][1]["option_id"]]},
    )
    assert all_response["next_question_id"] == text_end["question_id"]
    all_skip = client.put(
        f"/api/fill/{survey_id}/responses/{all_response['id']}/steps/{text_end['question_id']}",
        json={"revision": all_response["revision"], "skip": True},
    )
    assert all_skip.status_code == 200


def test_anonymous_response_endpoints_do_not_expose_respondent_to_creator(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "anonymous-owner")
    survey_id = create_survey(client)
    question = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "Answer", "required": True},
    )
    publish(client, survey_id)

    client.post("/api/auth/logout")
    register_and_login(client, "anonymous-respondent")
    response = start(client, survey_id)
    response_id = response["id"]
    saved = save_answer(
        client, survey_id, response, question["question_id"], {"text": "secret"}
    )
    assert "respondent_id" not in saved
    assert "username" not in saved

    client.post("/api/auth/logout")
    login(client, "anonymous-owner")
    creator_view = client.get(
        f"/api/fill/{survey_id}/responses/{response_id}"
    )
    assert creator_view.status_code == 404
    shared = client.get(f"/api/fill/{survey_id}")
    assert shared.status_code == 200
    assert "owner_id" not in shared.json()
    assert "respondent_id" not in shared.text


@pytest.mark.parametrize("allow_multiple", [False, True])
def test_repeat_submission_policy_and_no_content_deduplication(
    authenticated_api_client: TestClient,
    isolated_test_database,
    allow_multiple: bool,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "repeat-user-{}".format(allow_multiple))
    survey_id = create_survey(client, allow_multiple=allow_multiple)
    question = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "Same answer", "required": True},
    )
    publish(client, survey_id)

    first = start(client, survey_id)
    first = save_answer(
        client, survey_id, first, question["question_id"], {"text": "same"}
    )
    first_submit = client.post(
        f"/api/fill/{survey_id}/responses/{first['id']}/submit",
        json={"revision": first["revision"]},
    )
    assert first_submit.status_code == 200

    if not allow_multiple:
        assert client.post(f"/api/fill/{survey_id}/responses").status_code == 409
        response = isolated_test_database["responses"].find_one(
            {"_id": ObjectId(first["id"])}
        )
        assert response["lifecycle_guard"] == "single"
        assert isolated_test_database["responses"].count_documents(
            {"survey_id": ObjectId(survey_id), "status": "submitted"}
        ) == 1
    else:
        second = start(client, survey_id)
        second = save_answer(
            client, survey_id, second, question["question_id"], {"text": "same"}
        )
        second_submit = client.post(
            f"/api/fill/{survey_id}/responses/{second['id']}/submit",
            json={"revision": second["revision"]},
        )
        assert second_submit.status_code == 200
        assert second["id"] != first["id"]
        assert isolated_test_database["responses"].count_documents(
            {"survey_id": ObjectId(survey_id), "status": "submitted"}
        ) == 2
        records = list(
            isolated_test_database["responses"].find(
                {"survey_id": ObjectId(survey_id), "status": "submitted"}
            )
        )
        assert [record["steps"][0]["value"]["text"] for record in records] == [
            "same",
            "same",
        ]
        assert all("lifecycle_guard" not in record for record in records)


def test_deadline_and_close_are_reported_on_share_link_and_block_writes(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "deadline-owner")
    survey_id = create_survey(client)
    deadline_question = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "Question", "required": False},
    )
    publish(client, survey_id)
    draft = start(client, survey_id)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    set_deadline = client.put(
        f"/api/surveys/{survey_id}/deadline", json={"deadline": past}
    )
    assert set_deadline.status_code == 200
    public_view = client.get(f"/api/fill/{survey_id}")
    assert public_view.status_code == 200
    assert public_view.json()["accepting_responses"] is False
    assert public_view.json()["closed_reason"] == "deadline"
    assert client.post(f"/api/fill/{survey_id}/responses").status_code == 410
    expired_save = client.put(
        f"/api/fill/{survey_id}/responses/{draft['id']}/steps/{deadline_question['question_id']}",
        json={"revision": draft["revision"], "skip": True},
    )
    assert expired_save.status_code == 410
    expired_submit = client.post(
        f"/api/fill/{survey_id}/responses/{draft['id']}/submit",
        json={"revision": draft["revision"]},
    )
    assert expired_submit.status_code == 410

    client.post("/api/auth/logout")
    register_and_login(client, "manual-close-owner")
    closed_id = create_survey(client)
    close_question = add_question(
        client,
        closed_id,
        {"type": "text", "prompt": "Question", "required": False},
    )
    publish(client, closed_id)
    closed_draft = start(client, closed_id)
    close_response = client.post(f"/api/surveys/{closed_id}/close")
    assert close_response.status_code == 200
    closed_view = client.get(f"/api/fill/{closed_id}")
    assert closed_view.status_code == 200
    assert closed_view.json()["accepting_responses"] is False
    assert closed_view.json()["closed_reason"] == "closed"
    assert client.post(f"/api/fill/{closed_id}/responses").status_code == 410
    closed_save = client.put(
        f"/api/fill/{closed_id}/responses/{closed_draft['id']}/steps/{close_question['question_id']}",
        json={"revision": closed_draft["revision"], "skip": True},
    )
    assert closed_save.status_code == 410


def test_concurrent_start_resumes_one_draft_and_revision_allows_one_writer(
    api_application,
) -> None:
    with TestClient(api_application) as first_client, TestClient(api_application) as second_client:
        register_and_login(first_client, "concurrent-respondent")
        login(second_client, "concurrent-respondent")
        survey_id = create_survey(first_client, allow_multiple=True)
        question = add_question(
            first_client,
            survey_id,
            {"type": "text", "prompt": "Concurrent", "required": True},
        )
        publish(first_client, survey_id)

        def begin(client: TestClient) -> dict:
            result = client.post(f"/api/fill/{survey_id}/responses")
            assert result.status_code == 201, result.text
            return result.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            starts = list(executor.map(begin, [first_client, second_client]))
        assert starts[0]["id"] == starts[1]["id"]
        response_id = starts[0]["id"]

        def race_write(client: TestClient, value: str):
            return client.put(
                f"/api/fill/{survey_id}/responses/{response_id}/steps/{question['question_id']}",
                json={"revision": 0, "answer": {"text": value}},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            writes = list(
                executor.map(
                    lambda args: race_write(*args),
                    [(first_client, "first"), (second_client, "second")],
                )
            )
        assert sorted(result.status_code for result in writes) == [200, 409]
        winner = next(result.json() for result in writes if result.status_code == 200)
        assert winner["revision"] == 1


def test_distinct_respondents_can_submit_same_survey_concurrently(
    api_application,
) -> None:
    with TestClient(api_application) as owner_client, TestClient(
        api_application
    ) as first_client, TestClient(api_application) as second_client:
        register_and_login(owner_client, "multi-user-owner")
        survey_id = create_survey(owner_client)
        question = add_question(
            owner_client,
            survey_id,
            {"type": "text", "prompt": "Concurrent answer", "required": True},
        )
        publish(owner_client, survey_id)

        register_and_login(first_client, "multi-user-first")
        register_and_login(second_client, "multi-user-second")

        def fill_and_submit(client: TestClient, answer_text: str) -> tuple:
            response = start(client, survey_id)
            response = save_answer(
                client,
                survey_id,
                response,
                question["question_id"],
                {"text": answer_text},
            )
            submitted = client.post(
                f"/api/fill/{survey_id}/responses/{response['id']}/submit",
                json={"revision": response["revision"]},
            )
            return response["id"], submitted.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda args: fill_and_submit(*args),
                    [
                        (first_client, "first respondent"),
                        (second_client, "second respondent"),
                    ],
                )
            )

        assert {status_code for _, status_code in results} == {200}
        assert len({response_id for response_id, _ in results}) == 2
        database = api_application.state.database
        assert database["responses"].count_documents(
            {"survey_id": ObjectId(survey_id), "status": "submitted"}
        ) == 2


def test_response_indexes_are_present(isolated_test_database) -> None:
    from backend.app.modules.responses.repository import ResponseRepository

    repository = ResponseRepository(isolated_test_database["responses"])
    repository.ensure_indexes()
    indexes = list(isolated_test_database["responses"].list_indexes())
    lifecycle = next(
        index for index in indexes if index["name"] == "uq_responses_lifecycle_guard"
    )
    assert lifecycle["unique"] is True
    assert lifecycle["partialFilterExpression"] == {
        "lifecycle_guard": {"$exists": True}
    }
    assert any(
        index["name"] == "ix_responses_survey_status_submitted_at"
        for index in indexes
    )


@pytest.mark.parametrize(
    "operator,number,threshold,should_jump",
    [
        ("eq", 5, 5, True),
        ("ne", 4, 5, True),
        ("gt", 6, 5, True),
        ("gte", 5, 5, True),
        ("lt", 4, 5, True),
        ("lte", 5, 5, True),
        ("eq", 4, 5, False),
    ],
)
def test_numeric_jump_operators(
    operator: str, number: int, threshold: int, should_jump: bool
) -> None:
    survey = {
        "questions": [
            {
                "question_id": "q1",
                "type": "number",
                "jump_rules": [
                    {
                        "priority": 1,
                        "condition": {"operator": operator, "value": threshold},
                        "target_question_id": "q3",
                    }
                ],
            },
            {"question_id": "q2", "type": "text"},
            {"question_id": "q3", "type": "text"},
        ]
    }
    state = evaluate_path(
        survey,
        [
            {
                "question_id": "q1",
                "status": "answered",
                "value": {"number": number},
            }
        ],
    )
    assert state.next_question_id == ("q3" if should_jump else "q2")


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf")])
def test_numeric_answer_rejects_non_finite_values(invalid_value: float) -> None:
    with pytest.raises(InvalidAnswer):
        validate_answer(
            {"type": "number", "validation": {}}, {"number": invalid_value}
        )
