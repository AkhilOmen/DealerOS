import uuid
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.session import AsyncDataStore
from app.utils.error import MissingTenantError, UniqueKeyViolationError

UNIQUE_VIOLATION_SQLSTATE = "23505"


async def commit_or_raise(ads: AsyncDataStore) -> None:
    try:
        await ads.db.commit()
    except IntegrityError as ex:
        await ads.db.rollback()
        if getattr(ex.orig, "sqlstate", None) == UNIQUE_VIOLATION_SQLSTATE:
            raise UniqueKeyViolationError(str(ex.orig)) from ex
        raise


class AsyncBaseRepository[ModelType: Base]:
    def __init__(self, model: type[ModelType]):
        self.model = model

    async def get_by_id(self, ads: AsyncDataStore, id: uuid.UUID) -> ModelType | None:
        return await ads.db.get(self.model, id)

    async def get_first_by(self, ads: AsyncDataStore, **filters: Any) -> ModelType | None:
        result = await ads.db.execute(select(self.model).filter_by(**filters).limit(1))
        return result.scalars().first()

    async def create(self, ads: AsyncDataStore, obj: ModelType) -> ModelType:
        ads.db.add(obj)
        await commit_or_raise(ads)
        return obj

    async def update(self, ads: AsyncDataStore, obj: ModelType, **values: Any) -> ModelType:
        for key, value in values.items():
            setattr(obj, key, value)
        await commit_or_raise(ads)
        return obj


class AsyncBaseTenantRepository[ModelType: Base]:
    def __init__(self, model: type[ModelType]):
        if not hasattr(model, "tenant_id"):
            raise TypeError(f"{model.__name__} has no tenant_id column")
        self.model = model

    def _tenant_filter(self, tenant_id: uuid.UUID) -> ColumnElement[bool]:
        if tenant_id is None:
            raise MissingTenantError(f"tenant_id is required to read {self.model.__name__}")
        return self.model.tenant_id == tenant_id  # type: ignore[attr-defined]

    async def get_by_id(self, ads: AsyncDataStore, tenant_id: uuid.UUID, id: uuid.UUID) -> ModelType | None:
        result = await ads.db.execute(
            select(self.model)
            .where(self._tenant_filter(tenant_id), self.model.id == id)  # type: ignore[attr-defined]
        )
        return result.scalars().first()

    async def list(
        self, ads: AsyncDataStore, tenant_id: uuid.UUID, *where: ColumnElement[bool], limit: int = 100
    ) -> list[ModelType]:
        result = await ads.db.execute(
            select(self.model)
            .where(self._tenant_filter(tenant_id), *where)
            .limit(limit)
        )
        return list(result.scalars())
