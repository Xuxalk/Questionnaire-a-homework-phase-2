from fastapi.testclient import TestClient


def register_and_login(client: TestClient, username: str = "survey-owner") -> None:
    registered = client.post(
        "/api/auth/register",
        json={"username": username, "password": "Correct horse 42!"},
    )
    assert registered.status_code == 201
    logged_in = client.post(
        "/api/auth/login",
        json={"username": username, "password": "Correct horse 42!"},
    )
    assert logged_in.status_code == 200


def create_survey(client: TestClient, title: str = "Sample survey") -> dict:
    response = client.post(
        "/api/surveys",
        json={
            "title": title,
            "description": "Draft description",
            "anonymous": True,
            "allow_multiple_submissions": False,
        },
    )
    assert response.status_code == 201
    return response.json()


def add_question(client: TestClient, survey_id: str, payload: dict) -> dict:
    response = client.post(f"/api/surveys/{survey_id}/questions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_questionnaire_owner_scope_and_draft_crud(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    unauthenticated_create = client.post(
        "/api/surveys",
        json={
            "title": "No session",
            "description": "",
            "anonymous": False,
            "allow_multiple_submissions": False,
        },
    )
    assert unauthenticated_create.status_code == 401
    register_and_login(client)
    survey = create_survey(client)
    survey_id = survey["id"]

    assert survey["status"] == "draft"
    assert survey["questions"] == []
    assert client.post(f"/api/surveys/{survey_id}/publish").status_code == 422
    listing = client.get("/api/surveys")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [survey_id]
    assert client.get(f"/api/surveys/{survey_id}").status_code == 200

    changed = client.patch(
        f"/api/surveys/{survey_id}",
        json={"title": "Updated title", "anonymous": False},
    )
    assert changed.status_code == 200
    assert changed.json()["title"] == "Updated title"
    assert changed.json()["anonymous"] is False

    deadline = client.put(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2030-01-02T03:04:05+08:00"},
    )
    assert deadline.status_code == 200
    assert deadline.json()["deadline"] == "2030-01-01T19:04:05+00:00"

    client.post("/api/auth/logout")
    register_and_login(client, "another-owner")
    assert client.get(f"/api/surveys/{survey_id}").status_code == 404
    assert client.patch(f"/api/surveys/{survey_id}", json={"title": "stolen"}).status_code == 404
    assert client.get("/api/surveys").json() == []


def test_four_question_types_rules_publish_and_locked_fields(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client)
    survey_id = create_survey(client)["id"]

    survey = add_question(
        client,
        survey_id,
        {
            "type": "single_choice",
            "prompt": "Choose one",
            "required": True,
            "options": [{"label": "Yes"}, {"label": "No"}],
        },
    )
    single = survey["questions"][0]
    single_id = single["question_id"]
    yes_option = single["options"][0]["option_id"]

    survey = add_question(
        client,
        survey_id,
        {
            "type": "multi_choice",
            "prompt": "Choose several",
            "required": False,
            "options": [{"label": "A"}, {"label": "B"}],
            "validation": {"min_selected": 1, "max_selected": 2},
        },
    )
    multi = survey["questions"][1]
    multi_id = multi["question_id"]
    option_a = multi["options"][0]["option_id"]

    survey = add_question(
        client,
        survey_id,
        {
            "type": "text",
            "prompt": "Comments",
            "required": False,
            "validation": {"min_length": 0, "max_length": 500},
        },
    )
    text_id = survey["questions"][2]["question_id"]

    survey = add_question(
        client,
        survey_id,
        {
            "type": "number",
            "prompt": "Age",
            "required": True,
            "validation": {"min_value": 0, "max_value": 120, "integer_only": True},
        },
    )
    number_id = survey["questions"][3]["question_id"]

    survey = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "Final note", "required": False},
    )
    final_id = survey["questions"][4]["question_id"]

    # Adding a later question first allows earlier questions to target it.
    single_update = client.patch(
        f"/api/surveys/{survey_id}/questions/{single_id}",
        json={
            "type": "single_choice",
            "prompt": "Choose one",
            "required": True,
            "options": single["options"],
            "jump_rules": [
                {
                    "priority": 1,
                    "condition": {"operator": "equals", "option_id": yes_option},
                    "target_question_id": text_id,
                }
            ],
        },
    )
    assert single_update.status_code == 200

    multi_update = client.patch(
        f"/api/surveys/{survey_id}/questions/{multi_id}",
        json={
            "type": "multi_choice",
            "prompt": "Choose several",
            "required": False,
            "options": multi["options"],
            "validation": {"min_selected": 1, "max_selected": 2},
            "jump_rules": [
                {
                    "priority": 2,
                    "condition": {"match": "ALL", "option_ids": [option_a]},
                    "target_question_id": final_id,
                },
                {
                    "priority": 1,
                    "condition": {"match": "ANY", "option_ids": [option_a]},
                    "target_question_id": number_id,
                },
            ],
        },
    )
    assert multi_update.status_code == 200
    saved_multi_rules = multi_update.json()["questions"][1]["jump_rules"]
    assert [rule["priority"] for rule in saved_multi_rules] == [1, 2]

    number_update = client.patch(
        f"/api/surveys/{survey_id}/questions/{number_id}",
        json={
            "type": "number",
            "prompt": "Age",
            "required": True,
            "validation": {"min_value": 0, "max_value": 120, "integer_only": True},
            "jump_rules": [
                {
                    "priority": 1,
                    "condition": {"operator": "gte", "value": 100},
                    "target_question_id": final_id,
                }
            ],
        },
    )
    assert number_update.status_code == 200

    # Updating a question preserves its question ID and supports text limits.
    text_update = client.patch(
        f"/api/surveys/{survey_id}/questions/{text_id}",
        json={
            "type": "text",
            "prompt": "Comments updated",
            "required": False,
            "validation": {"min_length": 2, "max_length": 500},
        },
    )
    assert text_update.status_code == 200
    assert text_update.json()["questions"][2]["question_id"] == text_id

    published = client.post(f"/api/surveys/{survey_id}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["published_at"]

    locked_question_edit = client.patch(
        f"/api/surveys/{survey_id}/questions/{single_id}",
        json={
            "type": "single_choice",
            "prompt": "Changed after publishing",
            "required": True,
            "options": single["options"],
        },
    )
    assert locked_question_edit.status_code == 409
    assert client.post(
        f"/api/surveys/{survey_id}/questions",
        json={"type": "text", "prompt": "Late", "required": False},
    ).status_code == 409
    assert client.patch(
        f"/api/surveys/{survey_id}", json={"allow_multiple_submissions": True}
    ).status_code == 409

    # Descriptive edits and the operational deadline remain available while open.
    title_edit = client.patch(
        f"/api/surveys/{survey_id}", json={"title": "Published title"}
    )
    assert title_edit.status_code == 200
    deadline = client.put(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2030-01-01T00:00:00Z"},
    )
    assert deadline.status_code == 200

    closed = client.post(f"/api/surveys/{survey_id}/close")
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert client.put(
        f"/api/surveys/{survey_id}/deadline", json={"deadline": None}
    ).status_code == 409
    assert client.post(f"/api/surveys/{survey_id}/close").status_code == 200


def test_question_validation_and_referenced_question_cannot_be_deleted(
    authenticated_api_client: TestClient,
) -> None:
    client = authenticated_api_client
    register_and_login(client)
    survey_id = create_survey(client)["id"]

    invalid_multi = client.post(
        f"/api/surveys/{survey_id}/questions",
        json={
            "type": "multi_choice",
            "prompt": "Choose",
            "required": True,
            "options": [{"label": "Only option"}],
            "validation": {"min_selected": 1, "max_selected": 2},
        },
    )
    assert invalid_multi.status_code == 422

    source = add_question(
        client,
        survey_id,
        {
            "type": "single_choice",
            "prompt": "Source",
            "required": True,
            "options": [{"label": "Go"}],
        },
    )["questions"][0]
    target = add_question(
        client,
        survey_id,
        {"type": "text", "prompt": "Target", "required": False},
    )["questions"][1]

    updated = client.patch(
        f"/api/surveys/{survey_id}/questions/{source['question_id']}",
        json={
            "type": "single_choice",
            "prompt": "Source",
            "required": True,
            "options": source["options"],
            "jump_rules": [
                {
                    "priority": 1,
                    "condition": {
                        "operator": "equals",
                        "option_id": source["options"][0]["option_id"],
                    },
                    "target_question_id": target["question_id"],
                }
            ],
        },
    )
    assert updated.status_code == 200

    cannot_delete_target = client.delete(
        f"/api/surveys/{survey_id}/questions/{target['question_id']}"
    )
    assert cannot_delete_target.status_code == 409

    invalid_backwards_jump = client.patch(
        f"/api/surveys/{survey_id}/questions/{source['question_id']}",
        json={
            "type": "single_choice",
            "prompt": "Source",
            "required": True,
            "options": source["options"],
            "jump_rules": [
                {
                    "priority": 1,
                    "condition": {
                        "operator": "equals",
                        "option_id": source["options"][0]["option_id"],
                    },
                    "target_question_id": "missing-question",
                }
            ],
        },
    )
    assert invalid_backwards_jump.status_code == 422

    deleted_source = client.delete(
        f"/api/surveys/{survey_id}/questions/{source['question_id']}"
    )
    assert deleted_source.status_code == 200
    assert all(
        question["question_id"] != source["question_id"]
        for question in deleted_source.json()["questions"]
    )
