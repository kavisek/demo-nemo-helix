"""PostgreSQL monitoring dashboard for the database behind Grafana's data source.

Every query reads Postgres' live statistics views (pg_stat_activity,
pg_stat_database, pg_stat_statements, ...), so panels show the current state
rather than history and the dashboard refreshes every 30 seconds.
"""

from grafanalib.core import Dashboard, GridPos, RowPanel, Stat, Table, Threshold, Time

from .common import POSTGRES, sql

# -- Overview ---------------------------------------------------------------

UPTIME = "SELECT extract(epoch FROM now() - pg_postmaster_start_time()) AS uptime;"

CONNECTIONS = "SELECT count(*) AS connections FROM pg_stat_activity WHERE backend_type = 'client backend';"

CONNECTION_USAGE = """
SELECT 100.0 * count(*) / current_setting('max_connections')::int AS usage
FROM pg_stat_activity
WHERE backend_type = 'client backend';
"""

DATABASE_SIZE = "SELECT pg_database_size(current_database()) AS size;"

CACHE_HIT_RATIO = """
SELECT 100.0 * blks_hit / nullif(blks_hit + blks_read, 0) AS cache_hit_ratio
FROM pg_stat_database
WHERE datname = current_database();
"""

DEADLOCKS = "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database();"

# -- Activity ---------------------------------------------------------------

CONNECTIONS_BY_STATE = """
SELECT coalesce(state, 'background') AS state,
       datname AS database_name,
       usename AS username,
       application_name,
       count(*) AS connections
FROM pg_stat_activity
GROUP BY 1, 2, 3, 4
ORDER BY connections DESC;
"""

LONG_RUNNING_QUERIES = """
SELECT pid,
       usename AS username,
       datname AS database_name,
       state,
       extract(epoch FROM now() - query_start) AS running_for,
       wait_event_type,
       left(query, 200) AS query
FROM pg_stat_activity
WHERE state <> 'idle'
  AND backend_type = 'client backend'
  AND pid <> pg_backend_pid()
ORDER BY running_for DESC
LIMIT 20;
"""

# Same logic as performance.latest_locked_queries, inlined because that view
# isn't guaranteed to exist.
BLOCKED_QUERIES = """
SELECT pid,
       usename AS username,
       pg_blocking_pids(pid)::text AS blocked_by,
       datname AS database_name,
       extract(epoch FROM now() - query_start) AS waiting_for,
       left(query, 200) AS blocked_query
FROM pg_stat_activity
WHERE cardinality(pg_blocking_pids(pid)) > 0
ORDER BY waiting_for DESC;
"""

# -- Query performance ------------------------------------------------------

TOP_QUERIES = """
SELECT left(query, 200) AS query,
       calls,
       total_exec_time / 1000 AS total_time,
       mean_exec_time / 1000 AS mean_time,
       rows
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC
LIMIT 20;
"""

# -- Storage ----------------------------------------------------------------

TABLE_SIZES = """
SELECT schemaname AS schema_name,
       relname AS table_name,
       n_live_tup AS live_rows,
       n_dead_tup AS dead_rows,
       pg_total_relation_size(relid) AS total_size,
       pg_indexes_size(relid) AS index_size,
       last_autovacuum,
       last_autoanalyze
FROM pg_stat_user_tables
ORDER BY total_size DESC;
"""

INDEX_USAGE = """
SELECT schemaname AS schema_name,
       relname AS table_name,
       indexrelname AS index_name,
       idx_scan AS scans,
       pg_relation_size(indexrelid) AS size
FROM pg_stat_user_indexes
ORDER BY scans ASC, size DESC;
"""


def stat(title, query, unit, x, thresholds=None):
    return Stat(
        title=title,
        dataSource=POSTGRES,
        targets=[sql(query)],
        format=unit,
        reduceCalc="lastNotNull",
        graphMode="none",
        thresholds=thresholds or [Threshold("green", 0, 0.0)],
        gridPos=GridPos(h=4, w=4, x=x, y=1),
    )


def table(title, query, h, w, x, y, unit="none", description=None):
    return Table(
        title=title,
        description=description,
        dataSource=POSTGRES,
        targets=[sql(query)],
        unit=unit,
        gridPos=GridPos(h=h, w=w, x=x, y=y),
    )


dashboard = Dashboard(
    title="PostgreSQL Monitoring",
    uid="postgres_monitoring",
    description="Health of the PostgreSQL database used by Grafana's PostgreSQL data source.",
    tags=["postgres", "monitoring"],
    refresh="30s",
    time=Time("now-1h", "now"),
    panels=[
        RowPanel(title="Overview", gridPos=GridPos(h=1, w=24, x=0, y=0)),
        stat("Uptime", UPTIME, "s", x=0),
        stat("Client Connections", CONNECTIONS, "none", x=4),
        stat(
            "Connection Usage",
            CONNECTION_USAGE,
            "percent",
            x=8,
            thresholds=[Threshold("green", 0, 0.0), Threshold("orange", 1, 70.0), Threshold("red", 2, 90.0)],
        ),
        stat("Database Size", DATABASE_SIZE, "bytes", x=12),
        stat(
            "Cache Hit Ratio",
            CACHE_HIT_RATIO,
            "percent",
            x=16,
            thresholds=[Threshold("red", 0, 0.0), Threshold("orange", 1, 90.0), Threshold("green", 2, 99.0)],
        ),
        stat(
            "Deadlocks",
            DEADLOCKS,
            "none",
            x=20,
            thresholds=[Threshold("green", 0, 0.0), Threshold("red", 1, 1.0)],
        ),
        RowPanel(title="Activity", gridPos=GridPos(h=1, w=24, x=0, y=5)),
        table("Connections by State", CONNECTIONS_BY_STATE, h=8, w=10, x=0, y=6),
        table(
            "Long-Running Queries",
            LONG_RUNNING_QUERIES,
            h=8, w=14, x=10, y=6,
            description="Non-idle client queries, longest first. running_for is in seconds.",
        ),
        table(
            "Blocked Queries",
            BLOCKED_QUERIES,
            h=6, w=24, x=0, y=14,
            description="Queries waiting on a lock and the pids blocking them. waiting_for is in seconds.",
        ),
        RowPanel(title="Query Performance", gridPos=GridPos(h=1, w=24, x=0, y=20)),
        table(
            "Top Queries by Total Time",
            TOP_QUERIES,
            h=10, w=24, x=0, y=21,
            description="From pg_stat_statements. total_time and mean_time are in seconds.",
        ),
        RowPanel(title="Storage", gridPos=GridPos(h=1, w=24, x=0, y=31)),
        table(
            "Table Sizes and Vacuum",
            TABLE_SIZES,
            h=9, w=14, x=0, y=32,
            description="High dead_rows means autovacuum is falling behind. Sizes are in bytes.",
        ),
        table(
            "Index Usage",
            INDEX_USAGE,
            h=9, w=10, x=14, y=32,
            description="Indexes with zero scans are candidates for removal. Size is in bytes.",
        ),
    ],
).auto_panel_ids()
