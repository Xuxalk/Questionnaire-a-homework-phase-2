from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pymongo import MongoClient
from pymongo.database import Database
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from backend.app.core.config import get_settings
from backend.app.api.health import router as health_router
from backend.app.db.mongo import create_mongo_client, get_database
from backend.app.modules.accounts.repository import UserRepository
from backend.app.modules.accounts.routes import router as accounts_router
from backend.app.modules.questionnaires.repository import SurveyRepository
from backend.app.modules.questionnaires.routes import router as questionnaires_router
from backend.app.modules.question_library.repository import QuestionLibraryRepository
from backend.app.modules.question_library.routes import router as question_library_router
from backend.app.modules.responses.repository import ResponseRepository
from backend.app.modules.responses.routes import router as responses_router
from backend.app.modules.statistics.routes import router as statistics_router


FRONTEND_DIRECTORY = Path(__file__).resolve().parents[2] / "frontend"


def create_app(
    database: Optional[Database] = None,
    *,
    use_environment_database: bool = True,
    session_secret_key: Optional[str] = None,
    session_cookie_secure: Optional[bool] = None,
) -> FastAPI:
    settings = get_settings()
    active_session_secret = session_secret_key or settings.session_secret_key
    secure_cookie = (
        settings.session_cookie_secure
        if session_cookie_secure is None
        else session_cookie_secure
    )
    if active_session_secret is not None and len(active_session_secret) < 32:
        raise ValueError("SESSION_SECRET_KEY must contain at least 32 characters")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        mongo_client: Optional[MongoClient] = None
        active_database = database

        if active_database is None and use_environment_database:
            settings = get_settings()
            has_uri = bool(settings.mongodb_uri)
            has_database = bool(settings.mongodb_database)
            if has_uri != has_database:
                raise RuntimeError(
                    "Both MONGODB_URI and MONGODB_DATABASE must be configured"
                )
            if has_uri and has_database:
                mongo_client = create_mongo_client(settings)
                active_database = get_database(mongo_client, settings)

        try:
            application.state.database = active_database
            application.state.sessions_enabled = active_session_secret is not None
            if active_database is not None:
                UserRepository(active_database["users"]).ensure_indexes()
                SurveyRepository(active_database["surveys"]).ensure_indexes()
                ResponseRepository(active_database["responses"]).ensure_indexes()
                QuestionLibraryRepository(active_database).ensure_indexes()
            yield
        finally:
            if mongo_client is not None:
                mongo_client.close()

    application = FastAPI(
        title="MongoDB Survey System",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.sessions_enabled = active_session_secret is not None
    if active_session_secret is not None:
        application.add_middleware(
            SessionMiddleware,
            secret_key=active_session_secret,
            session_cookie="survey_session",
            max_age=7 * 24 * 60 * 60,
            same_site="lax",
            https_only=secure_cookie,
        )

    @application.middleware("http")
    async def reject_cross_origin_mutations(request: Request, call_next):
        if not request.url.path.startswith("/api/") or request.method not in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            return await call_next(request)

        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if fetch_site in {"cross-site", "same-site"}:
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-origin request blocked"},
            )

        origin = request.headers.get("origin")
        if origin:
            origin_parts = urlsplit(origin)
            target_parts = urlsplit(str(request.base_url))
            if (
                origin == "null"
                or origin_parts.scheme.lower() != target_parts.scheme.lower()
                or origin_parts.netloc.casefold() != target_parts.netloc.casefold()
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Cross-origin request blocked"},
                )
        return await call_next(request)

    application.include_router(health_router)
    application.include_router(accounts_router)
    application.include_router(questionnaires_router)
    application.include_router(question_library_router)
    application.include_router(responses_router)
    application.include_router(statistics_router)
    application.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIRECTORY)),
        name="frontend-assets",
    )

    @application.get("/", include_in_schema=False)
    def frontend_index():
        return FileResponse(str(FRONTEND_DIRECTORY / "index.html"))

    return application


app = create_app()
