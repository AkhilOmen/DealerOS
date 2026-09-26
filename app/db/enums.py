from enum import Enum


class Status(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Environment(str, Enum):
    PRODUCTION = "PRODUCTION"
    STAGING = "STAGING"
    SANDBOX = "SANDBOX"


class DatasetType(str, Enum):
    LOCATIONS = "LOCATIONS"
    SYSTEM_A_EVENTS = "SYSTEM_A_EVENTS"
    SYSTEM_B_ENTRIES = "SYSTEM_B_ENTRIES"


class TriggerType(str, Enum):
    MANUAL = "MANUAL"
    S3_EVENT = "S3_EVENT"  # TODO(phase-2)
    SCHEDULED = "SCHEDULED"  # TODO(phase-2)


class IngestionJobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIALLY_SUCCEEDED = "PARTIALLY_SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


class SourceSystem(str, Enum):
    SYSTEM_A = "SYSTEM_A"
    SYSTEM_B = "SYSTEM_B"


class MatchKeyMethod(str, Enum):
    EXACT = "EXACT"
    NORMALIZED = "NORMALIZED"
    UNPARSABLE = "UNPARSABLE"


class DiscrepancyType(str, Enum):
    DUPLICATE_IN_A = "DUPLICATE_IN_A"
    MISSING_IN_B = "MISSING_IN_B"
    ORPHAN_IN_B = "ORPHAN_IN_B"
    DUPLICATE_IN_B = "DUPLICATE_IN_B"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    MISSING_VALUE = "MISSING_VALUE"
    DATE_MISMATCH = "DATE_MISMATCH"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    TENANT_CONFLICT = "TENANT_CONFLICT"
    STATUS_MISMATCH = "STATUS_MISMATCH"


class DiscrepancyField(str, Enum):
    RECORD = "RECORD"
    AMOUNT = "AMOUNT"
    EVENT_DATE = "EVENT_DATE"
    LOCATION = "LOCATION"
    STATUS = "STATUS"


class DiscrepancyStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
