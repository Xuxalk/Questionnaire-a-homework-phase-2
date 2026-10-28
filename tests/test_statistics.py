from typing import Any, Dict

from bson import ObjectId
from fastapi.testclient import TestClient


PASSWORD = "Correct horse 42!"


def register_and_login(client: TestClient, username: str) -> None:
    registered = client.post(
        "/api/auth/register", json={"username": username, "password": PASSWORD}
    )
    assert registered.status_code == 201, registered.text
    logged_in = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert logged_in.status_code == 200, logged_in.text


def add_question(client: TestClient, survey_id: str, question: Dict[str, Any]) -> dict:
    response = client.post(f"/api/surveys/{survey_id}/questions", json=question)
    assert response.status_code == 201, response.text
    return response.json()["questions"][-1]


def save_answer(
    client: TestClient,
    survey_id: str,
    response: Dict[str, Any],
    question_id: str,
    answer: Dict[str, Any],
) -> Dict[str, Any]:
    saved = client.put(
        f"/api/fill/{survey_id}/responses/{response['id']}/steps/{question_id}",
        json={"revision": response["revision"], "answer": answer},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_statistics_aggregate_only_submitted_answered_steps(
    authenticated_api_client: TestClient,
    isolated_test_database,
) -> None:
    client = authenticated_api_client
    register_and_login(client, "statistics-owner")
    created = client.post(
        "/api/surveys",
        json={
            "title": "Statistics survey",
            "description": "Counts and averages",
            "anonymous": True,
            "allow_multiple_submissions": True,
        },
    )
    assert created.status_code == 201, created.text
    survey_id = created.json()["id"]

    single = add_question(
        client,
        survey_id,
        {
            "type": "single_choice",
            "prompt": "Color",
            "required": True,
            "options": [{"label": "Red"}, {"label": "Blue"}],
        },
    )
    multi = add_question(
        client,
        survey_id,
        {
            "type": "multi_choice",
            "prompt": "Fruits",
            "required": True,
            "options": [{"label": "Apple"}, {"label": "Pear"}],
            "validation": {"min_selected": 1, "max_selected": 2},
        },
    )
    text = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "Comment", "required": True},
    )
    number = add_question(
        client,
        survey_id,
        {"type": "number", "prompt": "Score", "required": True},
    )
    skipped = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "Optional", "required": False},
    )
    published = client.post(f"/api/surveys/{survey_id}/publish")
    assert published.status_code == 200, published.text

    first = client.post(f"/api/fill/{survey_id}/responses")
    assert first.status_code == 201, first.text
    response = first.json()
    response = save_answer(
        client,
        survey_id,
        response,
        single["question_id"],
        {"option_id": single["options"][0]["option_id"]},
    )
    response = save_answer(
        client,
        survey_id,
        response,
        multi["question_id"],
        {"option_ids": [option["option_id"] for option in multi["options"]]},
    )
    response = save_answer(
        client, survey_id, response, text["question_id"], {"text": "First answer"}
    )
    response = save_answer(
        client, survey_id, response, number["question_id"], {"number": 20}
    )
    skipped_step = client.put(
        f"/api/fill/{survey_id}/responses/{response['id']}/steps/{skipped['question_id']}",
        json={"revision": response["revision"], "skip": True},
    )
    assert skipped_step.status_code == 200, skipped_step.text
    submitted = client.post(
        f"/api/fill/{survey_id}/responses/{response['id']}/submit",
        json={"revision": skipped_step.json()["revision"]},
    )
    assert submitted.status_code == 200, submitted.text

    # This second response contains answers but remains draft and must not affect statistics.
    draft = client.post(f"/api/fill/{survey_id}/responses")
    assert draft.status_code == 201, draft.text
    draft_response = save_answer(
        client,
        survey_id,
        draft.json(),
        single["question_id"],
        {"option_id": single["options"][1]["option_id"]},
    )
    draft_response = save_answer(
        client,
        survey_id,
        draft_response,
        multi["question_id"],
        {"option_ids": [multi["options"][1]["option_id"]]},
    )
    draft_response = save_answer(
        client,
        survey_id,
        draft_response,
        text["question_id"],
        {"text": "Draft answer"},
    )
    save_answer(
        client,
        survey_id,
        draft_response,
        number["question_id"],
        {"number": 100},
    )

    statistics = client.get(f"/api/statistics/surveys/{survey_id}")
    assert statistics.status_code == 200, statistics.text
    result = statistics.json()
    assert result["submitted_count"] == 1
    questions = {item["question_id"]: item for item in result["questions"]}
    assert questions[single["question_id"]]["answered_count"] == 1
    assert [option["count"] for option in questions[single["question_id"]]["options"]] == [1, 0]
    assert [option["count"] for option in questions[multi["question_id"]]["options"]] == [1, 1]
    assert questions[text["question_id"]]["answers"][0]["text"] == "First answer"
    assert questions[number["question_id"]]["average"] == 20
    assert questions[skipped["question_id"]]["answered_count"] == 0
    assert questions[skipped["question_id"]]["answers"] == []
    assert "respondent_id" not in statistics.text
    assert "username" not in statistics.text

    single_statistics = client.get(
        f"/api/statistics/surveys/{survey_id}/questions/{number['question_id']}"
    )
    assert single_statistics.status_code == 200, single_statistics.text
    assert single_statistics.json()["question"]["average"] == 20
    assert "questions" not in single_statistics.json()

    # A different account cannot inspect private creator statistics.
    client.post("/api/auth/logout")
    register_and_login(client, "statistics-outsider")
    assert client.get(f"/api/statistics/surveys/{survey_id}").status_code == 404
    assert (
        client.get(
            f"/api/statistics/surveys/{survey_id}/questions/{number['question_id']}"
        ).status_code
        == 404
    )
    assert client.get(
        f"/api/statistics/surveys/{survey_id}/questions/missing"
    ).status_code == 404

    assert isolated_test_database["responses"].count_documents(
        {"survey_id": ObjectId(survey_id), "status": "submitted"}
    ) == 1
