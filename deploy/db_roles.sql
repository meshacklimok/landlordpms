-- landlordpms database roles (D-105, doc 20 §18-19). Run once as a superuser:
--   psql -v owner_pw="'...'" -v app_pw="'...'" -f deploy/db_roles.sql
--
-- landlordpms_owner  owns the tables and runs `migrate`. Nothing else uses it.
-- landlordpms        the web workers and cron. Reads and writes rows; cannot create, alter or drop tables,
--                    so a bug or an injected query cannot change the schema. CREATEDB is only for the
--                    monthly restore test, which restores into its own scratch database.
-- The ledger, deposit, follow-up and audit tables also refuse UPDATE and DELETE through triggers
-- (billing 0008, audit 0002), whoever connects.

CREATE ROLE landlordpms_owner LOGIN PASSWORD :owner_pw;
CREATE ROLE landlordpms LOGIN CREATEDB PASSWORD :app_pw;

CREATE DATABASE landlordpms OWNER landlordpms_owner;
\connect landlordpms

REVOKE ALL ON DATABASE landlordpms FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE landlordpms TO landlordpms;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO landlordpms_owner;
GRANT USAGE ON SCHEMA public TO landlordpms;

-- Tables and sequences the owner creates from now on.
ALTER DEFAULT PRIVILEGES FOR ROLE landlordpms_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO landlordpms;
ALTER DEFAULT PRIVILEGES FOR ROLE landlordpms_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO landlordpms;

-- Monitoring (`db_health`): see every session's state and pg_stat_statements, not just its own.
GRANT pg_read_all_stats TO landlordpms;
-- Slowest queries. Also needs shared_preload_libraries = 'pg_stat_statements' in postgresql.conf
-- (most managed PostgreSQL services have it on) and a restart.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Moving an existing install, where the app user owns everything, to these roles. Run in the database
-- after creating landlordpms_owner, then grant on the tables that already exist:
--   REASSIGN OWNED BY landlordpms TO landlordpms_owner;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO landlordpms;
--   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO landlordpms;
