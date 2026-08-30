"""JWT creation and validation; no refresh-token collection is required."""
from __future__ import annotations

from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed


def create_access_token(user: dict) -> str:
    now = timezone.now()
    payload = {
        "sub": str(user["_id"]),
        "email": user["email"],
        "tv": user.get("token_version", 0),
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_MINUTES),
        "iss": "family-tree-api",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"], issuer="family-tree-api")
    except jwt.ExpiredSignatureError as error:
        raise AuthenticationFailed("Your session has expired. Please sign in again.") from error
    except jwt.PyJWTError as error:
        raise AuthenticationFailed("Invalid authentication token.") from error
