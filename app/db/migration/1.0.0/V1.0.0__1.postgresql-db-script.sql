CREATE SCHEMA IF NOT EXISTS dealeros;

-- tenancy
CREATE TABLE IF NOT EXISTS dealeros."tenant" (
    id                  uuid                        NOT NULL,
    external_org_id     varchar(64)                 NOT NULL,
    name                varchar(255),
    status              varchar(32)                 NOT NULL,
    created_at          timestamp with time zone    NOT NULL DEFAULT now(),
    updated_at          timestamp with time zone    NOT NULL DEFAULT now(),
    created_by          varchar(100)                NOT NULL,
    updated_by          varchar(100)                NOT NULL,
    source              varchar(100),
    CONSTRAINT pk_tenant_id PRIMARY KEY (id),
    CONSTRAINT uq_tenant_external_org_id UNIQUE (external_org_id),
    CONSTRAINT ck_tenant_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);

CREATE TABLE IF NOT EXISTS dealeros."location" (
    id                      uuid                        NOT NULL,
    tenant_id               uuid                        NOT NULL,
    external_location_id    varchar(64)                 NOT NULL,
    location_name           varchar(255),
    status                  varchar(32)                 NOT NULL,
    created_at              timestamp with time zone    NOT NULL DEFAULT now(),
    updated_at              timestamp with time zone    NOT NULL DEFAULT now(),
    created_by              varchar(100)                NOT NULL,
    updated_by              varchar(100)                NOT NULL,
    source                  varchar(100),
    CONSTRAINT pk_location_id PRIMARY KEY (id),
    CONSTRAINT fk_location_tenant_id FOREIGN KEY (tenant_id) REFERENCES dealeros."tenant" (id),
    CONSTRAINT uq_location_external_location_id UNIQUE (external_location_id),
    CONSTRAINT ck_location_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);
CREATE INDEX IF NOT EXISTS idx_location_tenant_id ON dealeros."location" (tenant_id);


CREATE TABLE IF NOT EXISTS dealeros."tenant_credential" (
    id                  uuid                        NOT NULL,
    tenant_id           uuid                        NOT NULL,
    client_id           uuid                        NOT NULL,
    client_secret_hash  varchar(255)                NOT NULL,
    environment         varchar(32)                 NOT NULL,
    status              varchar(32)                 NOT NULL,
    expires_at          timestamp with time zone,
    revoked_at          timestamp with time zone,
    created_at          timestamp with time zone    NOT NULL DEFAULT now(),
    updated_at          timestamp with time zone    NOT NULL DEFAULT now(),
    created_by          varchar(100)                NOT NULL,
    updated_by          varchar(100)                NOT NULL,
    source              varchar(100),
    CONSTRAINT pk_tenant_credential_id PRIMARY KEY (id),
    CONSTRAINT fk_tenant_credential_tenant_id FOREIGN KEY (tenant_id) REFERENCES dealeros."tenant" (id),
    CONSTRAINT uq_tenant_credential_client_id UNIQUE (client_id),
    CONSTRAINT ck_tenant_credential_environment CHECK (environment IN ('PRODUCTION', 'STAGING', 'SANDBOX')),
    CONSTRAINT ck_tenant_credential_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
);
CREATE INDEX IF NOT EXISTS idx_tenant_credential_tenant_id ON dealeros."tenant_credential" (tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_credential_active_environment
    ON dealeros."tenant_credential" (tenant_id, environment) WHERE status = 'ACTIVE';


-- ingestion
CREATE TABLE IF NOT EXISTS dealeros."ingestion_job" (
    id                      uuid                        NOT NULL,
    dataset_type            varchar(32)                 NOT NULL,
    trigger_type            varchar(32)                 NOT NULL,
    triggered_by            varchar(100)                NOT NULL,
    file_name               varchar(255)                NOT NULL,
    file_uri                varchar(1024)               NOT NULL,
    file_hash               varchar(64)                 NOT NULL,
    file_size_bytes         bigint,
    status                  varchar(32)                 NOT NULL,
    duplicate_of_job_id     uuid,
    rows_received           integer,
    rows_loaded             integer,
    rows_rejected           integer,
    rejected_row_details    jsonb,
    error_message           text,
    started_at              timestamp with time zone,
    finished_at             timestamp with time zone,
    created_at              timestamp with time zone    NOT NULL DEFAULT now(),
    updated_at              timestamp with time zone    NOT NULL DEFAULT now(),
    created_by              varchar(100)                NOT NULL,
    updated_by              varchar(100)                NOT NULL,
    source                  varchar(100),
    CONSTRAINT pk_ingestion_job_id PRIMARY KEY (id),
    CONSTRAINT fk_ingestion_job_duplicate_of_job_id FOREIGN KEY (duplicate_of_job_id) REFERENCES dealeros."ingestion_job" (id),
    CONSTRAINT ck_ingestion_job_dataset_type CHECK (dataset_type IN ('LOCATIONS', 'SYSTEM_A_EVENTS', 'SYSTEM_B_ENTRIES')),
    CONSTRAINT ck_ingestion_job_trigger_type CHECK (trigger_type IN ('MANUAL', 'S3_EVENT', 'SCHEDULED')),
    CONSTRAINT ck_ingestion_job_status CHECK (
        status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIALLY_SUCCEEDED', 'FAILED', 'SKIPPED_DUPLICATE')
    )
);
CREATE INDEX IF NOT EXISTS idx_ingestion_job_file_hash ON dealeros."ingestion_job" (file_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_job_dataset_type_file_hash_loaded
    ON dealeros."ingestion_job" (dataset_type, file_hash) WHERE status IN ('SUCCEEDED', 'PARTIALLY_SUCCEEDED');


-- events (all source systems) and reconciliation output
CREATE TABLE IF NOT EXISTS dealeros."event" (
    id                  uuid                        NOT NULL,
    source_system       varchar(32)                 NOT NULL,
    external_event_id   varchar(128)                NOT NULL,
    match_key           varchar(128),
    match_key_method    varchar(32)                 NOT NULL,
    location_id         uuid                        NOT NULL,
    tenant_id           uuid,
    event_date          date,
    amount              numeric(14, 2),
    status              varchar(32),
    attributes          jsonb                       NOT NULL,
    raw_row             jsonb                       NOT NULL,
    ingestion_job_id    uuid                        NOT NULL,
    source_row_number   integer                     NOT NULL,
    created_at          timestamp with time zone    NOT NULL DEFAULT now(),
    updated_at          timestamp with time zone    NOT NULL DEFAULT now(),
    created_by          varchar(100)                NOT NULL,
    updated_by          varchar(100)                NOT NULL,
    source              varchar(100),
    CONSTRAINT pk_event_id PRIMARY KEY (id),
    CONSTRAINT fk_event_location_id FOREIGN KEY (location_id) REFERENCES dealeros."location" (id),
    CONSTRAINT fk_event_tenant_id FOREIGN KEY (tenant_id) REFERENCES dealeros."tenant" (id),
    CONSTRAINT fk_event_ingestion_job_id FOREIGN KEY (ingestion_job_id) REFERENCES dealeros."ingestion_job" (id),
    CONSTRAINT uq_event_source_system_external_event_id UNIQUE (source_system, external_event_id),
    CONSTRAINT ck_event_source_system CHECK (source_system IN ('SYSTEM_A', 'SYSTEM_B')),
    CONSTRAINT ck_event_match_key_method CHECK (match_key_method IN ('EXACT', 'NORMALIZED', 'UNPARSABLE'))
);
CREATE INDEX IF NOT EXISTS idx_event_ingestion_job_id ON dealeros."event" (ingestion_job_id);
CREATE INDEX IF NOT EXISTS idx_event_location_id ON dealeros."event" (location_id);
CREATE INDEX IF NOT EXISTS idx_event_match_key ON dealeros."event" (match_key);
CREATE INDEX IF NOT EXISTS idx_event_tenant_id_event_date ON dealeros."event" (tenant_id, event_date);
CREATE INDEX IF NOT EXISTS idx_event_tenant_id_match_key ON dealeros."event" (tenant_id, match_key);


CREATE TABLE IF NOT EXISTS dealeros."discrepancy" (
    id                  uuid                        NOT NULL,
    tenant_id           uuid,                                   -- NULL = tenant conflict, hidden
    match_key           varchar(128)                NOT NULL,
    type                varchar(32)                 NOT NULL,
    field               varchar(32)                 NOT NULL,
    event_ids           jsonb                       NOT NULL,
    values_by_system    jsonb                       NOT NULL,
    details             jsonb                       NOT NULL,
    status              varchar(32)                 NOT NULL,
    first_detected_at   timestamp with time zone    NOT NULL,
    last_detected_at    timestamp with time zone    NOT NULL,
    resolved_at         timestamp with time zone,
    created_at          timestamp with time zone    NOT NULL DEFAULT now(),
    updated_at          timestamp with time zone    NOT NULL DEFAULT now(),
    created_by          varchar(100)                NOT NULL,
    updated_by          varchar(100)                NOT NULL,
    source              varchar(100),
    CONSTRAINT pk_discrepancy_id PRIMARY KEY (id),
    CONSTRAINT fk_discrepancy_tenant_id FOREIGN KEY (tenant_id) REFERENCES dealeros."tenant" (id),
    CONSTRAINT uq_discrepancy_match_key_type_field UNIQUE (match_key, type, field),
    CONSTRAINT ck_discrepancy_type CHECK (type IN ('DUPLICATE_IN_A', 'MISSING_IN_B', 'ORPHAN_IN_B', 'DUPLICATE_IN_B',
        'VALUE_MISMATCH', 'MISSING_VALUE', 'DATE_MISMATCH', 'LOCATION_MISMATCH', 'TENANT_CONFLICT', 'STATUS_MISMATCH')),
    CONSTRAINT ck_discrepancy_field CHECK (field IN ('RECORD', 'AMOUNT', 'EVENT_DATE', 'LOCATION', 'STATUS')),
    CONSTRAINT ck_discrepancy_status CHECK (status IN ('OPEN', 'RESOLVED'))
);
CREATE INDEX IF NOT EXISTS idx_discrepancy_tenant_id_status_type ON dealeros."discrepancy" (tenant_id, status, type);
