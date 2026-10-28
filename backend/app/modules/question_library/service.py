import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from backend.app.modules.accounts.repository import UserRepository
from backend.app.modules.question_library.repository import QuestionLibraryRepository
from backend.app.modules.questionnaires.repository import SurveyRepository
from backend.app.modules.questionnaires.schemas import QuestionInput
from backend.app.modules.questionnaires.service import (
    QuestionnaireService,
    SurveyConflict,
    SurveyNotFound,
)


class LibraryNotFound(Exception):
    pass


class LibraryConflict(Exception):
    pass


class LibraryContentInvalid(Exception):
    pass


def parse_object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise LibraryNotFound
    return ObjectId(value)


def question_content_hash(content: Dict[str, Any]) -> str:
    encoded = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class QuestionLibraryService:
    def __init__(
        self,
        library: QuestionLibraryRepository,
        surveys: SurveyRepository,
        users: UserRepository,
    ):
        self._library = library
        self._surveys = surveys
        self._users = users

    def list_banks(self, owner_id: ObjectId) -> List[Dict[str, Any]]:
        self._library.ensure_default_bank(owner_id, self._now())
        return [self._public_bank(bank) for bank in self._library.list_banks(owner_id)]

    def create_bank(self, owner_id: ObjectId, name: str) -> Dict[str, Any]:
        name = name.strip()
        if not name:
            raise LibraryContentInvalid("Bank name cannot be blank")
        now = self._now()
        bank = {
            "_id": ObjectId(),
            "owner_id": owner_id,
            "name": name,
            "is_default": False,
            "visibility": "private",
            "created_at": now,
            "updated_at": now,
        }
        self._library.insert_bank(bank)
        return self._public_bank(bank)

    def list_bank_items(
        self, owner_id: ObjectId, bank_id: str
    ) -> List[Dict[str, Any]]:
        bank = self._find_bank(owner_id, bank_id)
        result = []
        for item in self._library.list_bank_items(bank["_id"]):
            family = self._library.find_owned_family(item["family_id"], owner_id)
            version = self._library.find_family_version(
                item["family_id"], item["version_id"]
            )
            if family is None or version is None:
                # Dangling data is never exposed as a usable library item.
                continue
            latest = self._library.find_version(family["latest_version_id"])
            result.append(
                {
                    "bank_id": str(bank["_id"]),
                    "family": self._public_family(family),
                    "version": self._public_version(version),
                    "latest_version": self._public_version(latest) if latest else None,
                    "created_at": item["created_at"],
                    "updated_at": item.get("updated_at", item["created_at"]),
                }
            )
        return result

    def create_question(
        self,
        owner_id: ObjectId,
        payload: QuestionInput,
        *,
        bank_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        content = self._content_from_input(payload)
        bank = self._resolve_bank(owner_id, bank_id)
        now = self._now()
        family = self._new_family(owner_id, now)
        try:
            self._library.insert_family(family)
        except DuplicateKeyError as exc:
            raise LibraryConflict("Question family already exists") from exc
        version = self._append_version(
            family, content, owner_id, parent_version_id=None, now=now
        )
        self._add_to_bank(bank, family, version, owner_id, now)
        return self._library_item(bank, family, version)

    def import_survey_question(
        self,
        owner_id: ObjectId,
        survey_id: str,
        question_id: str,
        *,
        bank_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        survey_oid = self._parse_survey_id(survey_id)
        survey = self._surveys.find_owned(survey_oid, owner_id)
        if survey is None:
            raise LibraryNotFound
        question_index = next(
            (
                index
                for index, question in enumerate(survey.get("questions", []))
                if question.get("question_id") == question_id
            ),
            None,
        )
        if question_index is None:
            raise LibraryNotFound
        question = survey["questions"][question_index]
        bank = self._resolve_bank(owner_id, bank_id)
        now = self._now()

        existing_ref = question.get("library_ref") or {}
        family = None
        version = None
        source_question_id = existing_ref.get("source_question_id")
        source_version_id = existing_ref.get("source_version_id")
        if ObjectId.is_valid(str(source_question_id)) and ObjectId.is_valid(
            str(source_version_id)
        ):
            family = self._library.find_owned_family(
                ObjectId(str(source_question_id)), owner_id
            )
            if family is not None:
                version = self._library.find_family_version(
                    family["_id"], ObjectId(str(source_version_id))
                )

        if family is None or version is None:
            family = self._library.find_family_by_origin(survey_oid, question_id)
            if family is None:
                content = self._content_from_document(question)
                family = self._new_family(
                    owner_id,
                    now,
                    origin_survey_id=survey_oid,
                    origin_question_id=question_id,
                )
                try:
                    self._library.insert_family(family)
                except DuplicateKeyError:
                    family = self._library.find_family_by_origin(
                        survey_oid, question_id
                    )
                    if family is None or family["owner_id"] != owner_id:
                        raise LibraryConflict("Question import raced with another request")
                latest_id = family.get("latest_version_id")
                version = (
                    self._library.find_version(latest_id) if latest_id else None
                )
                if version is None:
                    version = self._append_version(
                        family, content, owner_id, parent_version_id=None, now=now
                    )
            else:
                latest_id = family.get("latest_version_id")
                version = self._library.find_version(latest_id) if latest_id else None
                if version is None:
                    raise LibraryConflict("Imported question has no valid version")

        self._add_to_bank(bank, family, version, owner_id, now)
        expected_ref = {
            "source_question_id": family["_id"],
            "source_version_id": version["_id"],
        }
        if question.get("library_ref") != expected_ref:
            updated_questions = list(survey["questions"])
            updated_question = dict(updated_questions[question_index])
            updated_question["library_ref"] = expected_ref
            updated_questions[question_index] = updated_question
            saved = self._surveys.update_owned(
                survey_oid,
                owner_id,
                {"questions": updated_questions, "updated_at": now},
                expected_fields={"questions": survey["questions"]},
            )
            if saved is None:
                raise LibraryConflict(
                    "Survey changed while its question was being added to the library; retry"
                )
        return self._library_item(bank, family, version)

    def list_versions(
        self, owner_id: ObjectId, family_id: str
    ) -> List[Dict[str, Any]]:
        family = self._find_family(owner_id, family_id)
        return [
            self._public_version(version)
            for version in self._library.list_versions(family["_id"])
        ]

    def grant_version_share(
        self,
        owner_id: ObjectId,
        family_id: str,
        version_id: str,
        recipient_username: str,
    ) -> Dict[str, Any]:
        family = self._find_family(owner_id, family_id)
        version = self._find_version(family["_id"], version_id)
        normalized = recipient_username.strip().casefold()
        recipient = self._users.find_by_normalized_username(normalized)
        if recipient is None:
            raise LibraryNotFound
        if recipient["_id"] == owner_id:
            raise LibraryContentInvalid("A question cannot be shared with its owner")
        share = self._library.grant_share(
            family_id=family["_id"],
            version_id=version["_id"],
            owner_id=owner_id,
            recipient_user_id=recipient["_id"],
            now=self._now(),
        )
        return self._public_share(share, recipient.get("username"))

    def list_version_shares(
        self, owner_id: ObjectId, family_id: str, version_id: str
    ) -> List[Dict[str, Any]]:
        family = self._find_family(owner_id, family_id)
        version = self._find_version(family["_id"], version_id)
        result = []
        for share in self._library.list_version_shares(family["_id"], version["_id"]):
            recipient = self._users.find_by_id(share["recipient_user_id"])
            result.append(
                self._public_share(
                    share, recipient.get("username") if recipient else None
                )
            )
        return result

    def revoke_version_share(
        self,
        owner_id: ObjectId,
        family_id: str,
        version_id: str,
        recipient_user_id: str,
    ) -> Dict[str, Any]:
        family = self._find_family(owner_id, family_id)
        version = self._find_version(family["_id"], version_id)
        recipient_id = parse_object_id(recipient_user_id)
        share = self._library.revoke_share(
            family_id=family["_id"],
            version_id=version["_id"],
            recipient_user_id=recipient_id,
            owner_id=owner_id,
            now=self._now(),
        )
        if share is None:
            raise LibraryNotFound
        recipient = self._users.find_by_id(recipient_id)
        return self._public_share(share, recipient.get("username") if recipient else None)

    def list_received_shares(self, recipient_user_id: ObjectId) -> List[Dict[str, Any]]:
        result = []
        for share in self._library.list_received_shares(recipient_user_id):
            family = self._library.find_family(share["family_id"])
            version = self._library.find_family_version(
                share["family_id"], share["version_id"]
            )
            if (
                family is None
                or version is None
                or family.get("owner_id") != share.get("owner_id")
                or family.get("owner_id") == recipient_user_id
            ):
                continue
            owner = self._users.find_by_id(family["owner_id"])
            result.append(
                {
                    "family_id": str(family["_id"]),
                    "owner_id": str(family["owner_id"]),
                    "owner_username": owner.get("username") if owner else None,
                    "version": self._public_shared_version(version),
                    "granted_at": share["granted_at"],
                }
            )
        return result

    def resolve_usable_version(
        self, user_id: ObjectId, family_id: str, version_id: str
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        family_oid = parse_object_id(family_id)
        version_oid = parse_object_id(version_id)
        family = self._library.find_family(family_oid)
        version = self._library.find_family_version(family_oid, version_oid)
        if family is None or version is None:
            raise LibraryNotFound
        if family.get("owner_id") != user_id and self._library.find_active_share(
            family_oid, version_oid, user_id
        ) is None:
            # Do not distinguish an unknown version from an unauthorized one.
            raise LibraryNotFound
        return family, version

    def version_reference_for_snapshot_edit(
        self,
        user_id: ObjectId,
        family_id: str,
        version_id: str,
        payload: QuestionInput,
    ) -> Optional[Dict[str, ObjectId]]:
        """Append a version only when the editor owns the source family.

        A recipient edits only their survey-local snapshot. Its source reference
        remains as provenance; usage reports mark content that no longer matches
        the immutable source as adapted.
        """
        try:
            family_oid = parse_object_id(family_id)
            version_oid = parse_object_id(version_id)
        except LibraryNotFound:
            return None
        family = self._library.find_family(family_oid)
        source = self._library.find_family_version(family_oid, version_oid)
        if family is None or source is None:
            return None
        if family.get("owner_id") != user_id:
            return {
                "source_question_id": family_oid,
                "source_version_id": version_oid,
            }
        created = self.create_version(
            user_id,
            str(family_oid),
            payload,
            parent_version_id=str(version_oid),
            allow_local_jump_rules=True,
        )
        return {
            "source_question_id": family_oid,
            "source_version_id": ObjectId(created["id"]),
        }

    def list_usages(
        self,
        user_id: ObjectId,
        family_id: str,
        *,
        version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        family_oid = parse_object_id(family_id)
        family = self._library.find_family(family_oid)
        if family is None:
            raise LibraryNotFound
        is_owner = family.get("owner_id") == user_id
        if version_id is None:
            if not is_owner:
                raise LibraryContentInvalid(
                    "A shared recipient must select the exact authorized version"
                )
            selected_version_id = None
        else:
            selected_version_id = parse_object_id(version_id)
            version = self._library.find_family_version(family_oid, selected_version_id)
            if version is None:
                raise LibraryNotFound
            if not is_owner and self._library.find_active_share(
                family_oid, selected_version_id, user_id
            ) is None:
                raise LibraryNotFound

        own_usages: List[Dict[str, Any]] = []
        other_usage_sets: Dict[str, Dict[str, set]] = {}
        versions_by_id: Dict[ObjectId, Dict[str, Any]] = {}
        for survey in self._surveys.list_library_usages(family_oid, selected_version_id):
            survey_owner = survey.get("owner_id")
            for question in survey.get("questions", []):
                reference = question.get("library_ref") or {}
                if reference.get("source_question_id") != family_oid:
                    continue
                used_version_id = reference.get("source_version_id")
                if not isinstance(used_version_id, ObjectId):
                    if not ObjectId.is_valid(str(used_version_id)):
                        continue
                    used_version_id = ObjectId(str(used_version_id))
                if selected_version_id is not None and used_version_id != selected_version_id:
                    continue
                version = versions_by_id.get(used_version_id)
                if version is None:
                    version = self._library.find_family_version(family_oid, used_version_id)
                    if version is None:
                        continue
                    versions_by_id[used_version_id] = version
                snapshot_content = {
                    key: question[key]
                    for key in ("type", "prompt", "required", "options", "validation")
                    if key in question
                }
                exact_match = question_content_hash(snapshot_content) == version["content_hash"]
                if survey_owner == user_id:
                    own_usages.append(
                        {
                            "survey_id": str(survey["_id"]),
                            "title": survey.get("title", ""),
                            "owner_id": str(survey_owner),
                            "survey_status": survey.get("status"),
                            "question_id": question.get("question_id"),
                            "question_prompt": question.get("prompt", ""),
                            "version_id": str(used_version_id),
                            "exact_version_match": exact_match,
                        }
                    )
                elif is_owner:
                    key = str(used_version_id)
                    usage_set = other_usage_sets.setdefault(
                        key, {"exact": set(), "adapted": set()}
                    )
                    usage_set["exact" if exact_match else "adapted"].add(survey["_id"])

        own_usages.sort(key=lambda item: (item["title"].casefold(), item["survey_id"], item["question_id"] or ""))
        result: Dict[str, Any] = {
            "family_id": str(family_oid),
            "owner_id": str(family["owner_id"]),
            "version_id": str(selected_version_id) if selected_version_id else None,
            "access_scope": "owner" if is_owner else "shared_version",
            "own_surveys": own_usages,
        }
        if is_owner:
            result["other_survey_usage_by_version"] = [
                {
                    "version_id": key,
                    "exact_survey_count": len(counts["exact"]),
                    "adapted_survey_count": len(counts["adapted"]),
                }
                for key, counts in sorted(other_usage_sets.items())
            ]
        return result

    def create_version(
        self,
        owner_id: ObjectId,
        family_id: str,
        payload: QuestionInput,
        *,
        parent_version_id: Optional[str] = None,
        allow_local_jump_rules: bool = False,
    ) -> Dict[str, Any]:
        family = self._find_family(owner_id, family_id)
        parent_id = self._resolve_parent_id(family, parent_version_id)
        parent = (
            self._library.find_family_version(family["_id"], parent_id)
            if parent_id
            else None
        )
        if parent_id and parent is None:
            raise LibraryNotFound
        content = self._content_from_input(
            payload, allow_local_jump_rules=allow_local_jump_rules
        )
        digest = question_content_hash(content)
        if parent is not None and parent["content_hash"] == digest:
            return self._public_version(parent)
        version = self._append_version(
            family,
            content,
            owner_id,
            parent_version_id=parent_id,
            now=self._now(),
        )
        return self._public_version(version)

    def restore_version(
        self,
        owner_id: ObjectId,
        family_id: str,
        source_version_id: str,
    ) -> Dict[str, Any]:
        family = self._find_family(owner_id, family_id)
        source_id = parse_object_id(source_version_id)
        source = self._library.find_family_version(family["_id"], source_id)
        if source is None:
            raise LibraryNotFound
        latest_id = family.get("latest_version_id")
        parent_id = ObjectId(latest_id) if latest_id else None
        version = self._append_version(
            family,
            self._content_from_version(source),
            owner_id,
            parent_version_id=parent_id,
            restored_from_version_id=source["_id"],
            now=self._now(),
        )
        return self._public_version(version)

    def add_bank_item(
        self,
        owner_id: ObjectId,
        bank_id: str,
        family_id: str,
        version_id: str,
    ) -> Dict[str, Any]:
        bank = self._find_bank(owner_id, bank_id)
        family = self._find_family(owner_id, family_id)
        version = self._find_version(family["_id"], version_id)
        existing = self._library.find_bank_item(bank["_id"], family["_id"])
        if existing is not None:
            raise LibraryConflict("Question is already in this bank")
        self._add_to_bank(bank, family, version, owner_id, self._now())
        return self._library_item(bank, family, version)

    def update_bank_item_version(
        self,
        owner_id: ObjectId,
        bank_id: str,
        family_id: str,
        version_id: str,
    ) -> Dict[str, Any]:
        bank = self._find_bank(owner_id, bank_id)
        family = self._find_family(owner_id, family_id)
        version = self._find_version(family["_id"], version_id)
        item = self._library.set_bank_item_version(
            bank["_id"], family["_id"], version["_id"], self._now()
        )
        if item is None:
            raise LibraryNotFound
        return self._library_item(bank, family, version)

    def remove_bank_item(
        self, owner_id: ObjectId, bank_id: str, family_id: str
    ) -> None:
        bank = self._find_bank(owner_id, bank_id)
        family = self._find_family(owner_id, family_id)
        if not self._library.delete_bank_item(bank["_id"], family["_id"]):
            raise LibraryNotFound

    def _append_version(
        self,
        family: Dict[str, Any],
        content: Dict[str, Any],
        owner_id: ObjectId,
        *,
        parent_version_id: Optional[ObjectId],
        now: datetime,
        restored_from_version_id: Optional[ObjectId] = None,
    ) -> Dict[str, Any]:
        version_number = self._library.allocate_version_number(family["_id"], now)
        if version_number is None:
            raise LibraryNotFound
        version = {
            "_id": ObjectId(),
            "family_id": family["_id"],
            "version_number": version_number,
            "parent_version_id": parent_version_id,
            "created_by": owner_id,
            "created_at": now,
            "content_hash": question_content_hash(content),
            **content,
        }
        if restored_from_version_id is not None:
            version["restored_from_version_id"] = restored_from_version_id
        try:
            self._library.insert_version(version)
        except Exception:
            # Version numbers are monotonic; a failed insert may leave a gap.
            raise
        try:
            self._library.set_latest_version(
                family["_id"], version_number, version["_id"]
            )
        except Exception:
            # A network interruption can happen after the immutable version is
            # inserted but before the family pointer is acknowledged. Verify
            # the committed state, then retry this compare-and-set once so the
            # API does not strand an otherwise successful version write.
            try:
                current_family = self._library.find_family(family["_id"])
                if current_family is not None and current_family.get(
                    "latest_version_id"
                ) == version["_id"]:
                    return version
                if (
                    current_family is not None
                    and current_family.get("last_version_number") == version_number
                ):
                    self._library.set_latest_version(
                        family["_id"], version_number, version["_id"]
                    )
                    current_family = self._library.find_family(family["_id"])
                    if current_family is not None and current_family.get(
                        "latest_version_id"
                    ) == version["_id"]:
                        return version
            except Exception:
                # Preserve the original storage error if a retry also fails.
                pass
            raise
        return version

    def _content_from_input(
        self,
        payload: QuestionInput,
        *,
        allow_local_jump_rules: bool = False,
    ) -> Dict[str, Any]:
        if payload.jump_rules and not allow_local_jump_rules:
            raise LibraryContentInvalid(
                "Jump rules belong to a survey and cannot be stored in a reusable question"
            )
        question = QuestionnaireService._normalise_question(
            payload, question_id=str(uuid4())
        )
        question["jump_rules"] = []
        QuestionnaireService._validate_question_document(question, 0, [question])
        return self._content_from_version(question)

    def _content_from_document(self, question: Dict[str, Any]) -> Dict[str, Any]:
        candidate = {
            key: value
            for key, value in question.items()
            if key not in {"question_id", "jump_rules", "library_ref"}
        }
        candidate["jump_rules"] = []
        from pydantic import TypeAdapter

        try:
            parsed = TypeAdapter(QuestionInput).validate_python(candidate)
        except Exception as exc:
            raise LibraryContentInvalid(
                "Existing question cannot be safely copied to the library"
            ) from exc
        return self._content_from_input(parsed)

    @staticmethod
    def _content_from_version(version: Dict[str, Any]) -> Dict[str, Any]:
        keys = {"type", "prompt", "required", "options", "validation"}
        return {key: version[key] for key in keys if key in version}

    def _new_family(
        self,
        owner_id: ObjectId,
        now: datetime,
        *,
        origin_survey_id: Optional[ObjectId] = None,
        origin_question_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        family = {
            "_id": ObjectId(),
            "owner_id": owner_id,
            "visibility": "private",
            "last_version_number": 0,
            "latest_version_id": None,
            "created_at": now,
            "updated_at": now,
        }
        if origin_survey_id is not None and origin_question_id is not None:
            family["origin_survey_id"] = origin_survey_id
            family["origin_question_id"] = origin_question_id
        return family

    def _resolve_bank(self, owner_id: ObjectId, bank_id: Optional[str]) -> Dict[str, Any]:
        if bank_id is None:
            return self._library.ensure_default_bank(owner_id, self._now())
        return self._find_bank(owner_id, bank_id)

    def _add_to_bank(
        self,
        bank: Dict[str, Any],
        family: Dict[str, Any],
        version: Dict[str, Any],
        owner_id: ObjectId,
        now: datetime,
    ) -> None:
        existing = self._library.find_bank_item(bank["_id"], family["_id"])
        if existing is not None:
            return
        try:
            self._library.insert_bank_item(
                {
                    "_id": ObjectId(),
                    "bank_id": bank["_id"],
                    "family_id": family["_id"],
                    "version_id": version["_id"],
                    "added_by": owner_id,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except DuplicateKeyError:
            # Idempotent when two requests add the same family concurrently.
            pass

    def _find_bank(self, owner_id: ObjectId, bank_id: str) -> Dict[str, Any]:
        bank = self._library.find_owned_bank(parse_object_id(bank_id), owner_id)
        if bank is None:
            raise LibraryNotFound
        return bank

    def _find_family(self, owner_id: ObjectId, family_id: str) -> Dict[str, Any]:
        family = self._library.find_owned_family(parse_object_id(family_id), owner_id)
        if family is None:
            raise LibraryNotFound
        return family

    def _find_version(
        self, family_id: ObjectId, version_id: str
    ) -> Dict[str, Any]:
        version = self._library.find_family_version(
            family_id, parse_object_id(version_id)
        )
        if version is None:
            raise LibraryNotFound
        return version

    def _resolve_parent_id(
        self, family: Dict[str, Any], parent_version_id: Optional[str]
    ) -> Optional[ObjectId]:
        raw = parent_version_id or family.get("latest_version_id")
        if not raw:
            return None
        parent_id = parse_object_id(str(raw))
        if self._library.find_family_version(family["_id"], parent_id) is None:
            raise LibraryNotFound
        return parent_id

    def _library_item(
        self, bank: Dict[str, Any], family: Dict[str, Any], version: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "bank_id": str(bank["_id"]),
            "family": self._public_family(family),
            "version": self._public_version(version),
        }

    @staticmethod
    def _public_bank(bank: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(bank["_id"]),
            "name": bank["name"],
            "is_default": bank["is_default"],
            "visibility": bank.get("visibility", "private"),
            "created_at": bank["created_at"],
            "updated_at": bank["updated_at"],
        }

    @staticmethod
    def _public_family(family: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(family["_id"]),
            "owner_id": str(family["owner_id"]),
            "visibility": family.get("visibility", "private"),
            "latest_version_id": (
                str(family["latest_version_id"])
                if family.get("latest_version_id")
                else None
            ),
            "created_at": family["created_at"],
            "updated_at": family["updated_at"],
        }

    @staticmethod
    def _public_share(share: Dict[str, Any], recipient_username: Optional[str]) -> Dict[str, Any]:
        return {
            "id": str(share["_id"]),
            "family_id": str(share["family_id"]),
            "version_id": str(share["version_id"]),
            "owner_id": str(share["owner_id"]),
            "recipient_user_id": str(share["recipient_user_id"]),
            "recipient_username": recipient_username,
            "permission": share.get("permission", "use"),
            "status": share["status"],
            "granted_at": share["granted_at"],
            "revoked_at": share.get("revoked_at"),
        }

    @staticmethod
    def _public_shared_version(version: Dict[str, Any]) -> Dict[str, Any]:
        """Expose only the granted version, not its private version graph."""
        result = {
            "id": str(version["_id"]),
            "family_id": str(version["family_id"]),
            "version_number": version["version_number"],
            "created_at": version["created_at"],
        }
        for key in ("type", "prompt", "required", "options", "validation"):
            if key in version:
                result[key] = version[key]
        return result

    @staticmethod
    def _public_version(version: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            "id": str(version["_id"]),
            "family_id": str(version["family_id"]),
            "version_number": version["version_number"],
            "parent_version_id": (
                str(version["parent_version_id"])
                if version.get("parent_version_id")
                else None
            ),
            "restored_from_version_id": (
                str(version["restored_from_version_id"])
                if version.get("restored_from_version_id")
                else None
            ),
            "created_by": str(version["created_by"]),
            "created_at": version["created_at"],
            "content_hash": version["content_hash"],
        }
        for key in ("type", "prompt", "required", "options", "validation"):
            if key in version:
                result[key] = version[key]
        return result

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_survey_id(value: str) -> ObjectId:
        if not ObjectId.is_valid(value):
            raise LibraryNotFound
        return ObjectId(value)
