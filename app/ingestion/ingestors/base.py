import csv
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, ClassVar, TextIO

from pydantic import ValidationError

from app.db.enums import DatasetType
from app.db.models import IngestionJob
from app.db.session import AsyncDataStore
from app.schemas.rows import CsvRow
from app.utils.error import FileFormatError

EXTRA_COLUMNS_KEY = "__extra_columns__"


@dataclass(frozen=True)
class ParsedRow[RowT: CsvRow]:
    row_number: int
    raw: dict[str, Any]
    row: RowT


@dataclass
class IngestResult:
    rows_received: int = 0
    rows_loaded: int = 0
    rejected_row_details: list[dict[str, Any]] = field(default_factory=list)

    def reject(self, row_number: int, raw: dict[str, Any], reason: str, detail: str) -> None:
        self.rejected_row_details.append({"row": row_number, "reason": reason, "detail": detail, "raw": raw})

    @property
    def rows_rejected(self) -> int:
        return len(self.rejected_row_details)


def _format_validation_error(ex: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'row'}: {err['msg']}" for err in ex.errors())


class BaseIngestor[RowT: CsvRow](ABC):
    dataset_type: ClassVar[DatasetType]
    row_model: ClassVar[type[CsvRow]]
    key_field: ClassVar[str]  # natural key within one file

    @property
    def expected_headers(self) -> set[str]:
        return set(self.row_model.model_fields)

    async def ingest(self, ads: AsyncDataStore, job: IngestionJob, fh: TextIO) -> IngestResult:
        rows, result = self.parse(fh)
        await self.load(ads, job, rows, result)
        return result

    def parse(self, fh: TextIO) -> tuple[list[ParsedRow[RowT]], IngestResult]:
        """Pure: headers, per-row validation and in-file duplicates. No database."""
        reader = csv.DictReader(fh, restkey=EXTRA_COLUMNS_KEY)
        headers = [h.strip() for h in (reader.fieldnames or [])]
        missing = self.expected_headers - set(headers)
        if missing:
            raise FileFormatError(f"missing columns {sorted(missing)}; got {headers}")
        reader.fieldnames = headers

        result = IngestResult()
        parsed: list[ParsedRow[RowT]] = []
        for raw in reader:
            result.rows_received += 1
            row_number = reader.line_num
            if EXTRA_COLUMNS_KEY in raw or None in raw.values():
                result.reject(row_number, raw, "WRONG_COLUMN_COUNT", f"expected {len(headers)} columns")
                continue
            try:
                row = self.row_model.model_validate(raw)
            except ValidationError as ex:
                result.reject(row_number, raw, "VALIDATION_ERROR", _format_validation_error(ex))
                continue
            parsed.append(ParsedRow(row_number=row_number, raw=raw, row=row))  # type: ignore[arg-type]

        unique_rows = self._drop_in_file_duplicates(parsed, result)
        return unique_rows, result

    def _drop_in_file_duplicates(self, parsed: list[ParsedRow[RowT]], result: IngestResult) -> list[ParsedRow[RowT]]:
        by_key: dict[str, list[ParsedRow[RowT]]] = defaultdict(list)
        for p in parsed:
            by_key[getattr(p.row, self.key_field)].append(p)

        keep: list[ParsedRow[RowT]] = []
        for key, group in by_key.items():
            first, *rest = group
            if all(p.raw == first.raw for p in rest):
                keep.append(first)
                for p in rest:
                    result.reject(p.row_number, p.raw, "DUPLICATE_ROW", f"identical to row {first.row_number}")
            else:
                rows = ", ".join(str(p.row_number) for p in group)
                for p in group:
                    result.reject(
                        p.row_number,
                        p.raw,
                        "CONFLICTING_DUPLICATE_KEY",
                        f"{self.key_field}={key!r} differs on rows {rows}",
                    )
        return sorted(keep, key=lambda p: p.row_number)

    @abstractmethod
    async def load(
        self, ads: AsyncDataStore, job: IngestionJob, rows: list[ParsedRow[RowT]], result: IngestResult
    ) -> None:
        """Write valid rows (in the caller's transaction) and update result.rows_loaded /
        result.rejected_row_details."""
