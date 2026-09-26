from functools import cached_property

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "DEVELOPMENT"
    SERVICE_NAME: str = "dealeros"

    # --- Postgres ---
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5433
    POSTGRES_USER: str = "dealeros"
    POSTGRES_PASSWORD: SecretStr = SecretStr("dealeros")
    POSTGRES_DB: str = "dealeros"
    DB_SCHEMA: str = "dealeros"
    POOL_SIZE: int = 5
    MAX_OVERFLOW: int = 5
    POOL_RECYCLE_SECONDS: int = 1800

    # --- RabbitMQ ---
    MQ_HOST: str = "localhost"
    MQ_PORT: int = 5672
    MQ_USERNAME: str = "dealeros"
    MQ_PASSWORD: SecretStr = SecretStr("dealeros")
    MQ_VHOST: str = "dealeros"
    PREFETCH_COUNT: int = 1

    # Queues are declared in infra/rabbitmq/definitions.json; names must stay in sync. Messages go
    # straight to queues; the exchanges there only route expired retry messages back to main.
    INGESTION_MAIN_QUEUE: str = "ingestion-main-queue"
    INGESTION_RETRY_QUEUES: list[str] = [
        "ingestion-retry-1-queue",
        "ingestion-retry-2-queue",
        "ingestion-retry-3-queue",
    ]
    INGESTION_DEAD_QUEUE: str = "ingestion-dead-queue"

    RECONCILIATION_MAIN_QUEUE: str = "reconciliation-main-queue"
    RECONCILIATION_RETRY_QUEUES: list[str] = [
        "reconciliation-retry-1-queue",
        "reconciliation-retry-2-queue",
        "reconciliation-retry-3-queue",
    ]
    RECONCILIATION_DEAD_QUEUE: str = "reconciliation-dead-queue"

    # --- Ingestion ---
    # TODO(phase-2): replace local folder with S3 (file_uri becomes s3://bucket/key instead of local://key).
    DATA_DIR: str = "data/incoming"
    MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024

    # --- API ---
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    # TODO(phase-2): store internal clients in the DB (hashed secret, rotation) like tenant_credential.
    INTERNAL_CLIENT_ID: str = "46e3210d-94ec-465b-9566-c60c5779efdc"
    INTERNAL_CLIENT_SECRET: SecretStr = SecretStr("change-me")

    @cached_property
    def SQLALCHEMY_DATABASE_URI(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD.get_secret_value(),
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        )

    @property
    def MQ_URL(self) -> str:
        return (
            f"amqp://{self.MQ_USERNAME}:{self.MQ_PASSWORD.get_secret_value()}"
            f"@{self.MQ_HOST}:{self.MQ_PORT}/{self.MQ_VHOST}"
        )


settings = Settings()
