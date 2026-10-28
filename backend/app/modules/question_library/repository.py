from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError


class QuestionLibraryRepository:
    def __init__(self, database):
        self._families: Collection = database["question_families"]
        self._versions: Collection = database["question_versions"]
        self._banks: Collection = database["question_banks"]
        self._items: Collection = database["question_bank_items"]
        self._shares: Collection = database["question_shares"]
        self._migrations: Collection = database["schema_migrations"]

    def ensure_indexes(self) -> None:
        self._families.create_index(
            [("owner_id", ASCENDING), ("updated_at", DESCENDING)],
            name="ix_question_families_owner_updated_at",
        )
        self._families.create_index(
            [("origin_survey_id", ASCENDING), ("origin_question_id", ASCENDING)],
            unique=True,
            partialFilterExpression={
                "origin_survey_id": {"$exists": True},
                "origin_question_id": {"$exists": True},
            },
            name="uq_question_families_origin",
        )
        self._versions.create_index(
            [("family_id", ASCENDING), ("version_number", ASCENDING)],
            unique=True,
            name="uq_question_versions_family_number",
        )
        self._versions.create_index(
            [("family_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_question_versions_family_created_at",
        )
        self._versions.create_index(
            [("parent_version_id", ASCENDING)],
            name="ix_question_versions_parent",
        )
        self._banks.create_index(
            [("owner_id", ASCENDING), ("updated_at", DESCENDING)],
            name="ix_question_banks_owner_updated_at",
        )
        self._banks.create_index(
            [("owner_id", ASCENDING), ("is_default", ASCENDING)],
            unique=True,
            partialFilterExpression={"is_default": True},
            name="uq_question_banks_default_per_owner",
        )
        self._items.create_index(
            [("bank_id", ASCENDING), ("family_id", ASCENDING)],
            unique=True,
            name="uq_question_bank_items_bank_family",
        )
        self._items.create_index(
            [("family_id", ASCENDING), ("bank_id", ASCENDING)],
            name="ix_question_bank_items_family_bank",
        )
        self._ensure_share_indexes()

    def _ensure_share_indexes(self) -> None:
        """Replace the first-batch family-wide reservation safely.

        A family-wide grant cannot be interpreted as consent to every version.
        The first batch shipped only an empty reservation index, so it is safe to
        replace when empty. Any unexpected legacy records stop startup rather
        than being silently widened into version grants.
        """
        indexes = self._shares.index_information()
        legacy = indexes.get("uq_question_shares_family_recipient")
        if legacy is not None:
            legacy_key = [(key, direction) for key, direction in legacy.get("key", [])]
            expected_key = [("family_id", ASCENDING), ("recipient_user_id", ASCENDING)]
            if legacy_key != expected_key or not legacy.get("unique"):
                raise RuntimeError("Unrecognized question share reservation index")
            if self._shares.count_documents({}) > 0:
                raise RuntimeError(
                    "Legacy family-wide question shares exist; review them "
                    "before enabling version-scoped sharing"
                )
            self._shares.drop_index("uq_question_shares_family_recipient")
        if "ix_question_shares_recipient_family" in indexes:
            self._shares.drop_index("ix_question_shares_recipient_family")
        self._shares.create_index(
            [
                ("family_id", ASCENDING),
                ("version_id", ASCENDING),
                ("recipient_user_id", ASCENDING),
            ],
            unique=True,
            name="uq_question_shares_family_version_recipient",
        )
        self._shares.create_index(
            [("recipient_user_id", ASCENDING), ("status", ASCENDING), ("granted_at", DESCENDING)],
            name="ix_question_shares_recipient_status_granted",
        )
        self._shares.create_index(
            [("family_id", ASCENDING), ("version_id", ASCENDING), ("status", ASCENDING)],
            name="ix_question_shares_version_status",
        )
        self._migrations.create_index(
            [("migration_key", ASCENDING)],
            unique=True,
            name="uq_schema_migrations_key",
        )

    def insert_family(self, document: Dict[str, Any]) -> None:
        self._families.insert_one(document)

    def find_family(self, family_id: ObjectId) -> Optional[Dict[str, Any]]:
        return self._families.find_one({"_id": family_id})

    def find_owned_family(
        self, family_id: ObjectId, owner_id: ObjectId
    ) -> Optional[Dict[str, Any]]:
        return self._families.find_one({"_id": family_id, "owner_id": owner_id})

    def find_family_by_origin(
        self, survey_id: ObjectId, question_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._families.find_one(
            {"origin_survey_id": survey_id, "origin_question_id": question_id}
        )

    def allocate_version_number(
        self, family_id: ObjectId, now: datetime
    ) -> Optional[int]:
        family = self._families.find_one_and_update(
            {"_id": family_id},
            {"$inc": {"last_version_number": 1}, "$set": {"updated_at": now}},
            return_document=ReturnDocument.AFTER,
            projection={"last_version_number": 1},
        )
        return family.get("last_version_number") if family else None

    def set_latest_version(
        self,
        family_id: ObjectId,
        version_number: int,
        version_id: ObjectId,
    ) -> None:
        self._families.update_one(
            {"_id": family_id, "last_version_number": version_number},
            {"$set": {"latest_version_id": version_id}},
        )

    def insert_version(self, document: Dict[str, Any]) -> None:
        self._versions.insert_one(document)

    def find_version(self, version_id: ObjectId) -> Optional[Dict[str, Any]]:
        return self._versions.find_one({"_id": version_id})

    def find_family_version(
        self, family_id: ObjectId, version_id: ObjectId
    ) -> Optional[Dict[str, Any]]:
        return self._versions.find_one(
            {"_id": version_id, "family_id": family_id}
        )

    def list_versions(self, family_id: ObjectId) -> Iterable[Dict[str, Any]]:
        return self._versions.find({"family_id": family_id}).sort(
            "version_number", ASCENDING
        )

    def find_share(
        self,
        family_id: ObjectId,
        version_id: ObjectId,
        recipient_user_id: ObjectId,
    ) -> Optional[Dict[str, Any]]:
        return self._shares.find_one(
            {
                "family_id": family_id,
                "version_id": version_id,
                "recipient_user_id": recipient_user_id,
            }
        )

    def find_active_share(
        self,
        family_id: ObjectId,
        version_id: ObjectId,
        recipient_user_id: ObjectId,
    ) -> Optional[Dict[str, Any]]:
        return self._shares.find_one(
            {
                "family_id": family_id,
                "version_id": version_id,
                "recipient_user_id": recipient_user_id,
                "status": "active",
                "revoked_at": None,
            }
        )

    def grant_share(
        self,
        *,
        family_id: ObjectId,
        version_id: ObjectId,
        owner_id: ObjectId,
        recipient_user_id: ObjectId,
        now: datetime,
    ) -> Dict[str, Any]:
        key = {
            "family_id": family_id,
            "version_id": version_id,
            "recipient_user_id": recipient_user_id,
        }
        for _ in range(3):
            existing = self._shares.find_one(key)
            if existing is not None and existing.get("status") == "active":
                return existing
            if existing is not None:
                changed = self._shares.update_one(
                    {"_id": existing["_id"], "status": "revoked"},
                    {
                        "$set": {
                            "owner_id": owner_id,
                            "status": "active",
                            "permission": "use",
                            "granted_by": owner_id,
                            "granted_at": now,
                            "revoked_at": None,
                        }
                    },
                )
                if changed.modified_count:
                    return self._shares.find_one({"_id": existing["_id"]})
                continue
            document = {
                "_id": ObjectId(),
                **key,
                "owner_id": owner_id,
                "status": "active",
                "permission": "use",
                "granted_by": owner_id,
                "granted_at": now,
                "revoked_at": None,
                "created_at": now,
            }
            try:
                self._shares.insert_one(document)
                return document
            except DuplicateKeyError:
                # Another request may have created/re-enabled the same grant.
                continue
        existing = self._shares.find_one(key)
        if existing is not None and existing.get("status") == "active":
            return existing
        raise RuntimeError("Unable to safely grant question version access")

    def list_version_shares(
        self, family_id: ObjectId, version_id: ObjectId
    ) -> Iterable[Dict[str, Any]]:
        return self._shares.find(
            {"family_id": family_id, "version_id": version_id}
        ).sort("created_at", ASCENDING)

    def list_received_shares(self, recipient_user_id: ObjectId) -> Iterable[Dict[str, Any]]:
        return self._shares.find(
            {"recipient_user_id": recipient_user_id, "status": "active", "revoked_at": None}
        ).sort("granted_at", DESCENDING)

    def revoke_share(
        self,
        *,
        family_id: ObjectId,
        version_id: ObjectId,
        recipient_user_id: ObjectId,
        owner_id: ObjectId,
        now: datetime,
    ) -> Optional[Dict[str, Any]]:
        key = {
            "family_id": family_id,
            "version_id": version_id,
            "recipient_user_id": recipient_user_id,
            "owner_id": owner_id,
        }
        existing = self._shares.find_one(key)
        if existing is None:
            return None
        if existing.get("status") != "revoked":
            self._shares.update_one(
                {"_id": existing["_id"], "status": "active"},
                {"$set": {"status": "revoked", "revoked_at": now}},
            )
        return self._shares.find_one({"_id": existing["_id"]})

    def ensure_default_bank(
        self, owner_id: ObjectId, now: datetime
    ) -> Dict[str, Any]:
        try:
            return self._banks.find_one_and_update(
                {"owner_id": owner_id, "is_default": True},
                {
                    "$setOnInsert": {
                        "_id": ObjectId(),
                        "owner_id": owner_id,
                        "name": "个人题库",
                        "is_default": True,
                        "visibility": "private",
                        "created_at": now,
                        "updated_at": now,
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # A concurrent first request may win the partial unique index.
            existing = self._banks.find_one({"owner_id": owner_id, "is_default": True})
            if existing is not None:
                return existing
            raise

    def insert_bank(self, document: Dict[str, Any]) -> None:
        self._banks.insert_one(document)

    def find_owned_bank(
        self, bank_id: ObjectId, owner_id: ObjectId
    ) -> Optional[Dict[str, Any]]:
        return self._banks.find_one({"_id": bank_id, "owner_id": owner_id})

    def list_banks(self, owner_id: ObjectId) -> Iterable[Dict[str, Any]]:
        return self._banks.find({"owner_id": owner_id}).sort(
            [("is_default", DESCENDING), ("updated_at", DESCENDING)]
        )

    def insert_bank_item(self, document: Dict[str, Any]) -> None:
        self._items.insert_one(document)

    def find_bank_item(
        self, bank_id: ObjectId, family_id: ObjectId
    ) -> Optional[Dict[str, Any]]:
        return self._items.find_one({"bank_id": bank_id, "family_id": family_id})

    def list_bank_items(self, bank_id: ObjectId) -> Iterable[Dict[str, Any]]:
        return self._items.find({"bank_id": bank_id}).sort("created_at", DESCENDING)

    def set_bank_item_version(
        self,
        bank_id: ObjectId,
        family_id: ObjectId,
        version_id: ObjectId,
        now: datetime,
    ) -> Optional[Dict[str, Any]]:
        return self._items.find_one_and_update(
            {"bank_id": bank_id, "family_id": family_id},
            {"$set": {"version_id": version_id, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )

    def delete_bank_item(self, bank_id: ObjectId, family_id: ObjectId) -> bool:
        return self._items.delete_one(
            {"bank_id": bank_id, "family_id": family_id}
        ).deleted_count == 1

    def record_migration(self, migration_key: str, result: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        self._migrations.update_one(
            {"migration_key": migration_key},
            {
                "$set": {
                    "last_run": result,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "migration_key": migration_key,
                    "created_at": now,
                },
            },
            upsert=True,
        )
