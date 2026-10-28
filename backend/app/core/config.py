import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Settings:
    mongodb_uri: Optional[str]
    mongodb_database: Optional[str]
    mongodb_server_selection_timeout_ms: int
    session_secret_key: Optional[str]
    session_cookie_secure: bool

    @classmethod
    def from_environment(cls) -> "Settings":
        timeout_value = os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "5000")
        try:
            timeout_ms = int(timeout_value)
        except ValueError as exc:
            raise ValueError(
                "MONGODB_SERVER_SELECTION_TIMEOUT_MS must be an integer"
            ) from exc
        if timeout_ms <= 0:
            raise ValueError("MONGODB_SERVER_SELECTION_TIMEOUT_MS must be positive")

        secure_cookie_value = os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower()
        if secure_cookie_value not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("SESSION_COOKIE_SECURE must be a boolean value")
        secure_cookie = secure_cookie_value in {"true", "1", "yes"}

        return cls(
            mongodb_uri=os.getenv("MONGODB_URI") or None,
            mongodb_database=os.getenv("MONGODB_DATABASE") or None,
            mongodb_server_selection_timeout_ms=timeout_ms,
            session_secret_key=os.getenv("SESSION_SECRET_KEY") or None,
            session_cookie_secure=secure_cookie,
        )

    def require_mongodb_uri(self) -> str:
        if not self.mongodb_uri:
            raise RuntimeError("MONGODB_URI is not configured")
        return self.mongodb_uri

    def require_mongodb_database(self) -> str:
        if not self.mongodb_database:
            raise RuntimeError("MONGODB_DATABASE is not configured")
        return self.mongodb_database


def get_settings() -> Settings:
    """Read configuration at call time so tests and deployments can set env vars."""
    return Settings.from_environment()
