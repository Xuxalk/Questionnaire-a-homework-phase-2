from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.database import Database

from backend.app.modules.accounts.security import get_current_user
from backend.app.modules.questionnaires.repository import SurveyRepository
from backend.app.modules.responses.repository import ResponseRepository
from backend.app.modules.responses.schemas import StepSaveRequest, SubmitRequest
from backend.app.modules.responses.service import (
    FillSurveyNotFound,
    ResponseConflict,
    ResponseContentInvalid,
    ResponseIncomplete,
    ResponseNotFound,
    ResponseService,
    SurveyNotAvailable,
)


router = APIRouter(prefix="/api/fill", tags=["responses"])


def get_response_service(request: Request) -> ResponseService:
    database: Database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB is not configured",
        )
    return ResponseService(
        SurveyRepository(database["surveys"]),
        ResponseRepository(database["responses"]),
    )


def _invoke(operation):
    try:
        return operation()
    except FillSurveyNotFound:
        raise HTTPException(status_code=404, detail="Survey not found")
    except ResponseNotFound:
        raise HTTPException(status_code=404, detail="Response not found")
    except SurveyNotAvailable as exc:
        detail = (
            "Survey is closed"
            if exc.reason == "closed"
            else "Survey deadline has passed"
        )
        raise HTTPException(status_code=410, detail=detail)
    except ResponseConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ResponseContentInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ResponseIncomplete as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Response is incomplete",
                "next_question_id": exc.question_id,
            },
        )


@router.get("/{survey_id}")
def get_shared_survey(
    survey_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: ResponseService = Depends(get_response_service),
):
    # Authentication is required for every respondent, including anonymous surveys.
    return _invoke(lambda: service.get_shared_survey(survey_id))


@router.post("/{survey_id}/responses", status_code=status.HTTP_201_CREATED)
def start_or_resume_response(
    survey_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: ResponseService = Depends(get_response_service),
):
    return _invoke(
        lambda: service.start_or_resume(survey_id, current_user["_id"])
    )


@router.get("/{survey_id}/responses/{response_id}")
def get_response(
    survey_id: str,
    response_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: ResponseService = Depends(get_response_service),
):
    return _invoke(
        lambda: service.get_response(
            survey_id, response_id, current_user["_id"]
        )
    )


@router.put("/{survey_id}/responses/{response_id}/steps/{question_id}")
def save_step(
    survey_id: str,
    response_id: str,
    question_id: str,
    payload: StepSaveRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: ResponseService = Depends(get_response_service),
):
    return _invoke(
        lambda: service.save_step(
            survey_id,
            response_id,
            question_id,
            current_user["_id"],
            revision=payload.revision,
            answer=payload.answer,
            skip=payload.skip,
        )
    )


@router.post("/{survey_id}/responses/{response_id}/submit")
def submit_response(
    survey_id: str,
    response_id: str,
    payload: SubmitRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: ResponseService = Depends(get_response_service),
):
    return _invoke(
        lambda: service.submit(
            survey_id,
            response_id,
            current_user["_id"],
            revision=payload.revision,
        )
    )
