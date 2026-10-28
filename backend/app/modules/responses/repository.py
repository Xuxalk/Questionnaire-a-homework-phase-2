from datetime import datetime
from typing import Any, Dict, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection


class ResponseRepository:
    def __init__(self, collection: Collection):
        self._collection = collection

    def ensure_indexes(self) -> None:
        self._collection.create_index(
            [
                ("survey_id", ASCENDING),
                ("respondent_id", ASCENDING),
                ("lifecycle_guard", ASCENDING),
            ],
            unique=True,
            partialFilterExpression={"lifecycle_guard": {"$exists": True}},
            name="uq_responses_lifecycle_guard",
        )
        self._collection.create_index(
            [
                ("survey_id", ASCENDING),
                ("status", ASCENDING),
                ("submitted_at", DESCENDING),
            ],
            name="ix_responses_survey_status_submitted_at",
        )

    def insert(self, response: Dict[str, Any]) -> None:
        self._collection.insert_one(response)

    def find_draft(
        self, survey_id: ObjectId, respondent_id: ObjectId
    ) -> Optional[Dict[str, Any]]:
        return self._collection.find_one(
            {
                "survey_id": survey_id,
                "respondent_id": respondent_id,
                "status": "draft",
            }
        )

    def has_single_submission(
        self, survey_id: ObjectId, respondent_id: ObjectId
    ) -> bool:
        return self._collection.find_one(
            {
                "survey_id": survey_id,
                "respondent_id": respondent_id,
                "status": "submitted",
                "lifecycle_guard": "single",
            },
            projection={"_id": 1},
        ) is not None

    def find_for_respondent(
        self,
        response_id: ObjectId,
        survey_id: ObjectId,
        respondent_id: ObjectId,
    ) -> Optional[Dict[str, Any]]:
        return self._collection.find_one(
            {
                "_id": response_id,
                "survey_id": survey_id,
                "respondent_id": respondent_id,
            }
        )

    def save_steps(
        self,
        response_id: ObjectId,
        survey_id: ObjectId,
        respondent_id: ObjectId,
        expected_revision: int,
        steps: list,
        now: datetime,
    ) -> Optional[Dict[str, Any]]:
        return self._collection.find_one_and_update(
            {
                "_id": response_id,
                "survey_id": survey_id,
                "respondent_id": respondent_id,
                "status": "draft",
                "revision": expected_revision,
            },
            {"$set": {"steps": steps, "updated_at": now}, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
        )

    def submit(
        self,
        response_id: ObjectId,
        survey_id: ObjectId,
        respondent_id: ObjectId,
        expected_revision: int,
        now: datetime,
        *,
        allow_multiple_submissions: bool,
    ) -> Optional[Dict[str, Any]]:
        query = {
            "_id": response_id,
            "survey_id": survey_id,
            "respondent_id": respondent_id,
            "status": "draft",
            "revision": expected_revision,
            "lifecycle_guard": "draft" if allow_multiple_submissions else "single",
        }
        update: Dict[str, Any] = {
            "$set": {
                "status": "submitted",
                "submitted_at": now,
                "updated_at": now,
            },
            "$inc": {"revision": 1},
        }
        if allow_multiple_submissions:
            update["$unset"] = {"lifecycle_guard": ""}
        return self._collection.find_one_and_update(
            query,
            update,
            return_document=ReturnDocument.AFTER,
        )
