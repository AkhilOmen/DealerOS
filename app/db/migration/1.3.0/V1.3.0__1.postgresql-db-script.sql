DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dealeros_api') THEN
        CREATE ROLE dealeros_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '${api_db_password}';
    ELSE
        ALTER ROLE dealeros_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '${api_db_password}';
    END IF;
END
$$;

GRANT dealeros_tenant_reader TO dealeros_api;

GRANT USAGE ON SCHEMA dealeros TO dealeros_api;

GRANT SELECT, INSERT, UPDATE ON dealeros."ingestion_job", dealeros."query_log", dealeros."tenant_credential"
    TO dealeros_api;

GRANT SELECT ON dealeros."tenant" TO dealeros_api;
CREATE POLICY api_reads_org_list ON dealeros."tenant" FOR SELECT TO dealeros_api USING (true);
