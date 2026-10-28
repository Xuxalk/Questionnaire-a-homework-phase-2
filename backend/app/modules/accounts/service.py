from datetime import datetime, timezone
from typing import Any, Dict

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from backend.app.modules.accounts.passwords import hash_password, verify_password
from backend.app.modules.accounts.repository import UserRepository


class UsernameAlreadyRegistered(Exception):
    """Raised when the normalized username is already present."""


class InvalidCredentials(Exception):
    """Raised when the username or password is invalid."""


class AccountService:
    def __init__(self, users: UserRepository):
        self._users = users

    def register(self, username: str, password: str) -> Dict[str, Any]:
        display_username = username.strip()
        normalized_username = display_username.casefold()
        now = datetime.now(timezone.utc)
        user_id = ObjectId()

        user_document = {
            "_id": user_id,
            "username": display_username,
            "username_normalized": normalized_username,
            "password_hash": hash_password(password),
            "created_at": now,
            "updated_at": now,
        }

        try:
            self._users.insert_user(user_document)
        except DuplicateKeyError as exc:
            # The unique index is the source of truth, including registration races.
            raise UsernameAlreadyRegistered from exc

        return {
            "id": str(user_id),
            "username": display_username,
            "created_at": now,
            "updated_at": now,
        }

    def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        normalized_username = username.strip().casefold()
        user = self._users.find_by_normalized_username(normalized_username)
        if user is None or not verify_password(user.get("password_hash", ""), password):
            raise InvalidCredentials
        return {
            "id": str(user["_id"]),
            "username": user["username"],
            "created_at": user["created_at"],
            "updated_at": user["updated_at"],
        }
