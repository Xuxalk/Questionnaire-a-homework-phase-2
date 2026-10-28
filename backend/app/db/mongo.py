from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database

from backend.app.core.config import Settings, get_settings


def create_mongo_client(settings: Optional[Settings] = None) -> MongoClient:
    """Create a client from environment-backed settings; no connection is made here."""
    config = settings or get_settings()
    return MongoClient(
        config.require_mongodb_uri(),
        serverSelectionTimeoutMS=config.mongodb_server_selection_timeout_ms,
        tz_aware=True,
    )


def get_database(
    client: MongoClient, settings: Optional[Settings] = None
) -> Database:
    config = settings or get_settings()
    return client[config.require_mongodb_database()]
