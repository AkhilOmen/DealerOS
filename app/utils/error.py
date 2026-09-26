"""All custom exceptions, in one place."""


# --- DB ---
class UniqueKeyViolationError(Exception):
    """An insert hit a unique constraint."""


class MissingTenantError(Exception):
    """A tenant-scoped read was attempted without a tenant_id."""


# --- Ingestion ---
class FileTooLargeError(Exception):
    """The upload exceeds MAX_UPLOAD_BYTES."""


class FileFormatError(Exception):
    """The file as a whole is unusable (bad headers, not a CSV). Not retryable."""


class RowRejection(Exception):
    """A single row can't be stored; recorded in the job's rejected_row_details."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class EnqueueError(Exception):
    """The job row exists (marked FAILED) but its message could not be published."""
