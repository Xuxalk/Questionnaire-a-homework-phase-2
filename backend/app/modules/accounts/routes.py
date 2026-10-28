from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pymongo.database import Database

from backend.app.modules.accounts.repository import UserRepository
from backend.app.modules.accounts.schemas import LoginRequest
from backend.app.modules.accounts.security import get_current_user
from backend.app.modules.accounts.schemas import (
    RegisteredUserResponse,
    RegisterRequest,
)
from backend.app.modules.accounts.service import (
    AccountService,
    InvalidCredentials,
    UsernameAlreadyRegistered,
)

router = APIRouter(prefix="/api/auth", tags=["accounts"])


def get_user_repository(request: Request) -> UserRepository:
    database: Database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB is not configured",
        )
    return UserRepository(database["users"])


@router.post(
    "/register",
    response_model=RegisteredUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
    users: UserRepository = Depends(get_user_repository),
) -> RegisteredUserResponse:
    try:
        public_user = AccountService(users).register(
            username=payload.username,
            password=payload.password,
        )
    except UsernameAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already registered",
        )

    return RegisteredUserResponse(**public_user)


@router.post("/login", response_model=RegisteredUserResponse)
def login(
    payload: LoginRequest,
    request: Request,
    users: UserRepository = Depends(get_user_repository),
) -> RegisteredUserResponse:
    if not getattr(request.app.state, "sessions_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session authentication is not configured",
        )
    try:
        public_user = AccountService(users).authenticate(
            username=payload.username,
            password=payload.password,
        )
    except InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Session"},
        )

    # Keep only the internal account ID in the signed, HttpOnly session cookie.
    request.session.clear()
    request.session["user_id"] = public_user["id"]
    return RegisteredUserResponse(**public_user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    if getattr(request.app.state, "sessions_enabled", False):
        request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=RegisteredUserResponse)
def get_me(current_user: Dict[str, Any] = Depends(get_current_user)) -> RegisteredUserResponse:
    return RegisteredUserResponse(
        id=str(current_user["_id"]),
        username=current_user["username"],
        created_at=current_user["created_at"],
        updated_at=current_user["updated_at"],
    )
