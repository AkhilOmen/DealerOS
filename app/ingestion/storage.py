import hashlib
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import unquote, urlparse

from app.core.config import settings
from app.db.enums import DatasetType
from app.utils.error import FileStorageError, FileTooLargeError


@dataclass(frozen=True)
class StoredFile:
    uri: str
    sha256: str
    size_bytes: int


def safe_file_name(name: str) -> str:
    name = Path(name).name  # drop any client-supplied directories
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "upload.csv"


LOCAL_SCHEME = "local"


class LocalFileStore:
    """Files live under DATA_DIR/<dataset_type>/<job_id>/<file_name> and are addressed as
    local://<dataset_type>/<job_id>/<file_name>, i.e. relative to DATA_DIR, so the API, the
    CLI on the host and the consumer in docker all resolve the same file.

    TODO(phase-2): S3FileStore with the same interface; file_uri becomes s3://bucket/key and
        the S3 bridge (Lambda) creates the job + publishes the same message.
    """

    def __init__(self, root: str = settings.DATA_DIR):
        self.root = Path(root).resolve()

    async def save(
        self,
        job_id: uuid.UUID,
        dataset_type: DatasetType,
        file_name: str,
        chunks: AsyncIterator[bytes],
        max_bytes: int = settings.MAX_UPLOAD_BYTES,
    ) -> StoredFile:

        key = f"{dataset_type.value.lower()}/{job_id}/{safe_file_name(file_name)}"
        path = self.root / key
        digest = hashlib.sha256()
        size = 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as out:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLargeError(f"file exceeds {max_bytes} bytes")
                    digest.update(chunk)
                    out.write(chunk)
        except OSError as ex:
            path.unlink(missing_ok=True)
            raise FileStorageError(f"could not store {key}: {ex}") from ex
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return StoredFile(uri=f"{LOCAL_SCHEME}://{key}", sha256=digest.hexdigest(), size_bytes=size)

    def path_for(self, uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != LOCAL_SCHEME:
            raise ValueError(f"unsupported file uri scheme {parsed.scheme!r}")
        path = (self.root / unquote(parsed.netloc + parsed.path)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"file uri escapes the data directory: {uri!r}")
        return path

    def open_text(self, uri: str) -> TextIO:
        return self.path_for(uri).open(
            "r",
            encoding="utf-8-sig",
            newline=""
        )


file_store = LocalFileStore()
