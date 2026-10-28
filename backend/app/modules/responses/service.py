from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from backend.app.modules.questionnaires.repository import SurveyRepository
from backend.app.modules.responses.engine import (
    InvalidAnswer,
    InvalidStoredSteps,
    PathState,
    evaluate_path,
    validate_answer,
)
from backend.app.modules.responses.repository import ResponseRepository


class SurveyNotAvailable(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class FillSurveyNotFound(Exception):
    pass


class ResponseNotFound(Exception):
    pass


class ResponseConflict(Exception):
    pass


class ResponseContentInvalid(Exception):
    pass


class ResponseIncomplete(Exception):
    def __init__(self, question_id: Optional[str]):
        self.question_id = question_id


def _parse_object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise FillSurveyNotFound
    return ObjectId(value)


class ResponseService:
    def __init__(
        self,
        surveys: SurveyRepository,
        responses: ResponseRepository,
    ):
        self._surveys = surveys
        self._responses = responses

    def get_shared_survey(self, survey_id: str) -> Dict[str, Any]:
        survey = self._find_shared_survey(survey_id)
        now = datetime.now(timezone.utc)
        deadline = survey.get("deadline")
        deadline_passed = deadline is not None and now >= deadline
        is_accepting = survey["status"] == "published" and not deadline_passed
        if survey["status"] == "closed":
            closed_reason = "closed"
        elif deadline_passed:
            closed_reason = "deadline"
        else:
            closed_reason = None

        # Only published survey content is public. Never serialize owner_id or
        # internal collection fields through the share-link API.
        public_questions = []
        for question in survey.get("questions", []):
            public_question = {
                key: question[key]
                for key in (
                    "question_id",
                    "type",
                    "prompt",
                    "required",
                    "options",
                    "validation",
                    "jump_rules",
                )
                if key in question
            }
            public_questions.append(public_question)

        return {
            "id": str(survey["_id"]),
            "title": survey["title"],
            "description": survey.get("description", ""),
            "anonymous": survey["anonymous"],
            "status": survey["status"],
            "deadline": deadline,
            "accepting_responses": is_accepting,
            "closed_reason": closed_reason,
            "questions": public_questions,
        }

    def start_or_resume(
        self,
        survey_id: str,
        respondent_id: ObjectId,
    ) -> Dict[str, Any]:
        survey_oid = _parse_object_id(survey_id)
        survey = self._find_shared_survey_by_id(survey_oid)
        self._ensure_accepting(survey)
        allow_multiple = survey["allow_multiple_submissions"]

        if not allow_multiple and self._responses.has_single_submission(
            survey_oid, respondent_id
        ):
            raise ResponseConflict("A response has already been submitted")

        draft = self._responses.find_draft(survey_oid, respondent_id)
        if draft is not None:
            return self._public_response(survey, draft)

        now = datetime.now(timezone.utc)
        response = {
            "_id": ObjectId(),
            "survey_id": survey_oid,
            "respondent_id": respondent_id,
            "status": "draft",
            "lifecycle_guard": "draft" if allow_multiple else "single",
            "revision": 0,
            "steps": [],
            "created_at": now,
            "updated_at": now,
        }

        # The unique lifecycle index arbitrates simultaneous start requests.
        # A loser resumes the winning draft; if it was just submitted, an
        # allow-repeat survey may create its next draft after a single retry.
        for attempt in range(2):
            try:
                self._responses.insert(response)
                return self._public_response(survey, response)
            except DuplicateKeyError:
                draft = self._responses.find_draft(survey_oid, respondent_id)
                if draft is not None:
                    return self._public_response(survey, draft)
                if not allow_multiple and self._responses.has_single_submission(
                    survey_oid, respondent_id
                ):
                    raise ResponseConflict("A response has already been submitted")
                if attempt == 1:
                    break
                response["_id"] = ObjectId()
        raise ResponseConflict("Could not create or restore the response draft")

    def get_response(
        self,
        survey_id: str,
        response_id: str,
        respondent_id: ObjectId,
    ) -> Dict[str, Any]:
        survey = self._find_shared_survey(survey_id)
        response = self._find_respondent_response(
            survey["_id"], response_id, respondent_id
        )
        return self._public_response(survey, response)

    def save_step(
        self,
        survey_id: str,
        response_id: str,
        question_id: str,
        respondent_id: ObjectId,
        *,
        revision: int,
        answer: Optional[Dict[str, Any]],
        skip: bool,
    ) -> Dict[str, Any]:
        survey = self._find_shared_survey(survey_id)
        self._ensure_accepting(survey)
        response = self._find_respondent_response(
            survey["_id"], response_id, respondent_id
        )
        if response["status"] != "draft":
            raise ResponseConflict("Submitted responses cannot be changed")
        self._check_revision(response, revision)

        question_by_id = {
            question["question_id"]: question
            for question in survey.get("questions", [])
        }
        question = question_by_id.get(question_id)
        if question is None:
            raise ResponseNotFound

        try:
            current_path = evaluate_path(survey, response.get("steps", []))
        except InvalidStoredSteps as exc:
            raise ResponseContentInvalid(str(exc)) from exc
        if question_id not in current_path.path_question_ids:
            raise ResponseConflict("Question is not on the current response path")

        if skip:
            if answer is not None:
                raise ResponseContentInvalid("A skipped step cannot include an answer")
            if question.get("required"):
                raise ResponseContentInvalid("Required questions cannot be skipped")
            new_step = {"question_id": question_id, "status": "skipped"}
        else:
            if answer is None:
                raise ResponseContentInvalid("Provide an answer or explicitly skip")
            try:
                normalized_answer = validate_answer(question, answer)
            except InvalidAnswer as exc:
                raise ResponseContentInvalid(str(exc)) from exc
            new_step = {
                "question_id": question_id,
                "status": "answered",
                "value": normalized_answer,
            }

        steps_by_id = {
            step["question_id"]: step for step in response.get("steps", [])
        }
        steps_by_id[question_id] = new_step
        candidate_steps = list(steps_by_id.values())
        try:
            new_path = evaluate_path(survey, candidate_steps)
        except InvalidStoredSteps as exc:
            raise ResponseContentInvalid(str(exc)) from exc

        # Rewriting only the reachable, ordered steps atomically drops every
        # answer made unreachable by an edited earlier answer.
        clean_steps = [
            steps_by_id[reachable_id]
            for reachable_id in new_path.path_question_ids
            if reachable_id in steps_by_id
        ]
        saved = self._responses.save_steps(
            response["_id"],
            survey["_id"],
            respondent_id,
            revision,
            clean_steps,
            datetime.now(timezone.utc),
        )
        if saved is None:
            self._raise_write_conflict(survey["_id"], response["_id"], respondent_id)
        return self._public_response(survey, saved)

    def submit(
        self,
        survey_id: str,
        response_id: str,
        respondent_id: ObjectId,
        *,
        revision: int,
    ) -> Dict[str, Any]:
        survey = self._find_shared_survey(survey_id)
        self._ensure_accepting(survey)
        response = self._find_respondent_response(
            survey["_id"], response_id, respondent_id
        )
        if response["status"] != "draft":
            raise ResponseConflict("Response has already been submitted")
        self._check_revision(response, revision)

        try:
            path = evaluate_path(survey, response.get("steps", []))
        except InvalidStoredSteps as exc:
            raise ResponseContentInvalid(str(exc)) from exc
        if not path.complete:
            raise ResponseIncomplete(path.next_question_id)

        question_by_id = {
            question["question_id"]: question
            for question in survey.get("questions", [])
        }
        steps_by_id = {
            step["question_id"]: step for step in response.get("steps", [])
        }
        if set(steps_by_id) != set(path.path_question_ids):
            raise ResponseContentInvalid("Response contains steps outside the current path")
        for question_id in path.path_question_ids:
            question = question_by_id[question_id]
            step = steps_by_id[question_id]
            if step["status"] == "skipped":
                if question.get("required"):
                    raise ResponseContentInvalid("Required questions cannot be skipped")
                continue
            try:
                validate_answer(question, step.get("value"))
            except InvalidAnswer as exc:
                raise ResponseContentInvalid(str(exc)) from exc

        try:
            submitted = self._responses.submit(
                response["_id"],
                survey["_id"],
                respondent_id,
                revision,
                datetime.now(timezone.utc),
                allow_multiple_submissions=survey["allow_multiple_submissions"],
            )
        except DuplicateKeyError as exc:
            raise ResponseConflict("A response has already been submitted") from exc
        if submitted is None:
            self._raise_write_conflict(survey["_id"], response["_id"], respondent_id)
        return self._public_response(survey, submitted)

    def _find_shared_survey(self, survey_id: str) -> Dict[str, Any]:
        return self._find_shared_survey_by_id(_parse_object_id(survey_id))

    def _find_shared_survey_by_id(self, survey_id: ObjectId) -> Dict[str, Any]:
        survey = self._surveys.find_fillable_or_closed(survey_id)
        if survey is None:
            raise FillSurveyNotFound
        return survey

    @staticmethod
    def _ensure_accepting(survey: Dict[str, Any]) -> None:
        if survey["status"] == "closed":
            raise SurveyNotAvailable("closed")
        deadline = survey.get("deadline")
        if deadline is not None and datetime.now(timezone.utc) >= deadline:
            raise SurveyNotAvailable("deadline")

    def _find_respondent_response(
        self,
        survey_id: ObjectId,
        response_id: str,
        respondent_id: ObjectId,
    ) -> Dict[str, Any]:
        if not ObjectId.is_valid(response_id):
            raise ResponseNotFound
        response = self._responses.find_for_respondent(
            ObjectId(response_id), survey_id, respondent_id
        )
        if response is None:
            raise ResponseNotFound
        return response

    @staticmethod
    def _check_revision(response: Dict[str, Any], revision: int) -> None:
        if response["revision"] != revision:
            raise ResponseConflict("Response revision is stale")

    def _raise_write_conflict(
        self,
        survey_id: ObjectId,
        response_id: ObjectId,
        respondent_id: ObjectId,
    ) -> None:
        latest = self._responses.find_for_respondent(
            response_id, survey_id, respondent_id
        )
        if latest is None:
            raise ResponseNotFound
        if latest["status"] != "draft":
            raise ResponseConflict("Submitted responses cannot be changed")
        raise ResponseConflict("Response revision is stale")

    @staticmethod
    def _public_response(
        survey: Dict[str, Any], response: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            path = evaluate_path(survey, response.get("steps", []))
        except InvalidStoredSteps as exc:
            raise ResponseContentInvalid(str(exc)) from exc
        result = {
            "id": str(response["_id"]),
            "survey_id": str(response["survey_id"]),
            "status": response["status"],
            "revision": response["revision"],
            "steps": response.get("steps", []),
            "path_question_ids": path.path_question_ids,
            "next_question_id": path.next_question_id,
            "complete": path.complete,
            "created_at": response["created_at"],
            "updated_at": response["updated_at"],
        }
        if response.get("submitted_at") is not None:
            result["submitted_at"] = response["submitted_at"]
        return result
