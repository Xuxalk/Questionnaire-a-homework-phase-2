"""Safely backfill first-phase survey questions into immutable library versions.

The command defaults to a read-only dry run. Use --apply only with a dedicated,
backed-up database after reviewing the printed preflight report.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from bson import ObjectId
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from backend.app.core.config import get_settings
from backend.app.modules.question_library.repository import QuestionLibraryRepository
from backend.app.modules.question_library.service import question_content_hash
from backend.app.modules.questionnaires.schemas import QuestionInput


MIGRATION_KEY = "question-library-v1"


def _deterministic_object_id(seed: str) -> ObjectId:
    return ObjectId(hashlib.sha256(seed.encode("utf-8")).digest()[:12])


def _canonical_content(question: Dict[str, Any]) -> Dict[str, Any]:
    from pydantic import TypeAdapter

    candidate = {
        key: value
        for key, value in question.items()
        if key not in {"question_id", "jump_rules", "library_ref"}
    }
    candidate["jump_rules"] = []
    parsed = TypeAdapter(QuestionInput).validate_python(candidate)
    normalized = parsed.model_dump(exclude_none=True)
    return {
        key: normalized[key]
        for key in ("type", "prompt", "required", "options", "validation")
        if key in normalized
    }


def _valid_origin(survey: Dict[str, Any], question: Any) -> Tuple[bool, str]:
    if not isinstance(survey.get("_id"), ObjectId):
        return False, "survey has no valid ObjectId"
    if not isinstance(survey.get("owner_id"), ObjectId):
        return False, "survey owner_id is missing or invalid"
    if not isinstance(question, dict):
        return False, "question is not a document"
    question_id = question.get("question_id")
    if not isinstance(question_id, str) or not question_id.strip():
        return False, "question_id is missing or invalid"
    try:
        _canonical_content(question)
    except Exception as exc:
        return False, "question content is invalid: {}".format(str(exc))
    return True, ""


def _scan(database) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    plans: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        "migration_key": MIGRATION_KEY,
        "surveys_scanned": 0,
        "questions_scanned": 0,
        "would_link": 0,
        "already_linked": 0,
        "skipped": 0,
        "failed": 0,
        "failures": [],
    }
    for survey in database["surveys"].find({}).sort("_id", 1):
        result["surveys_scanned"] += 1
        questions = survey.get("questions", [])
        if not isinstance(questions, list):
            result["failed"] += 1
            result["failures"].append(
                {
                    "survey_id": str(survey.get("_id")),
                    "question_id": None,
                    "reason": "questions is not an array",
                }
            )
            continue
        counts: Dict[str, int] = {}
        for question in questions:
            if isinstance(question, dict) and isinstance(
                question.get("question_id"), str
            ):
                qid = question["question_id"]
                counts[qid] = counts.get(qid, 0) + 1
        for question in questions:
            result["questions_scanned"] += 1
            qid = question.get("question_id") if isinstance(question, dict) else None
            if isinstance(qid, str) and counts.get(qid, 0) > 1:
                result["failed"] += 1
                result["failures"].append(
                    {
                        "survey_id": str(survey["_id"]),
                        "question_id": qid,
                        "reason": "duplicate question_id in one survey",
                    }
                )
                continue
            valid, reason = _valid_origin(survey, question)
            if not valid:
                result["failed"] += 1
                result["failures"].append(
                    {
                        "survey_id": str(survey.get("_id")),
                        "question_id": qid,
                        "reason": reason,
                    }
                )
                continue
            ref = question.get("library_ref") or {}
            if ref:
                family_id = ref.get("source_question_id")
                version_id = ref.get("source_version_id")
                if not isinstance(family_id, ObjectId) or not isinstance(
                    version_id, ObjectId
                ):
                    result["failed"] += 1
                    result["failures"].append(
                        {
                            "survey_id": str(survey["_id"]),
                            "question_id": qid,
                            "reason": "library_ref is incomplete or invalid",
                        }
                    )
                    continue
                family = database["question_families"].find_one({"_id": family_id})
                version = database["question_versions"].find_one(
                    {"_id": version_id, "family_id": family_id}
                )
                if family is None or version is None:
                    result["failed"] += 1
                    result["failures"].append(
                        {
                            "survey_id": str(survey["_id"]),
                            "question_id": qid,
                            "reason": "library_ref points to a missing family or version",
                        }
                    )
                    continue
                if family.get("owner_id") != survey.get("owner_id"):
                    result["failed"] += 1
                    result["failures"].append(
                        {
                            "survey_id": str(survey["_id"]),
                            "question_id": qid,
                            "reason": "private library reference belongs to another user",
                        }
                    )
                    continue
                content = _canonical_content(question)
                if version.get("content_hash") != question_content_hash(content):
                    result["failed"] += 1
                    result["failures"].append(
                        {
                            "survey_id": str(survey["_id"]),
                            "question_id": qid,
                            "reason": "linked version content does not match survey snapshot",
                        }
                    )
                    continue
                result["already_linked"] += 1
                continue
            result["would_link"] += 1
            plans.append(
                {
                    "survey_id": survey["_id"],
                    "owner_id": survey["owner_id"],
                    "question_id": qid,
                    "question": question,
                    "content": _canonical_content(question),
                }
            )
    return plans, result


def _find_or_create_family(database, library, plan: Dict[str, Any], now: datetime):
    survey_id = plan["survey_id"]
    question_id = plan["question_id"]
    family = library.find_family_by_origin(survey_id, question_id)
    if family is not None:
        if family.get("owner_id") != plan["owner_id"]:
            raise ValueError("origin family belongs to a different user")
        return family
    family_id = _deterministic_object_id(
        "question-family:{}:{}".format(survey_id, question_id)
    )
    family = {
        "_id": family_id,
        "owner_id": plan["owner_id"],
        "visibility": "private",
        "origin_survey_id": survey_id,
        "origin_question_id": question_id,
        "last_version_number": 0,
        "latest_version_id": None,
        "created_at": now,
        "updated_at": now,
        "migration_key": MIGRATION_KEY,
    }
    try:
        library.insert_family(family)
    except DuplicateKeyError:
        family = library.find_family_by_origin(survey_id, question_id)
        if family is None or family.get("owner_id") != plan["owner_id"]:
            raise ValueError("origin family was concurrently created with conflicting data")
    return family


def _find_or_create_version(database, library, family, content, owner_id, now):
    digest = question_content_hash(content)
    versions = database["question_versions"]
    existing = versions.find_one({"family_id": family["_id"], "content_hash": digest})
    if existing is not None:
        # A previous run may have inserted the immutable version and then
        # stopped before updating the family's latest-version pointer. Repair
        # that narrow partial-write state before treating the version as done.
        current_family = library.find_family(family["_id"])
        if current_family is None:
            raise ValueError("question family disappeared during migration")
        if (
            existing.get("version_number") == current_family.get("last_version_number")
            and current_family.get("latest_version_id") != existing["_id"]
        ):
            library.set_latest_version(
                family["_id"], existing["version_number"], existing["_id"]
            )
        return existing
    version_number = library.allocate_version_number(family["_id"], now)
    if version_number is None:
        raise ValueError("question family disappeared during migration")
    version_id = _deterministic_object_id(
        "question-version:{}:{}:{}".format(family["_id"], version_number, digest)
    )
    parent_id = family.get("latest_version_id")
    version = {
        "_id": version_id,
        "family_id": family["_id"],
        "version_number": version_number,
        "parent_version_id": parent_id,
        "created_by": owner_id,
        "created_at": now,
        "content_hash": digest,
        "migration_key": MIGRATION_KEY,
        **content,
    }
    try:
        library.insert_version(version)
    except DuplicateKeyError:
        existing = versions.find_one(
            {"family_id": family["_id"], "version_number": version_number}
        )
        if existing is None or existing.get("content_hash") != digest:
            raise ValueError("version number collision with different immutable content")
        version = existing
    library.set_latest_version(
        family["_id"], version["version_number"], version["_id"]
    )
    return version


def migrate_question_library(database, *, dry_run: bool = True) -> Dict[str, Any]:
    """Preflight and optionally migrate all safe legacy survey question instances."""
    plans, result = _scan(database)
    result["mode"] = "dry-run" if dry_run else "apply"
    result["linked"] = 0
    result["run_id"] = str(uuid4())
    result["started_at"] = datetime.now(timezone.utc).isoformat()
    if dry_run:
        return result

    library = QuestionLibraryRepository(database)
    library.ensure_indexes()
    for plan in plans:
        now = datetime.now(timezone.utc)
        try:
            family = _find_or_create_family(database, library, plan, now)
            version = _find_or_create_version(
                database, library, family, plan["content"], plan["owner_id"], now
            )
            bank = library.ensure_default_bank(plan["owner_id"], now)
            if library.find_bank_item(bank["_id"], family["_id"]) is None:
                try:
                    library.insert_bank_item(
                        {
                            "_id": ObjectId(),
                            "bank_id": bank["_id"],
                            "family_id": family["_id"],
                            "version_id": version["_id"],
                            "added_by": plan["owner_id"],
                            "created_at": now,
                            "updated_at": now,
                            "migration_key": MIGRATION_KEY,
                        }
                    )
                except DuplicateKeyError:
                    # Safe under a retry/concurrent migration of the same survey.
                    pass
            expected_question = dict(plan["question"])
            expected_question.pop("library_ref", None)
            new_ref = {
                "source_question_id": family["_id"],
                "source_version_id": version["_id"],
            }
            result_update = database["surveys"].update_one(
                {
                    "_id": plan["survey_id"],
                    "questions": {"$elemMatch": expected_question},
                },
                {
                    "$set": {
                        "questions.$.library_ref": new_ref,
                        "updated_at": now,
                    }
                },
            )
            if result_update.modified_count != 1:
                raise ValueError("survey changed after preflight; rerun after review")
            result["linked"] += 1
        except Exception as exc:
            result["failed"] += 1
            result["failures"].append(
                {
                    "survey_id": str(plan["survey_id"]),
                    "question_id": plan["question_id"],
                    "reason": "migration write failed safely: {}".format(str(exc)),
                }
            )
    result["would_link"] = len(plans)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    library.record_migration(MIGRATION_KEY, result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="read and report only (default; performs no database writes)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="write version records and source references to configured MongoDB",
    )
    parser.add_argument(
        "--report-file",
        help="optional path for a JSON report; stdout is always written",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    if not settings.mongodb_uri or not settings.mongodb_database:
        parser.error("MONGODB_URI and MONGODB_DATABASE must be configured")
    if args.apply:
        print(
            "Applying migration to database {!r}. Confirm this is an isolated or backed-up database.".format(
                settings.mongodb_database
            ),
            file=sys.stderr,
        )
    client = MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        tz_aware=True,
    )
    try:
        client.admin.command("ping")
        result = migrate_question_library(
            client[settings.mongodb_database], dry_run=not args.apply
        )
    finally:
        client.close()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.report_file:
        report_path = Path(args.report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    return 2 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
