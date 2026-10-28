from typing import Any, Dict, Iterable, Optional

from bson import ObjectId
from pymongo import DESCENDING
from pymongo.collection import Collection
from pymongo import ReturnDocument


class SurveyRepository:
    def __init__(self, collection: Collection):
        self._collection = collection

    def ensure_indexes(self) -> None:
        self._collection.create_index(
            [("owner_id", 1), ("updated_at", DESCENDING)],
            name="ix_surveys_owner_updated_at",
        )
        self._collection.create_index(
            [
                ("questions.library_ref.source_question_id", 1),
                ("questions.library_ref.source_version_id", 1),
            ],
            name="ix_surveys_question_library_ref",
        )
        self._collection.create_index(
            [
                ("owner_id", 1),
                ("questions.library_ref.source_question_id", 1),
                ("questions.library_ref.source_version_id", 1),
            ],
            name="ix_surveys_owner_library_version",
        )

    def insert(self, survey: Dict[str, Any]) -> None:
        self._collection.insert_one(survey)

    def list_owned(self, owner_id: ObjectId) -> Iterable[Dict[str, Any]]:
        return self._collection.find({"owner_id": owner_id}).sort(
            "updated_at", DESCENDING
        )

    def find_owned(
        self, survey_id: ObjectId, owner_id: ObjectId
    ) -> Optional[Dict[str, Any]]:
        return self._collection.find_one({"_id": survey_id, "owner_id": owner_id})

    def find_fillable_or_closed(self, survey_id: ObjectId) -> Optional[Dict[str, Any]]:
        return self._collection.find_one(
            {"_id": survey_id, "status": {"$in": ["published", "closed"]}}
        )

    def list_library_usages(
        self, family_id: ObjectId, version_id: Optional[ObjectId] = None
    ) -> Iterable[Dict[str, Any]]:
        library_ref: Dict[str, Any] = {"library_ref.source_question_id": family_id}
        if version_id is not None:
            library_ref["library_ref.source_version_id"] = version_id
        return self._collection.find(
            {"questions": {"$elemMatch": library_ref}},
            {
                "owner_id": 1,
                "title": 1,
                "status": 1,
                "questions.question_id": 1,
                "questions.prompt": 1,
                "questions.type": 1,
                "questions.required": 1,
                "questions.options": 1,
                "questions.validation": 1,
                "questions.library_ref": 1,
            },
        )

    def update_owned(
        self,
        survey_id: ObjectId,
        owner_id: ObjectId,
        fields: Dict[str, Any],
        *,
        required_status: Optional[str] = None,
        expected_fields: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        query: Dict[str, Any] = {"_id": survey_id, "owner_id": owner_id}
        if required_status is not None:
            query["status"] = required_status
        if expected_fields:
            query.update(expected_fields)
        return self._collection.find_one_and_update(
            query,
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )

    def change_status(
        self,
        survey_id: ObjectId,
        owner_id: ObjectId,
        current_status: str,
        fields: Dict[str, Any],
        *,
        expected_fields: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        query: Dict[str, Any] = {
            "_id": survey_id,
            "owner_id": owner_id,
            "status": current_status,
        }
        if expected_fields:
            query.update(expected_fields)
        return self._collection.find_one_and_update(
            query,
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
