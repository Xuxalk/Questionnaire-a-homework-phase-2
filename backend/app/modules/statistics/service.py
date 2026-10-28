import json
import math
from typing import Any, Dict, List, Optional

from bson import ObjectId

from backend.app.modules.question_library.repository import QuestionLibraryRepository
from backend.app.modules.questionnaires.repository import SurveyRepository
from backend.app.modules.statistics.repository import StatisticsRepository


class StatisticsNotFound(Exception):
    pass


def _object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise StatisticsNotFound
    return ObjectId(value)


class StatisticsService:
    def __init__(
        self,
        surveys: SurveyRepository,
        responses: StatisticsRepository,
        question_library: QuestionLibraryRepository,
    ):
        self._surveys = surveys
        self._responses = responses
        self._question_library = question_library

    def get_survey_statistics(
        self, survey_id: str, owner_id: ObjectId
    ) -> Dict[str, Any]:
        survey = self._find_owned_survey(survey_id, owner_id)
        question_stats = self._aggregate_questions(survey, survey["questions"])
        return {
            "survey_id": str(survey["_id"]),
            "title": survey["title"],
            "status": survey["status"],
            "submitted_count": self._responses.count_submitted(survey["_id"]),
            "questions": question_stats,
        }

    def get_question_statistics(
        self, survey_id: str, question_id: str, owner_id: ObjectId
    ) -> Dict[str, Any]:
        survey = self._find_owned_survey(survey_id, owner_id)
        question = next(
            (
                item
                for item in survey.get("questions", [])
                if item.get("question_id") == question_id
            ),
            None,
        )
        if question is None:
            raise StatisticsNotFound
        stats = self._aggregate_questions(
            survey, [question], filter_question_id=question_id
        )[0]
        return {
            "survey_id": str(survey["_id"]),
            "title": survey["title"],
            "submitted_count": self._responses.count_submitted(survey["_id"]),
            "question": stats,
        }

    def list_library_question_versions(
        self, family_id: str, user_id: ObjectId
    ) -> Dict[str, Any]:
        family_oid = _object_id(family_id)
        family = self._question_library.find_family(family_oid)
        if family is None:
            raise StatisticsNotFound
        is_owner = family.get("owner_id") == user_id
        all_versions = list(self._question_library.list_versions(family_oid))
        if is_owner:
            versions = all_versions
        else:
            shared_version_ids = {
                share["version_id"]
                for share in self._question_library.list_received_shares(user_id)
                if share.get("family_id") == family_oid
                and share.get("owner_id") == family.get("owner_id")
            }
            versions = [
                version for version in all_versions if version["_id"] in shared_version_ids
            ]
            if not versions:
                raise StatisticsNotFound
        return {
            "family_id": str(family_oid),
            "access_scope": "owner" if is_owner else "shared_versions",
            "versions": [
                self._statistics_version(version, include_history=is_owner)
                for version in versions
            ],
        }

    def get_library_version_statistics(
        self,
        family_id: str,
        version_id: str,
        user_id: ObjectId,
        *,
        survey_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        family_oid = _object_id(family_id)
        version_oid = _object_id(version_id)
        family = self._question_library.find_family(family_oid)
        version = self._question_library.find_family_version(family_oid, version_oid)
        if family is None or version is None:
            raise StatisticsNotFound
        is_owner = family.get("owner_id") == user_id
        if not is_owner and self._question_library.find_active_share(
            family_oid, version_oid, user_id
        ) is None:
            # Sharing a source question never grants access to anyone else's answers.
            raise StatisticsNotFound

        survey_oid = _object_id(survey_id) if survey_id is not None else None
        if survey_oid is not None:
            selected_survey = self._surveys.find_owned(survey_oid, user_id)
            if selected_survey is None or selected_survey.get("status") not in {
                "published",
                "closed",
            }:
                raise StatisticsNotFound

        version_content = {
            key: version[key]
            for key in ("type", "prompt", "required", "options", "validation")
            if key in version
        }
        aggregate_stats = self._new_library_question_stats(version_content)
        aggregate_stats["question_instance_count"] = 0
        survey_instances: Dict[tuple, Dict[str, Any]] = {}
        compatible_survey_ids = set()
        matched_survey_ids = set()
        incompatible_instance_count = 0

        rows = self._responses.library_version_answer_rows(
            owner_id=user_id,
            family_id=family_oid,
            version_id=version_oid,
            survey_id=survey_oid,
        )
        for row in rows:
            survey_oid_row = row["survey_id"]
            question = row["question"]
            key = (survey_oid_row, question.get("question_id"))
            matched_survey_ids.add(survey_oid_row)
            instance = survey_instances.get(key)
            if instance is None:
                compatible = self._compatible_question_schema(version_content, question)
                instance = {
                    "survey_id": str(survey_oid_row),
                    "title": row.get("title", ""),
                    "survey_status": row.get("survey_status"),
                    "question_id": question.get("question_id"),
                    "prompt": question.get("prompt", ""),
                    "type": question.get("type"),
                    "compatible": compatible,
                    "excluded_reason": None if compatible else (
                        "题目类型、选项或校验规则与所选版本不兼容，未纳入汇总。"
                    ),
                    "stats": self._new_library_question_stats(question),
                }
                survey_instances[key] = instance
                if compatible:
                    aggregate_stats["question_instance_count"] += 1
                    compatible_survey_ids.add(survey_oid_row)
                else:
                    incompatible_instance_count += 1

            answer = row.get("answer")
            if answer is None or not instance["compatible"]:
                continue
            context = {
                "survey_id": instance["survey_id"],
                "survey_title": instance["title"],
                "question_id": instance["question_id"],
            }
            if self._record_library_answer(
                instance["stats"], answer, row.get("submitted_at")
            ):
                self._record_library_answer(
                    aggregate_stats,
                    answer,
                    row.get("submitted_at"),
                    context=context,
                )

        per_survey: Dict[ObjectId, Dict[str, Any]] = {}
        for (survey_oid_row, _), instance in survey_instances.items():
            entry = per_survey.setdefault(
                survey_oid_row,
                {
                    "survey_id": instance["survey_id"],
                    "title": instance["title"],
                    "status": instance["survey_status"],
                    "question_instances": [],
                },
            )
            instance_result = {
                key: value for key, value in instance.items() if key != "stats"
            }
            instance_result.update(
                self._finalize_library_question_stats(instance["stats"])
            )
            entry["question_instances"].append(instance_result)

        survey_results = sorted(
            per_survey.values(),
            key=lambda item: (item["title"].casefold(), item["survey_id"]),
        )
        for survey in survey_results:
            survey["question_instances"].sort(
                key=lambda item: item["question_id"] or ""
            )

        aggregate_result = self._finalize_library_question_stats(aggregate_stats)
        aggregate_result.update(
            {
                "matched_survey_count": len(matched_survey_ids),
                "included_survey_count": len(compatible_survey_ids),
                "incompatible_survey_count": len(
                    matched_survey_ids - compatible_survey_ids
                ),
                "incompatible_question_instance_count": incompatible_instance_count,
            }
        )
        return {
            "family_id": str(family_oid),
            "version": self._statistics_version(version, include_history=is_owner),
            "access_scope": "owner" if is_owner else "shared_version",
            "scope": "仅当前用户有权查看统计的问卷；严格限定此不可变题目版本。",
            "unit": "每个问卷内题目实例中的有效已提交回答；同一问卷重复使用会分别计数。",
            "selected_survey_id": str(survey_oid) if survey_oid else None,
            "aggregate": aggregate_result,
            "surveys": survey_results,
        }

    @staticmethod
    def _statistics_version(
        version: Dict[str, Any], *, include_history: bool
    ) -> Dict[str, Any]:
        result = {
            "id": str(version["_id"]),
            "version_number": version["version_number"],
            "type": version["type"],
            "prompt": version["prompt"],
            "required": version["required"],
            "created_at": version["created_at"],
        }
        for key in ("options", "validation"):
            if key in version:
                result[key] = version[key]
        if include_history:
            result["parent_version_id"] = (
                str(version["parent_version_id"])
                if version.get("parent_version_id")
                else None
            )
        return result

    @staticmethod
    def _compatibility_signature(question: Dict[str, Any]) -> tuple:
        options = question.get("options") or []
        normalized_options = tuple(
            sorted(
                (str(option.get("option_id")), str(option.get("label", "")))
                for option in options
            )
        )
        validation = json.dumps(
            question.get("validation") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return question.get("type"), normalized_options, validation

    @classmethod
    def _compatible_question_schema(
        cls, version: Dict[str, Any], question: Dict[str, Any]
    ) -> bool:
        # Prompt and required status may be locally adapted; answer semantics may not.
        return cls._compatibility_signature(version) == cls._compatibility_signature(question)

    @staticmethod
    def _new_library_question_stats(question: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "prompt": question.get("prompt", ""),
            "type": question.get("type"),
            "answered_count": 0,
            "valid_answer_count": 0,
        }
        if question.get("question_id") is not None:
            result["question_id"] = question["question_id"]
        if question.get("type") in {"single_choice", "multi_choice"}:
            result["options"] = [
                {
                    "option_id": option["option_id"],
                    "label": option["label"],
                    "count": 0,
                }
                for option in question.get("options", [])
            ]
        elif question.get("type") == "text":
            result["answers"] = []
        elif question.get("type") == "number":
            validation = question.get("validation") or {}
            result.update(
                {
                    "_sum": 0.0,
                    "minimum": None,
                    "maximum": None,
                    "integer_only": bool(validation.get("integer_only", False)),
                    "distribution": {},
                }
            )
        return result

    @staticmethod
    def _record_library_answer(
        stats: Dict[str, Any],
        answer: Any,
        submitted_at: Any,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not isinstance(answer, dict):
            return False
        answer_type = stats.get("type")
        if answer_type == "single_choice":
            option_id = answer.get("option_id")
            if not isinstance(option_id, str):
                return False
            options = {option["option_id"]: option for option in stats["options"]}
            if option_id not in options:
                return False
            options[option_id]["count"] += 1
        elif answer_type == "multi_choice":
            selected = answer.get("option_ids")
            if (
                not isinstance(selected, list)
                or any(not isinstance(item, str) for item in selected)
                or len(selected) != len(set(selected))
            ):
                return False
            options = {option["option_id"]: option for option in stats["options"]}
            if any(option_id not in options for option_id in selected):
                return False
            for option_id in selected:
                options[option_id]["count"] += 1
        elif answer_type == "text":
            value = answer.get("text")
            if not isinstance(value, str):
                return False
            row = {"text": value, "submitted_at": submitted_at}
            if context:
                row.update(context)
            stats["answers"].append(row)
        elif answer_type == "number":
            value = answer.get("number")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            if not math.isfinite(float(value)):
                return False
            stats["_sum"] += value
            stats["minimum"] = value if stats["minimum"] is None else min(stats["minimum"], value)
            stats["maximum"] = value if stats["maximum"] is None else max(stats["maximum"], value)
            if stats["integer_only"] and float(value).is_integer():
                bucket = int(value)
                stats["distribution"][bucket] = stats["distribution"].get(bucket, 0) + 1
        else:
            return False
        stats["answered_count"] += 1
        stats["valid_answer_count"] += 1
        return True

    @staticmethod
    def _finalize_library_question_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            key: value
            for key, value in stats.items()
            if not key.startswith("_")
            and key not in {"integer_only", "distribution"}
        }
        denominator = stats["valid_answer_count"]
        if "options" in stats:
            result["options"] = [
                {
                    **option,
                    "proportion": option["count"] / denominator if denominator else 0,
                    "percentage": option["count"] / denominator * 100 if denominator else 0,
                }
                for option in stats["options"]
            ]
        if stats.get("type") == "number":
            result.update(
                {
                    "average": stats["_sum"] / denominator if denominator else None,
                    "minimum": stats["minimum"],
                    "maximum": stats["maximum"],
                }
            )
            if stats["integer_only"]:
                result["distribution"] = [
                    {
                        "value": value,
                        "count": count,
                        "proportion": count / denominator if denominator else 0,
                        "percentage": count / denominator * 100 if denominator else 0,
                    }
                    for value, count in sorted(stats["distribution"].items())
                ]
        return result

    def _find_owned_survey(
        self, survey_id: str, owner_id: ObjectId
    ) -> Dict[str, Any]:
        survey = self._surveys.find_owned(_object_id(survey_id), owner_id)
        if survey is None:
            raise StatisticsNotFound
        return survey

    def _aggregate_questions(
        self,
        survey: Dict[str, Any],
        questions: List[Dict[str, Any]],
        *,
        filter_question_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        question_ids = {question["question_id"] for question in questions}
        stats_by_id = {
            question["question_id"]: self._initial_question_stats(question)
            for question in questions
        }
        for step in self._responses.answered_steps(
            survey["_id"], question_id=filter_question_id
        ):
            question_id = step.get("question_id")
            if question_id not in question_ids:
                continue
            stats = stats_by_id[question_id]
            value = step.get("value", {})
            stats["answered_count"] += 1
            if stats["type"] == "single_choice":
                self._increment_options(stats, [value.get("option_id")])
            elif stats["type"] == "multi_choice":
                self._increment_options(stats, value.get("option_ids", []))
            elif stats["type"] == "text":
                stats["answers"].append(
                    {
                        "text": value.get("text", ""),
                        "submitted_at": step.get("submitted_at"),
                    }
                )
            elif stats["type"] == "number":
                number = value.get("number")
                if isinstance(number, (int, float)) and not isinstance(number, bool):
                    stats["_sum"] += number

        results = []
        for question in questions:
            stats = stats_by_id[question["question_id"]]
            if stats["type"] == "number":
                stats["average"] = (
                    stats.pop("_sum") / stats["answered_count"]
                    if stats["answered_count"]
                    else None
                )
            results.append(stats)
        return results

    @staticmethod
    def _initial_question_stats(question: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "question_id": question["question_id"],
            "prompt": question["prompt"],
            "type": question["type"],
            "answered_count": 0,
        }
        if question["type"] in {"single_choice", "multi_choice"}:
            result["options"] = [
                {
                    "option_id": option["option_id"],
                    "label": option["label"],
                    "count": 0,
                }
                for option in question.get("options", [])
            ]
        elif question["type"] == "text":
            result["answers"] = []
        elif question["type"] == "number":
            result["_sum"] = 0
            result["average"] = None
        return result

    @staticmethod
    def _increment_options(stats: Dict[str, Any], selected_ids: List[Any]) -> None:
        counts = {option["option_id"]: option for option in stats["options"]}
        for option_id in selected_ids:
            option = counts.get(option_id)
            if option is not None:
                option["count"] += 1
