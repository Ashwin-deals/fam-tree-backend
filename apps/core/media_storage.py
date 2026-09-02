"""Pluggable storage for user-uploaded memory media.

GridFS is the only backend today, and this is the only module that imports
`gridfs`. Migrating to an object-storage bucket (S3/Azure Blob/GCS) later means
writing one new class with the same four-method shape and pointing
`get_media_storage()` at it — nothing outside this file should ever import
`gridfs` or otherwise assume how/where bytes are actually stored.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import gridfs
from bson import ObjectId

from .database import get_database


@dataclass(frozen=True)
class StoredFile:
    backend: str
    reference: str
    content_type: str
    size_bytes: int
    filename: str

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "reference": self.reference,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "filename": self.filename,
        }


class MediaStorage(Protocol):
    def save(self, upload, *, folder: str, kind: str) -> StoredFile: ...
    def save_bytes(self, data: bytes, *, filename: str, content_type: str, folder: str, kind: str) -> StoredFile: ...
    def open(self, reference: str):
        """Return a file-like object supporting .read()/.seek()/.tell() and a .length attribute."""
        ...
    def delete(self, reference: str) -> None: ...


class GridFSMediaStorage:
    backend_name = "gridfs"

    def __init__(self) -> None:
        # Bucket name deliberately distinct from the domain "memories" collection so
        # GridFS's own auto-created "memory_media.files"/"memory_media.chunks" collections
        # never sit confusingly next to it.
        self._bucket = gridfs.GridFSBucket(get_database(), bucket_name="memory_media")

    def save(self, upload, *, folder: str, kind: str) -> StoredFile:
        file_id = self._bucket.upload_from_stream(
            f"{folder}/{upload.name}",
            upload,
            metadata={"content_type": upload.content_type, "kind": kind},
        )
        return StoredFile(self.backend_name, str(file_id), upload.content_type, upload.size, upload.name)

    def save_bytes(self, data: bytes, *, filename: str, content_type: str, folder: str, kind: str) -> StoredFile:
        file_id = self._bucket.upload_from_stream(
            f"{folder}/{filename}",
            data,
            metadata={"content_type": content_type, "kind": kind},
        )
        return StoredFile(self.backend_name, str(file_id), content_type, len(data), filename)

    def open(self, reference: str):
        return self._bucket.open_download_stream(ObjectId(reference))

    def delete(self, reference: str) -> None:
        try:
            self._bucket.delete(ObjectId(reference))
        except gridfs.errors.NoFile:
            pass


_storage: MediaStorage | None = None


def get_media_storage() -> MediaStorage:
    global _storage
    if _storage is None:
        _storage = GridFSMediaStorage()
    return _storage
