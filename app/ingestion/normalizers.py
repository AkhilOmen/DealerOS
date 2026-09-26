import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.db.enums import MatchKeyMethod

_AMOUNT_RE = re.compile(r"^-?(\d+|\d{1,3}(,\d{3})+|\d{1,2}(,\d{2})*,\d{3})(\.\d{1,2})?$")
_RECORD_REF_RE = re.compile(r"^(?:REC)?(\d+)$")
RECORD_KEY_PREFIX = "REC-"


def blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None

    value = value.strip()
    return value or None


def parse_amount(value: str | None) -> Decimal | None:
    value = blank_to_none(value)
    if value is None:
        return None

    if not _AMOUNT_RE.match(value):
        raise ValueError(f"unparsable amount {value!r}")
    try:
        return Decimal(value.replace(",", ""))

    except InvalidOperation as ex:
        raise ValueError(f"unparsable amount {value!r}") from ex


def parse_iso_date(value: str | None) -> date | None:
    # TODO(phase-2): accept other formats per source-system config if real exports need it.
    value = blank_to_none(value)
    if value is None:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError as ex:
        raise ValueError(f"unparsable date {value!r}, expected YYYY-MM-DD") from ex


def normalize_record_ref(raw: str | None) -> tuple[str | None, MatchKeyMethod]:
    if raw is None or not raw.strip():
        return None, MatchKeyMethod.UNPARSABLE

    compact = re.sub(r"[\s\-_]", "", raw).upper()
    match = _RECORD_REF_RE.match(compact)
    if not match:
        return None, MatchKeyMethod.UNPARSABLE

    key = f"{RECORD_KEY_PREFIX}{match.group(1)}"
    return key, MatchKeyMethod.EXACT if raw == key else MatchKeyMethod.NORMALIZED
