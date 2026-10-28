from typing import Any, Dict

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.collection import Collection


class UserRepository:
    def __init__(self, collection: Collection):
        self._collection = collection

    def ensure_indexes(self) -> None:
        self._collection.create_index(
            [("username_normalized", ASCENDING)],
            unique=True,
            name="uq_users_username_normalized",
        )

    def insert_user(self, user_document: Dict[str, Any]) -> None:
        self._collection.insert_one(user_document)

    def find_by_normalized_username(self, username_normalized: str):
        return self._collection.find_one({"username_normalized": username_normalized})

    def find_by_id(self, user_id: ObjectId):
        return self._collection.find_one({"_id": user_id})
