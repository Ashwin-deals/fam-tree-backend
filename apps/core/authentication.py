from __future__ import annotations

from dataclasses import dataclass

from bson import ObjectId
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .database import get_database
from .security import decode_access_token


@dataclass
class MongoUser:
    """Small request-user adapter; application users remain in MongoDB."""
    document: dict

    @property
    def id(self) -> str:
        return str(self.document["_id"])

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False


class MongoJWTAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            raise AuthenticationFailed("Use an Authorization header with a Bearer token.")
        claims = decode_access_token(parts[1])
        try:
            user_id = ObjectId(claims["sub"])
        except (KeyError, TypeError, ValueError) as error:
            raise AuthenticationFailed("Invalid authentication token.") from error
        user = get_database().users.find_one({"_id": user_id}, {"password_hash": 0})
        if not user or user.get("token_version", 0) != claims.get("tv"):
            raise AuthenticationFailed("Your session is no longer valid. Please sign in again.")
        return MongoUser(user), parts[1]
