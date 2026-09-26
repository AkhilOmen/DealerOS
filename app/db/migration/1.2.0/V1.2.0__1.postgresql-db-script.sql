CREATE TABLE IF NOT EXISTS dealeros."query_log" (
    id                  uuid                        NOT NULL,
    question            text                        NOT NULL,
    external_org_id     varchar(64),
    tenant_id           uuid,
    status              varchar(32)                 NOT NULL,
    error_message       text,
    plan                jsonb,
    row_count           integer,
    latency_ms          integer                     NOT NULL,
    model               varchar(100),
    created_at          timestamp with time zone    NOT NULL DEFAULT now(),
    updated_at          timestamp with time zone    NOT NULL DEFAULT now(),
    created_by          varchar(100)                NOT NULL,
    updated_by          varchar(100)                NOT NULL,
    source              varchar(100),
    CONSTRAINT pk_query_log_id PRIMARY KEY (id),
    CONSTRAINT fk_query_log_tenant_id FOREIGN KEY (tenant_id) REFERENCES dealeros."tenant" (id),
    CONSTRAINT ck_query_log_status CHECK (status IN ('ANSWERED', 'REJECTED', 'UNSUPPORTED', 'FAILED'))
);
CREATE INDEX IF NOT EXISTS idx_query_log_tenant_id ON dealeros."query_log" (tenant_id);
