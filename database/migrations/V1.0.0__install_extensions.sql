-- pg_stat_statements also needs shared_preload_libraries=pg_stat_statements,
-- which the postgres service/deployment passes on its command line.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Home for the monitoring views in R__1000_monitoring_view.sql.
CREATE SCHEMA IF NOT EXISTS performance;
