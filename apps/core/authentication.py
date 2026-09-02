from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from bson import ObjectId
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .database import get_database
from .security import decode_access_token
from .services import now

# Sessions/last-active are only touched when this stale, to avoid a write on every request.
_ACTIVITY_TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass
class MongoUser:
    """Small request-user adapter; application users remain in MongoDB."""
    document: dict
    session_id: str | None = None

    @property
    def id(self) -> str:
        return str(self.document["_id"])

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False


def resolve_user_from_token(token: str) -> MongoUser:
    """Turn a raw bearer token string into an authenticated MongoUser.

    Shared by MongoJWTAuthentication (header-based, every normal API request) and the
    memory media streaming view (apps/api/media_views.py), which also accepts the token
    as a `?token=` query param since a plain <img>/<video> src can't carry a header.
    """
    claims = decode_access_token(token)
    try:
        user_id = ObjectId(claims["sub"])
    except (KeyError, TypeError, ValueError) as error:
        raise AuthenticationFailed("Invalid authentication token.") from error
    database = get_database()
    user = database.users.find_one({"_id": user_id}, {"password_hash": 0})
    if not user or user.get("token_version", 0) != claims.get("tv"):
        raise AuthenticationFailed("Your session is no longer valid. Please sign in again.")
    if not user.get("is_active", True):
        raise AuthenticationFailed("This account has been deactivated.")
    # Tokens issued before session tracking existed have no "sid" claim; let them keep
    # working (no forced logout on deploy) but skip per-device tracking for them.
    session_id = claims.get("sid")
    session = None
    if session_id:
        session = database.sessions.find_one({"session_id": session_id, "user_id": user_id})
        if not session or session.get("revoked"):
            raise AuthenticationFailed("This session has been signed out. Please sign in again.")
    timestamp = now()
    last_seen = session.get("last_seen_at") if session else None
    if session and (not last_seen or timestamp - last_seen > _ACTIVITY_TOUCH_INTERVAL):
        database.sessions.update_one({"_id": session["_id"]}, {"$set": {"last_seen_at": timestamp}})
        database.users.update_one({"_id": user_id}, {"$set": {"last_active_at": timestamp}})
    return MongoUser(user, session_id)


class MongoJWTAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            raise AuthenticationFailed("Use an Authorization header with a Bearer token.")
        return resolve_user_from_token(parts[1]), parts[1]
