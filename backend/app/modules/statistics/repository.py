from typing import Any, Dict, Iterable, Optional

from bson import ObjectId
from pymongo.collection import Collection


class StatisticsRepository:
    """Read-only aggregation queries over submitted responses."""

    def __init__(self, collection: Collection, survey_collection: Optional[Collection] = None):
        self._collection = collection
        self._surveys = survey_collection

    def count_submitted(self, survey_id: ObjectId) -> int:
        return self._collection.count_documents(
            {"survey_id": survey_id, "status": "submitted"}
        )

    def answered_steps(
        self,
        survey_id: ObjectId,
        *,
        question_id: Optional[str] = None,
    ) -> Iterable[Dict[str, Any]]:
        match: Dict[str, Any] = {
            "survey_id": survey_id,
            "status": "submitted",
        }
        pipeline = [
            {"$match": match},
            {"$unwind": "$steps"},
            {"$match": {"steps.status": "answered"}},
        ]
        if question_id is not None:
            pipeline.append({"$match": {"steps.question_id": question_id}})
        pipeline.append(
            {
                "$project": {
                    "_id": 0,
                    "question_id": "$steps.question_id",
                    "value": "$steps.value",
                    "submitted_at": 1,
                }
            }
        )
        return self._collection.aggregate(pipeline, allowDiskUse=True)

    def library_version_pipeline(
        self,
        *,
        owner_id: ObjectId,
        family_id: ObjectId,
        version_id: ObjectId,
        survey_id: Optional[ObjectId] = None,
    ) -> list:
        """Build one indexed survey-to-response aggregation for an exact version."""
        match: Dict[str, Any] = {
            "owner_id": owner_id,
            "status": {"$in": ["published", "closed"]},
            "questions": {
                "$elemMatch": {
                    "library_ref.source_question_id": family_id,
                    "library_ref.source_version_id": version_id,
                }
            },
        }
        if survey_id is not None:
            match["_id"] = survey_id
        return [
            {"$match": match},
            {"$unwind": "$questions"},
            {
                "$match": {
                    "questions.library_ref.source_question_id": family_id,
                    "questions.library_ref.source_version_id": version_id,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "survey_id": "$_id",
                    "title": 1,
                    "survey_status": "$status",
                    "question": {
                        "question_id": "$questions.question_id",
                        "prompt": "$questions.prompt",
                        "type": "$questions.type",
                        "required": "$questions.required",
                        "options": "$questions.options",
                        "validation": "$questions.validation",
                    },
                }
            },
            {
                "$lookup": {
                    "from": self._collection.name,
                    "let": {
                        "requested_survey_id": "$survey_id",
                        "requested_question_id": "$question.question_id",
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "status": "submitted",
                                "$expr": {
                                    "$eq": ["$survey_id", "$$requested_survey_id"]
                                },
                            }
                        },
                        {"$unwind": "$steps"},
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$steps.question_id",
                                                "$$requested_question_id",
                                            ]
                                        },
                                        {"$eq": ["$steps.status", "answered"]},
                                    ]
                                }
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "value": "$steps.value",
                                "submitted_at": 1,
                            }
                        },
                    ],
                    "as": "answer_rows",
                }
            },
            {
                "$unwind": {
                    "path": "$answer_rows",
                    "preserveNullAndEmptyArrays": True,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "survey_id": 1,
                    "title": 1,
                    "survey_status": 1,
                    "question": 1,
                    "answer": "$answer_rows.value",
                    "submitted_at": "$answer_rows.submitted_at",
                }
            },
        ]

    def library_version_answer_rows(
        self,
        *,
        owner_id: ObjectId,
        family_id: ObjectId,
        version_id: ObjectId,
        survey_id: Optional[ObjectId] = None,
    ) -> Iterable[Dict[str, Any]]:
        if self._surveys is None:
            raise RuntimeError("Survey collection is required for library statistics")
        return self._surveys.aggregate(
            self.library_version_pipeline(
                owner_id=owner_id,
                family_id=family_id,
                version_id=version_id,
                survey_id=survey_id,
            ),
            allowDiskUse=True,
            maxTimeMS=60_000,
        )
