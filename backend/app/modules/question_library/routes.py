from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pymongo.database import Database

from backend.app.modules.accounts.security import get_current_user
from backend.app.modules.accounts.repository import UserRepository
from backend.app.modules.question_library.repository import QuestionLibraryRepository
from backend.app.modules.question_library.schemas import (
    BankCreateRequest,
    BankItemRequest,
    BankItemVersionRequest,
    ShareQuestionVersionRequest,
    SurveyQuestionImportRequest,
)
from backend.app.modules.question_library.service import (
    LibraryConflict,
    LibraryContentInvalid,
    LibraryNotFound,
    QuestionLibraryService,
)
from backend.app.modules.questionnaires.repository import SurveyRepository
from backend.app.modules.questionnaires.schemas import QuestionInput


router = APIRouter(prefix="/api/question-library", tags=["question-library"])


def get_question_library_service(request: Request) -> QuestionLibraryService:
    database: Database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB is not configured",
        )
    return QuestionLibraryService(
        QuestionLibraryRepository(database),
        SurveyRepository(database["surveys"]),
        UserRepository(database["users"]),
    )


def _invoke(operation):
    try:
        return operation()
    except LibraryNotFound:
        raise HTTPException(status_code=404, detail="Question library item not found")
    except LibraryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LibraryContentInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/banks")
def list_banks(
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return service.list_banks(current_user["_id"])


@router.post("/banks", status_code=status.HTTP_201_CREATED)
def create_bank(
    payload: BankCreateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(lambda: service.create_bank(current_user["_id"], payload.name))


@router.get("/banks/{bank_id}/items")
def list_bank_items(
    bank_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(lambda: service.list_bank_items(current_user["_id"], bank_id))


@router.post("/banks/{bank_id}/items", status_code=status.HTTP_201_CREATED)
def add_bank_item(
    bank_id: str,
    payload: BankItemRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.add_bank_item(
            current_user["_id"], bank_id, payload.family_id, payload.version_id
        )
    )


@router.put("/banks/{bank_id}/items/{family_id}")
def update_bank_item_version(
    bank_id: str,
    family_id: str,
    payload: BankItemVersionRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.update_bank_item_version(
            current_user["_id"], bank_id, family_id, payload.version_id
        )
    )


@router.delete("/banks/{bank_id}/items/{family_id}", status_code=204)
def remove_bank_item(
    bank_id: str,
    family_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    _invoke(lambda: service.remove_bank_item(current_user["_id"], bank_id, family_id))


@router.get("/shares/received")
def list_received_shares(
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return service.list_received_shares(current_user["_id"])


@router.post("/questions", status_code=status.HTTP_201_CREATED)
def create_library_question(
    payload: QuestionInput,
    bank_id: Optional[str] = Query(default=None),
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.create_question(
            current_user["_id"], payload, bank_id=bank_id
        )
    )


@router.post("/from-survey/{survey_id}/{question_id}", status_code=201)
def import_survey_question(
    survey_id: str,
    question_id: str,
    payload: SurveyQuestionImportRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.import_survey_question(
            current_user["_id"],
            survey_id,
            question_id,
            bank_id=payload.bank_id,
        )
    )


@router.get("/questions/{family_id}/versions")
def list_versions(
    family_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(lambda: service.list_versions(current_user["_id"], family_id))


@router.post("/questions/{family_id}/versions/{version_id}/shares")
def grant_version_share(
    family_id: str,
    version_id: str,
    payload: ShareQuestionVersionRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.grant_version_share(
            current_user["_id"], family_id, version_id, payload.recipient_username
        )
    )


@router.get("/questions/{family_id}/versions/{version_id}/shares")
def list_version_shares(
    family_id: str,
    version_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.list_version_shares(
            current_user["_id"], family_id, version_id
        )
    )


@router.delete(
    "/questions/{family_id}/versions/{version_id}/shares/{recipient_user_id}"
)
def revoke_version_share(
    family_id: str,
    version_id: str,
    recipient_user_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.revoke_version_share(
            current_user["_id"], family_id, version_id, recipient_user_id
        )
    )


@router.get("/questions/{family_id}/usages")
def list_question_usages(
    family_id: str,
    version_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.list_usages(
            current_user["_id"], family_id, version_id=version_id
        )
    )


@router.post("/questions/{family_id}/versions", status_code=201)
def create_version(
    family_id: str,
    payload: QuestionInput,
    parent_version_id: Optional[str] = Query(default=None),
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.create_version(
            current_user["_id"],
            family_id,
            payload,
            parent_version_id=parent_version_id,
        )
    )


@router.post("/questions/{family_id}/versions/{version_id}/restore", status_code=201)
def restore_version(
    family_id: str,
    version_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: QuestionLibraryService = Depends(get_question_library_service),
):
    return _invoke(
        lambda: service.restore_version(
            current_user["_id"], family_id, version_id
        )
    )
