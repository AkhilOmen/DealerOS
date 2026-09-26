from app.db.enums import DatasetType
from app.ingestion.ingestors.base import BaseIngestor
from app.ingestion.ingestors.events import SystemAIngestor, SystemBIngestor
from app.ingestion.ingestors.locations import LocationsIngestor

_INGESTORS: dict[DatasetType, BaseIngestor] = {
    ingestor.dataset_type: ingestor for ingestor in (
        LocationsIngestor(), SystemAIngestor(), SystemBIngestor()
    )
}


def get_ingestor(dataset_type: DatasetType) -> BaseIngestor:
    return _INGESTORS[dataset_type]
