"""Authenticated streaming of Family Moments media.

Deliberately a plain Django view, not a DRF APIView: it must return raw bytes
(with manual HTTP Range support for video seeking) rather than go through DRF's
JSON renderer/exception handler, which are the wrong tool for a binary stream.
A plain <img>/<video> tag can't attach an Authorization header, so this view
accepts the access token as a `?token=` query param as a fallback — the exact
same ownership/family-membership/visibility check that gates the JSON API also
gates every byte here, so a leaked media URL grants no more access than the
JSON API already would.
"""
from __future__ import annotations

import re

from django.http import HttpResponse, StreamingHttpResponse
from rest_framework.exceptions import AuthenticationFailed, ValidationError

from apps.core.authentication import resolve_user_from_token
from apps.core.database import get_database
from apps.core.services import can_view_memory, household_ids_for_user_in_family, membership_for, object_id, open_memory_media

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _resolve_request_user(request):
    header = request.headers.get("Authorization", "")
    token = None
    parts = header.split()
    if len(parts) == 2 and parts[0] == "Bearer":
        token = parts[1]
    if not token:
        token = request.GET.get("token")
    if not token:
        return None
    try:
        return resolve_user_from_token(token)
    except AuthenticationFailed:
        return None


def stream_memory_media(request, memory_id: str, file_id: str):
    user = _resolve_request_user(request)
    if user is None:
        return HttpResponse(status=401)

    database = get_database()
    try:
        memory = database.memories.find_one({"_id": object_id(memory_id, "memory_id")})
    except ValidationError:
        memory = None
    if not memory:
        return HttpResponse(status=404)

    family = database.families.find_one({"_id": memory["family_id"]})
    if not family or not membership_for(family, user.document["_id"]):
        return HttpResponse(status=404)

    household_ids = household_ids_for_user_in_family(memory["family_id"], user.document["_id"])
    if not can_view_memory(memory, user.document["_id"], household_ids):
        return HttpResponse(status=404)

    storage = memory.get("storage", {})
    references = {ref["reference"]: ref for ref in (storage.get("media"), storage.get("thumbnail")) if ref}
    stored = references.get(file_id)
    if not stored:
        return HttpResponse(status=404)

    try:
        filelike = open_memory_media(file_id)
    except Exception:
        return HttpResponse(status=404)

    return _stream_with_range_support(request, filelike, stored["content_type"])


def _stream_with_range_support(request, filelike, content_type: str) -> HttpResponse:
    total = filelike.length
    match = _RANGE_RE.match(request.headers.get("Range", ""))
    start, end, status_code = 0, total - 1, 200
    if match:
        raw_start, raw_end = match.groups()
        start = int(raw_start) if raw_start else 0
        end = min(int(raw_end), total - 1) if raw_end else total - 1
        if total == 0 or start > end or start >= total:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{total}"
            return response
        status_code = 206

    filelike.seek(start)
    remaining = end - start + 1

    def chunks(remaining_bytes: int, chunk_size: int = 64 * 1024):
        while remaining_bytes > 0:
            data = filelike.read(min(chunk_size, remaining_bytes))
            if not data:
                break
            remaining_bytes -= len(data)
            yield data

    response = StreamingHttpResponse(chunks(remaining), status=status_code, content_type=content_type)
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(remaining)
    response["Cache-Control"] = "private, max-age=3600"
    response["Referrer-Policy"] = "same-origin"
    if status_code == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{total}"
    return response
