DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dealeros_tenant_reader') THEN
        CREATE ROLE dealeros_tenant_reader NOLOGIN;
    END IF;
END
$$;
GRANT dealeros_tenant_reader TO CURRENT_USER;

GRANT USAGE ON SCHEMA dealeros TO dealeros_tenant_reader;
GRANT SELECT ON dealeros."tenant", dealeros."location", dealeros."event", dealeros."discrepancy"
    TO dealeros_tenant_reader;

ALTER TABLE dealeros."tenant" ENABLE ROW LEVEL SECURITY;
ALTER TABLE dealeros."location" ENABLE ROW LEVEL SECURITY;
ALTER TABLE dealeros."event" ENABLE ROW LEVEL SECURITY;
ALTER TABLE dealeros."discrepancy" ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON dealeros."tenant" FOR SELECT TO dealeros_tenant_reader
    USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY tenant_isolation ON dealeros."location" FOR SELECT TO dealeros_tenant_reader
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY tenant_isolation ON dealeros."event" FOR SELECT TO dealeros_tenant_reader
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY tenant_isolation ON dealeros."discrepancy" FOR SELECT TO dealeros_tenant_reader
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
