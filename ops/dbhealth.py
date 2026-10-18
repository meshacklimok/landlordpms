"""Database monitoring: connections, storage, performance and index use (doc 20 §22-24, §28).

`report()` is for `manage.py db_health`; `problems()` feeds the `check_jobs` alerts. Query text is only ever taken
from pg_stat_statements, which replaces values with $1, never from pg_stat_activity: Django binds parameters on
the client, so the text there can hold tenants' names and phone numbers.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor

from . import backup

# Alert thresholds. Each can be overridden in settings.
CONNECTIONS_PERCENT = 80  # of the server's max_connections
LONG_QUERY_SECONDS = 300  # a statement still running, or a transaction left open, this long
DISK_FREE_PERCENT = 10  # on the disks holding the backups and the uploaded files
DISK_FREE_GB = 2
# Report-only hints.
BIG_TABLE_ROWS = 10_000
MIB = 1024 * 1024


def _limit(name: str, default):
    return getattr(settings, f"DB_ALERT_{name}", default)


def _rows(sql: str, params=()) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def _one(sql: str, params=()):
    rows = _rows(sql, params)
    return rows[0][0] if rows else None


@dataclass
class Report:
    connections: dict = field(default_factory=dict)
    long_running: list = field(default_factory=list)
    storage: dict = field(default_factory=dict)
    disks: list = field(default_factory=list)
    performance: dict = field(default_factory=dict)
    slow_queries: list | None = None  # None: pg_stat_statements is not installed
    unindexed: list = field(default_factory=list)
    unused_indexes: list = field(default_factory=list)
    vacuum: list = field(default_factory=list)
    unapplied: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)


# --- connections (§24) ------------------------------------------------------------------------------------------

def connection_stats() -> dict:
    used, limit = _rows("SELECT (SELECT count(*) FROM pg_stat_activity), current_setting('max_connections')::int")[0]
    ours = _rows("""SELECT coalesce(nullif(application_name, ''), '(none)'), coalesce(state, '(other)'), count(*)
                    FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()
                    GROUP BY 1, 2 ORDER BY 3 DESC""")
    return {"used": used, "max": limit, "percent": round(100 * used / limit, 1) if limit else 0,
            "by_app": [{"app": app, "state": state, "count": n} for app, state, n in ours]}


def long_running(seconds: int | None = None) -> list[dict]:
    """Statements running, or transactions open, longer than `seconds`. Who and how long only, never the text."""
    seconds = seconds or _limit("LONG_QUERY_SECONDS", LONG_QUERY_SECONDS)
    rows = _rows("""SELECT pid, coalesce(nullif(application_name, ''), '(none)'), state,
                           extract(epoch FROM now() - coalesce(xact_start, query_start))::int
                    FROM pg_stat_activity
                    WHERE datname = current_database() AND pid <> pg_backend_pid()
                      AND state IN ('active', 'idle in transaction', 'idle in transaction (aborted)')
                      AND now() - coalesce(xact_start, query_start) > make_interval(secs => %s)
                    ORDER BY 4 DESC""", [seconds])
    return [{"pid": pid, "app": app, "state": state, "seconds": secs} for pid, app, state, secs in rows]


# --- storage (§23) ----------------------------------------------------------------------------------------------

def storage_stats(top: int = 10) -> dict:
    size = _one("SELECT pg_database_size(current_database())")
    tables = _rows("""SELECT relname, n_live_tup, pg_total_relation_size(relid), pg_indexes_size(relid)
                      FROM pg_stat_user_tables ORDER BY 3 DESC LIMIT %s""", [top])
    return {"database_bytes": size,
            "tables": [{"table": t, "rows": n, "bytes": b, "index_bytes": i} for t, n, b, i in tables]}


def disk_stats() -> list[dict]:
    """Free space where backups and uploads are written. A database on another server is watched by its host."""
    out, seen = [], set()
    for label, folder in (("backups", backup.backup_dir()), ("uploads", Path(settings.MEDIA_ROOT))):
        probe = folder
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            usage = shutil.disk_usage(probe)
        except OSError:
            continue
        key = (usage.total, usage.free)
        if key in seen:  # the same disk
            out[-1]["label"] += f" + {label}"
            continue
        seen.add(key)
        out.append({"label": label, "path": str(folder), "total": usage.total, "free": usage.free,
                    "free_percent": round(100 * usage.free / usage.total, 1) if usage.total else 0})
    return out


# --- performance (§12, §22) -------------------------------------------------------------------------------------

def performance_stats() -> dict:
    hit, read, commits, rollbacks, deadlocks, temp = _rows(
        """SELECT blks_hit, blks_read, xact_commit, xact_rollback, deadlocks, temp_bytes
           FROM pg_stat_database WHERE datname = current_database()""")[0]
    return {"cache_hit_percent": round(100 * hit / (hit + read), 2) if hit + read else None,
            "commits": commits, "rollbacks": rollbacks, "deadlocks": deadlocks, "temp_bytes": temp}


def has_pg_stat_statements() -> bool:
    return bool(_one("SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"))


def slow_queries(top: int = 10) -> list[dict] | None:
    """The statements that took the most time in total, with values replaced by $1. None if not installed."""
    if not has_pg_stat_statements():
        return None
    try:
        with transaction.atomic():
            rows = _rows("""SELECT calls, round(total_exec_time::numeric, 1), round(mean_exec_time::numeric, 2),
                                   rows, left(regexp_replace(query, '\\s+', ' ', 'g'), 300)
                            FROM pg_stat_statements
                            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                            ORDER BY total_exec_time DESC LIMIT %s""", [top])
    except DatabaseError:  # installed in the database but not loaded by the server (shared_preload_libraries)
        return None
    return [{"calls": c, "total_ms": float(t), "mean_ms": float(m), "rows": r, "query": q} for c, t, m, r, q in rows]


def unindexed_tables() -> list[dict]:
    """Big tables read mostly by scanning every row: a filter there may want an index."""
    rows = _rows("""SELECT relname, seq_scan, coalesce(idx_scan, 0), n_live_tup FROM pg_stat_user_tables
                    WHERE n_live_tup >= %s AND seq_scan > coalesce(idx_scan, 0) ORDER BY seq_scan DESC""",
                 [BIG_TABLE_ROWS])
    return [{"table": t, "seq_scans": s, "index_scans": i, "rows": n} for t, s, i, n in rows]


def unused_indexes(min_bytes: int = MIB) -> list[dict]:
    """Indexes never used since the statistics were reset, that enforce nothing. Each one slows every write."""
    rows = _rows("""SELECT s.relname, s.indexrelname, pg_relation_size(s.indexrelid)
                    FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid = s.indexrelid
                    WHERE s.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary
                      AND pg_relation_size(s.indexrelid) >= %s
                    ORDER BY 3 DESC""", [min_bytes])
    return [{"table": t, "index": i, "bytes": b} for t, i, b in rows]


def vacuum_stats() -> list[dict]:
    """Tables where over a fifth of the rows are dead: autovacuum is behind."""
    rows = _rows("""SELECT relname, n_live_tup, n_dead_tup, greatest(last_autovacuum, last_vacuum)
                    FROM pg_stat_user_tables WHERE n_dead_tup > %s AND n_dead_tup > n_live_tup / 5
                    ORDER BY n_dead_tup DESC""", [BIG_TABLE_ROWS])
    return [{"table": t, "live": live, "dead": dead, "last_vacuum": at} for t, live, dead, at in rows]


# --- configuration (§2, §4, §18) --------------------------------------------------------------------------------

def unapplied_migrations() -> list[str]:
    executor = MigrationExecutor(connections[DEFAULT_DB_ALIAS])
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{migration.app_label}.{migration.name}" for migration, _backwards in plan]


def session_settings() -> dict:
    names = ["server_version", "statement_timeout", "idle_in_transaction_session_timeout", "ssl", "application_name"]
    values = dict(_rows("""SELECT name, setting FROM pg_settings WHERE name = ANY(%s)""", [names]))
    values["ssl_in_use"] = bool(_one("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"))
    values["superuser"] = bool(_one("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))
    values["pool"] = bool(connection.settings_dict.get("OPTIONS", {}).get("pool"))
    return values


def report() -> Report:
    return Report(connections=connection_stats(), long_running=long_running(), storage=storage_stats(),
                  disks=disk_stats(), performance=performance_stats(), slow_queries=slow_queries(),
                  unindexed=unindexed_tables(), unused_indexes=unused_indexes(), vacuum=vacuum_stats(),
                  unapplied=unapplied_migrations(), settings=session_settings())


def problems() -> list[str]:
    """What an operator must hear about now. Empty when the database cannot be reached: the health check says so."""
    try:
        with transaction.atomic():
            return _problems()
    except DatabaseError:  # an unreachable database is reported by health.database()
        return []


def _problems() -> list[str]:
    lines = []
    conns = connection_stats()
    if conns["percent"] >= _limit("CONNECTIONS_PERCENT", CONNECTIONS_PERCENT):
        lines.append(f"database connections at {conns['percent']}% ({conns['used']} of {conns['max']})")
    for row in long_running():
        lines.append(f"database: {row['app']} has been {row['state']} for {row['seconds'] // 60} minutes "
                     f"(pid {row['pid']})")
    free_percent, free_gb = _limit("DISK_FREE_PERCENT", DISK_FREE_PERCENT), _limit("DISK_FREE_GB", DISK_FREE_GB)
    for disk in disk_stats():
        if disk["free_percent"] < free_percent or disk["free"] < free_gb * 1024 * MIB:
            lines.append(f"disk for {disk['label']} is low: {disk['free'] // (1024 * MIB)} GB "
                         f"({disk['free_percent']}%) free at {disk['path']}")
    size_limit = _limit("SIZE_MB", 0)
    if size_limit and (size := storage_stats(top=0)["database_bytes"]) > size_limit * MIB:
        lines.append(f"database is {size // MIB} MB, over the {size_limit} MB set in DB_ALERT_SIZE_MB")
    if pending := unapplied_migrations():
        lines.append(f"{len(pending)} migration(s) not applied, first {pending[0]}")
    return lines
