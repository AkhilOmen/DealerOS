"""Manual trigger without the API:

    uv run python -m app.cli ingest --dataset-type LOCATIONS path/to/locations.csv

Stores the file, creates the job and publishes it, exactly like POST /v1/ingestion-jobs.
"""

import argparse
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.enums import DatasetType, TriggerType
from app.db.session import AsyncDataStore, AsyncSessionLocal, async_engine
from app.ingestion import service
from app.messaging.connection import connect
from app.messaging.publisher import Publisher
from app.schemas.messages import ReconciliationMessage

CLI_PRINCIPAL = "system:cli"


async def _iter_file(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            yield chunk


async def ingest(dataset_type: DatasetType, path: Path) -> None:
    connection = await connect()
    publisher = Publisher(connection)
    ads = AsyncDataStore(db=AsyncSessionLocal())
    try:
        job = await service.submit_job(
            ads,
            publisher,
            dataset_type=dataset_type,
            file_name=path.name,
            chunks=_iter_file(path),
            trigger_type=TriggerType.MANUAL,
            triggered_by=CLI_PRINCIPAL,
        )
        print(f"job {job.id} {job.status.value}")
    finally:
        await ads.close()
        await publisher.close()
        await connection.close()
        await async_engine.dispose()


async def reconcile() -> None:
    connection = await connect()
    publisher = Publisher(connection)
    try:
        await publisher.publish(settings.RECONCILIATION_MAIN_QUEUE, ReconciliationMessage())
        print("reconciliation queued")
    finally:
        await publisher.close()
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest_cmd = sub.add_parser("ingest", help="queue a CSV for ingestion")
    ingest_cmd.add_argument("--dataset-type", required=True, choices=[d.value for d in DatasetType])
    ingest_cmd.add_argument("path", type=Path)
    sub.add_parser("reconcile", help="queue a reconciliation run (normally triggered automatically)")
    args = parser.parse_args()

    setup_logging()
    if args.command == "ingest":
        asyncio.run(ingest(DatasetType(args.dataset_type), args.path))
    elif args.command == "reconcile":
        asyncio.run(reconcile())


if __name__ == "__main__":
    main()
