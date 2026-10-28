import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from bson import ObjectId

from backend.app.modules.questionnaires.repository import SurveyRepository
from backend.app.modules.questionnaires.schemas import QuestionBase


class SurveyNotFound(Exception):
    pass


class SurveyConflict(Exception):
    pass


class SurveyContentInvalid(Exception):
    pass


def _public_survey(document: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(document)
    result["id"] = str(result.pop("_id"))
    result["owner_id"] = str(result["owner_id"])
    result["questions"] = [dict(question) for question in result.get("questions", [])]
    for question in result["questions"]:
        library_ref = question.get("library_ref")
        if library_ref:
            question["library_ref"] = {
                key: str(value) if isinstance(value, ObjectId) else value
                for key, value in library_ref.items()
            }
    return result


def _parse_survey_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise SurveyNotFound
    return ObjectId(value)


class QuestionnaireService:
    def __init__(self, surveys: SurveyRepository, question_library=None):
        self._surveys = surveys
        self._question_library = question_library

    def create_survey(
        self,
        owner_id: ObjectId,
        *,
        title: str,
        description: str,
        anonymous: bool,
        allow_multiple_submissions: bool,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        survey = {
            "_id": ObjectId(),
            "owner_id": owner_id,
            "title": title,
            "description": description,
            "anonymous": anonymous,
            "allow_multiple_submissions": allow_multiple_submissions,
            "status": "draft",
            "deadline": None,
            "questions": [],
            "created_at": now,
            "updated_at": now,
            "published_at": None,
            "closed_at": None,
        }
        self._surveys.insert(survey)
        return _public_survey(survey)

    def list_surveys(self, owner_id: ObjectId) -> List[Dict[str, Any]]:
        return [_public_survey(document) for document in self._surveys.list_owned(owner_id)]

    def get_survey(self, survey_id: str, owner_id: ObjectId) -> Dict[str, Any]:
        document = self._find_owned(survey_id, owner_id)
        return _public_survey(document)

    def update_survey(
        self,
        survey_id: str,
        owner_id: ObjectId,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        current = self._find_owned(survey_id, owner_id)
        updates = {key: value for key, value in updates.items() if value is not None}
        if not updates:
            return _public_survey(current)

        policy_fields = {"anonymous", "allow_multiple_submissions"}.intersection(updates)
        if policy_fields and current["status"] != "draft":
            raise SurveyConflict("Filling policies cannot change after publication")

        fields = dict(updates)
        fields["updated_at"] = datetime.now(timezone.utc)
        saved = self._surveys.update_owned(
            current["_id"],
            owner_id,
            fields,
            required_status="draft" if policy_fields else None,
        )
        if saved is None:
            latest = self._surveys.find_owned(current["_id"], owner_id)
            if latest is None:
                raise SurveyNotFound
            raise SurveyConflict("Survey state changed while it was being updated")
        return _public_survey(saved)

    def set_deadline(
        self,
        survey_id: str,
        owner_id: ObjectId,
        deadline: Optional[datetime],
    ) -> Dict[str, Any]:
        current = self._find_owned(survey_id, owner_id)
        if current["status"] not in {"draft", "published"}:
            raise SurveyConflict("Deadline cannot be changed after the survey is closed")
        saved = self._surveys.update_owned(
            current["_id"],
            owner_id,
            {"deadline": deadline, "updated_at": datetime.now(timezone.utc)},
            required_status=current["status"],
        )
        if saved is None:
            raise SurveyConflict("Survey state changed while deadline was updated")
        return _public_survey(saved)

    def add_question(
        self, survey_id: str, owner_id: ObjectId, payload: QuestionBase
    ) -> Dict[str, Any]:
        current = self._find_draft(survey_id, owner_id)
        question = self._normalise_question(payload, question_id=str(uuid4()))
        questions = list(current["questions"]) + [question]
        self._validate_question(question, len(questions) - 1, questions)
        return self._save_questions(current, owner_id, questions)

    def add_question_from_library(
        self,
        survey_id: str,
        owner_id: ObjectId,
        source_question_id: str,
        source_version_id: str,
    ) -> Dict[str, Any]:
        if self._question_library is None:
            raise SurveyContentInvalid("Question library is not configured")
        current = self._find_draft(survey_id, owner_id)
        family, version = self._question_library.resolve_usable_version(
            owner_id, source_question_id, source_version_id
        )
        family_id = family["_id"]
        version_id = version["_id"]
        question = {
            key: version[key]
            for key in ("type", "prompt", "required", "options", "validation")
            if key in version
        }
        question["question_id"] = str(uuid4())
        # Jump targets are local to the source survey and are deliberately not copied.
        question["jump_rules"] = []
        question["library_ref"] = {
            "source_question_id": family_id,
            "source_version_id": version_id,
        }
        questions = list(current["questions"]) + [question]
        self._validate_question(question, len(questions) - 1, questions)
        return self._save_questions(current, owner_id, questions)

    def update_question(
        self,
        survey_id: str,
        question_id: str,
        owner_id: ObjectId,
        payload: QuestionBase,
        *,
        library_ref: Optional[Dict[str, ObjectId]] = None,
    ) -> Dict[str, Any]:
        current = self._find_draft(survey_id, owner_id)
        questions = list(current["questions"])
        index = self._question_index(questions, question_id)
        updated = self._normalise_question(payload, question_id=question_id)
        if library_ref is not None:
            updated["library_ref"] = dict(library_ref)
        questions[index] = updated
        self._validate_question(updated, index, questions)
        return self._save_questions(current, owner_id, questions)

    def delete_question(
        self, survey_id: str, question_id: str, owner_id: ObjectId
    ) -> Dict[str, Any]:
        current = self._find_draft(survey_id, owner_id)
        questions = list(current["questions"])
        index = self._question_index(questions, question_id)
        for question in questions:
            for rule in question.get("jump_rules", []):
                if rule.get("target_question_id") == question_id:
                    raise SurveyConflict(
                        "Remove jump rules targeting this question before deleting it"
                    )
        del questions[index]
        return self._save_questions(current, owner_id, questions)

    def publish(self, survey_id: str, owner_id: ObjectId) -> Dict[str, Any]:
        current = self._find_draft(survey_id, owner_id)
        questions = current["questions"]
        if not questions:
            raise SurveyContentInvalid("A survey needs at least one question")
        question_ids = [question.get("question_id") for question in questions]
        if len(question_ids) != len(set(question_ids)):
            raise SurveyContentInvalid("Question IDs must be unique within a survey")
        for index, question in enumerate(questions):
            self._validate_question_document(question, index, questions)

        now = datetime.now(timezone.utc)
        saved = self._surveys.change_status(
            current["_id"],
            owner_id,
            "draft",
            {"status": "published", "published_at": now, "updated_at": now},
            expected_fields={"questions": questions},
        )
        if saved is None:
            raise SurveyConflict("Survey state changed while it was being published")
        return _public_survey(saved)

    def close(self, survey_id: str, owner_id: ObjectId) -> Dict[str, Any]:
        current = self._find_owned(survey_id, owner_id)
        if current["status"] == "closed":
            return _public_survey(current)
        if current["status"] != "published":
            raise SurveyConflict("Only published surveys can be closed")
        now = datetime.now(timezone.utc)
        saved = self._surveys.change_status(
            current["_id"],
            owner_id,
            "published",
            {"status": "closed", "closed_at": now, "updated_at": now},
        )
        if saved is None:
            raise SurveyConflict("Survey state changed while it was being closed")
        return _public_survey(saved)

    def _save_questions(
        self,
        current: Dict[str, Any],
        owner_id: ObjectId,
        questions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        saved = self._surveys.update_owned(
            current["_id"],
            owner_id,
            {"questions": questions, "updated_at": datetime.now(timezone.utc)},
            required_status="draft",
            expected_fields={"questions": current["questions"]},
        )
        if saved is None:
            latest = self._surveys.find_owned(current["_id"], owner_id)
            if latest is None:
                raise SurveyNotFound
            if latest["status"] != "draft":
                raise SurveyConflict("Questions can only be edited in draft surveys")
            raise SurveyConflict("Survey questions changed during this edit")
        return _public_survey(saved)

    def _find_owned(self, survey_id: str, owner_id: ObjectId) -> Dict[str, Any]:
        document = self._surveys.find_owned(_parse_survey_id(survey_id), owner_id)
        if document is None:
            raise SurveyNotFound
        return document

    def _find_draft(self, survey_id: str, owner_id: ObjectId) -> Dict[str, Any]:
        document = self._find_owned(survey_id, owner_id)
        if document["status"] != "draft":
            raise SurveyConflict("Only draft surveys can be edited")
        return document

    @staticmethod
    def _question_index(questions: Sequence[Dict[str, Any]], question_id: str) -> int:
        for index, question in enumerate(questions):
            if question.get("question_id") == question_id:
                return index
        raise SurveyNotFound

    @staticmethod
    def _normalise_question(payload: QuestionBase, question_id: str) -> Dict[str, Any]:
        question = payload.model_dump(exclude_none=True)
        question["question_id"] = question_id
        if "options" in question:
            seen = set()
            for option in question["options"]:
                if not option.get("option_id"):
                    option["option_id"] = str(uuid4())
                if option["option_id"] in seen:
                    raise SurveyContentInvalid("Option IDs must be unique per question")
                seen.add(option["option_id"])
        question["jump_rules"] = sorted(
            question.get("jump_rules", []), key=lambda rule: rule["priority"]
        )
        return question

    def _validate_question(
        self,
        question: Dict[str, Any],
        index: int,
        questions: Sequence[Dict[str, Any]],
    ) -> None:
        self._validate_question_document(question, index, questions)

    @staticmethod
    def _validate_question_document(
        question: Dict[str, Any],
        index: int,
        questions: Sequence[Dict[str, Any]],
    ) -> None:
        qtype = question.get("type")
        options = question.get("options", [])
        option_ids = [option.get("option_id") for option in options]
        if any(not isinstance(option_id, str) for option_id in option_ids):
            raise SurveyContentInvalid("Option IDs must be strings")
        if len(option_ids) != len(set(option_ids)):
            raise SurveyContentInvalid("Option IDs must be unique per question")

        validation = question.get("validation", {})
        if qtype == "multi_choice":
            minimum = validation.get("min_selected")
            maximum = validation.get("max_selected")
            if minimum is None or maximum is None or minimum > maximum:
                raise SurveyContentInvalid("Invalid multi-choice selection bounds")
            if maximum > len(options):
                raise SurveyContentInvalid("max_selected cannot exceed option count")
        elif qtype == "text":
            minimum, maximum = validation.get("min_length"), validation.get("max_length")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise SurveyContentInvalid("min_length cannot exceed max_length")
        elif qtype == "number":
            minimum, maximum = validation.get("min_value"), validation.get("max_value")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise SurveyContentInvalid("min_value cannot exceed max_value")

        priorities = [rule.get("priority") for rule in question.get("jump_rules", [])]
        if len(priorities) != len(set(priorities)):
            raise SurveyContentInvalid("Jump rule priorities must be unique per question")
        id_to_index = {
            item.get("question_id"): item_index
            for item_index, item in enumerate(questions)
        }
        for rule in question.get("jump_rules", []):
            target = rule.get("target_question_id")
            if target not in id_to_index or id_to_index[target] <= index:
                raise SurveyContentInvalid(
                    "Jump targets must refer to a later question in this survey"
                )
            condition = rule.get("condition")
            if not isinstance(condition, dict):
                raise SurveyContentInvalid("Jump condition must be an object")
            if qtype == "single_choice":
                if set(condition) != {"operator", "option_id"}:
                    raise SurveyContentInvalid("Invalid single-choice jump condition")
                if (
                    condition.get("operator") != "equals"
                    or not isinstance(condition.get("option_id"), str)
                    or condition["option_id"] not in option_ids
                ):
                    raise SurveyContentInvalid("Jump condition references an invalid option")
            elif qtype == "multi_choice":
                if set(condition) != {"match", "option_ids"}:
                    raise SurveyContentInvalid("Invalid multi-choice jump condition")
                referenced = condition["option_ids"]
                if (
                    not isinstance(condition.get("match"), str)
                    or condition["match"] not in {"ANY", "ALL"}
                    or not isinstance(referenced, list)
                    or not referenced
                    or any(not isinstance(option_id, str) for option_id in referenced)
                    or len(referenced) != len(set(referenced))
                    or any(option_id not in option_ids for option_id in referenced)
                ):
                    raise SurveyContentInvalid("Jump condition references invalid options")
            elif qtype == "number":
                if set(condition) != {"operator", "value"}:
                    raise SurveyContentInvalid("Invalid numeric jump condition")
                rule_value = condition["value"]
                valid_numeric_value = (
                    not isinstance(rule_value, bool)
                    and isinstance(rule_value, (int, float))
                )
                if valid_numeric_value:
                    try:
                        valid_numeric_value = math.isfinite(float(rule_value))
                    except (OverflowError, ValueError):
                        valid_numeric_value = False
                if (
                    not isinstance(condition.get("operator"), str)
                    or condition["operator"] not in {"eq", "ne", "gt", "gte", "lt", "lte"}
                    or not valid_numeric_value
                ):
                    raise SurveyContentInvalid("Invalid numeric jump comparison")
            else:
                raise SurveyContentInvalid("Text questions cannot have jump rules")
