from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pymongo.database import Database

from backend.app.modules.accounts.security import get_current_user
from backend.app.modules.question_library.repository import QuestionLibraryRepository
from backend.app.modules.questionnaires.repository import SurveyRepository
from backend.app.modules.statistics.repository import StatisticsRepository
from backend.app.modules.statistics.service import (
    StatisticsNotFound,
    StatisticsService,
)


router = APIRouter(prefix="/api/statistics", tags=["statistics"])


def get_statistics_service(request: Request) -> StatisticsService:
    database: Database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB is not configured",
        )
    return StatisticsService(
        SurveyRepository(database["surveys"]),
        StatisticsRepository(database["responses"], database["surveys"]),
        QuestionLibraryRepository(database),
    )


def _invoke(operation):
    try:
        return operation()
    except StatisticsNotFound:
        raise HTTPException(status_code=404, detail="Survey or question not found")


@router.get("/surveys/{survey_id}")
def get_survey_statistics(
    survey_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: StatisticsService = Depends(get_statistics_service),
):
    return _invoke(
        lambda: service.get_survey_statistics(survey_id, current_user["_id"])
    )


@router.get("/surveys/{survey_id}/questions/{question_id}")
def get_question_statistics(
    survey_id: str,
    question_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: StatisticsService = Depends(get_statistics_service),
):
    return _invoke(
        lambda: service.get_question_statistics(
            survey_id, question_id, current_user["_id"]
        )
    )


@router.get("/question-library/{family_id}/versions")
def list_question_library_statistics_versions(
    family_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: StatisticsService = Depends(get_statistics_service),
):
    return _invoke(
        lambda: service.list_library_question_versions(
            family_id, current_user["_id"]
        )
    )


@router.get("/question-library/{family_id}/versions/{version_id}")
def get_question_library_version_statistics(
    family_id: str,
    version_id: str,
    survey_id: Optional[str] = Query(default=None, min_length=1, max_length=64),
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: StatisticsService = Depends(get_statistics_service),
):
    return _invoke(
        lambda: service.get_library_version_statistics(
            family_id,
            version_id,
            current_user["_id"],
            survey_id=survey_id,
        )
    )
