from typing import Any, Dict

from bson import ObjectId
from fastapi import HTTPException, Request, status

from backend.app.modules.accounts.repository import UserRepository


def get_current_user(request: Request) -> Dict[str, Any]:
    """Load the account identified by the signed session cookie."""
    if not getattr(request.app.state, "sessions_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session authentication is not configured",
        )

    user_id = request.session.get("user_id")
    if not isinstance(user_id, str) or not ObjectId.is_valid(user_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )

    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB is not configured",
        )

    user = UserRepository(database["users"]).find_by_id(ObjectId(user_id))
    if user is None:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    return user
