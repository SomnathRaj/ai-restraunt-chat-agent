"""MongoDB connection + index setup.

Kept deliberately thin: MongoDB is schema-less and every service validates
its own inputs, so a per-collection model/ORM layer would be unneeded
indirection for a single-restaurant, no-auth V1 (see ARCHITECTURE.md Section 6).

The client is created lazily on first use, never at import time, so the app
can boot with MONGODB_URI unset (see ARCHITECTURE.md Section 7).
"""

import logging

from flask import current_app
from pymongo import ASCENDING, MongoClient
from pymongo.errors import PyMongoError

log = logging.getLogger(__name__)


class DatabaseNotConfigured(Exception):
    """Raised when a DB-backed operation runs before MONGODB_URI is set."""


def _create_client():
    uri = current_app.config.get("MONGODB_URI")
    if not uri:
        return None
    return MongoClient(uri, serverSelectionTimeoutMS=3000)


def get_client():
    """Return a MongoClient cached on the app instance, or None if unconfigured.

    Cached on current_app.extensions (not flask.g) because a MongoClient
    manages its own connection pool and is meant to be a long-lived
    singleton per process, not recreated on every request.
    """
    if "mongo_client" not in current_app.extensions:
        current_app.extensions["mongo_client"] = _create_client()
    return current_app.extensions["mongo_client"]


def get_db():
    """Return the app database, or raise DatabaseNotConfigured if MONGODB_URI is unset.

    Routes should catch this (or call is_configured() first) and return a
    clean 503 rather than letting a pymongo traceback reach the client.

    Ensures indexes exist once per app instance on first successful call --
    in particular the unique index on chat_sessions.session_id, which closes
    a real race: without it, two concurrent requests upserting the same new
    session_id could both decide to insert, creating duplicate session docs.
    """
    client = get_client()
    if client is None:
        raise DatabaseNotConfigured("MONGODB_URI is not set")
    db = client[current_app.config["MONGODB_DATABASE"]]
    if not current_app.extensions.get("mongo_indexes_ensured"):
        ensure_indexes(db)
        current_app.extensions["mongo_indexes_ensured"] = True
    return db


def is_configured() -> bool:
    return bool(current_app.config.get("MONGODB_URI"))


def ping() -> bool:
    """Used by /healthz. Never raises -- returns False on any failure."""
    client = get_client()
    if client is None:
        return False
    try:
        client.admin.command("ping")
        return True
    except PyMongoError:
        return False


def ensure_indexes(db):
    """Create indexes for all collections. Safe to call repeatedly (idempotent).

    Call this once a working DB connection is available (e.g. from a setup
    script or lazily on first successful request) -- never at import time.
    """
    db.menu.create_index([("item_id", ASCENDING)], unique=True)
    db.menu.create_index([("active", ASCENDING), ("availability", ASCENDING)])
    db.menu.create_index([("category", ASCENDING)])

    db.categories.create_index([("name", ASCENDING)], unique=True)

    db.faq.create_index([("faq_id", ASCENDING)], unique=True)
    db.faq.create_index([("active", ASCENDING)])

    db.orders.create_index([("order_id", ASCENDING)], unique=True)
    db.orders.create_index([("mobile", ASCENDING)])

    db.chat_sessions.create_index([("session_id", ASCENDING)], unique=True)
    db.chat_sessions.create_index("updated_at", expireAfterSeconds=current_app.config["SESSION_TTL_SECONDS"])

    db.users.create_index([("email", ASCENDING)], unique=True)

    log.info("MongoDB indexes ensured")
