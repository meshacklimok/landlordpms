from django.core.management.base import BaseCommand, CommandError

from core import dbhealth

MIB = 1024 * 1024


def _size(n: int | None) -> str:
    if n is None:
        return "-"
    return f"{n / (1024 * MIB):.1f} GB" if n >= 1024 * MIB else f"{n / MIB:.1f} MB"


class Command(BaseCommand):
    help = ("Report on the database: connections, storage, cache hits, slowest queries, index use and vacuum. "
            "Fails when `check_jobs` would alert (doc 20).")

    def add_arguments(self, parser):
        parser.add_argument("--top", type=int, default=10, help="How many tables and queries to list.")

    def handle(self, *args, **options):
        r, top, out = dbhealth.report(), options["top"], self.stdout.write
        s = r.settings

        out(self.style.MIGRATE_HEADING("Connection and settings"))
        out(f"  PostgreSQL {s.get('server_version')}; TLS {'on' if s['ssl_in_use'] else 'off'}; "
            f"pool {'on' if s['pool'] else 'off'}; superuser {'YES' if s['superuser'] else 'no'}")
        out(f"  statement_timeout {s.get('statement_timeout')} ms; idle_in_transaction_session_timeout "
            f"{s.get('idle_in_transaction_session_timeout')} ms; application_name {s.get('application_name')}")
        out(f"  migrations not applied: {', '.join(r.unapplied) or 'none'}")

        c = r.connections
        out(self.style.MIGRATE_HEADING(f"Connections: {c['used']} of {c['max']} ({c['percent']}%) on the server"))
        for row in c["by_app"]:
            out(f"  {row['count']:>4}  {row['app']}  ({row['state']})")
        for row in r.long_running:
            out(self.style.WARNING(f"  long: pid {row['pid']} {row['app']} {row['state']} for {row['seconds']}s"))

        out(self.style.MIGRATE_HEADING(f"Storage: database {_size(r.storage['database_bytes'])}"))
        for t in r.storage["tables"][:top]:
            out(f"  {_size(t['bytes']):>10}  (indexes {_size(t['index_bytes'])})  {t['rows']:>9} rows  {t['table']}")
        for d in r.disks:
            out(f"  disk for {d['label']}: {_size(d['free'])} free of {_size(d['total'])} ({d['free_percent']}%)")

        p = r.performance
        hit = "-" if p["cache_hit_percent"] is None else f"{p['cache_hit_percent']}%"
        out(self.style.MIGRATE_HEADING("Performance since the statistics were reset"))
        out(f"  cache hits {hit} (aim for 99%); commits {p['commits']}, rollbacks {p['rollbacks']}, "
            f"deadlocks {p['deadlocks']}, temp files {_size(p['temp_bytes'])}")
        if r.slow_queries is None:
            out("  slowest queries: pg_stat_statements is not installed (doc 20, Monitoring).")
        for q in (r.slow_queries or [])[:top]:
            out(f"  {q['total_ms']:>10.0f} ms total  {q['mean_ms']:>8.2f} ms avg  {q['calls']:>7} calls  {q['query']}")

        out(self.style.MIGRATE_HEADING("Indexes and vacuum"))
        for t in r.unindexed:
            out(f"  mostly full scans: {t['table']} ({t['rows']} rows, {t['seq_scans']} scans, "
                f"{t['index_scans']} index reads)")
        for i in r.unused_indexes:
            out(f"  never used: {i['index']} on {i['table']} ({_size(i['bytes'])})")
        for t in r.vacuum:
            out(f"  dead rows: {t['table']} {t['dead']} dead of {t['live']}, last vacuum {t['last_vacuum'] or 'never'}")
        if not (r.unindexed or r.unused_indexes or r.vacuum):
            out("  nothing to note")

        problems = dbhealth.problems()
        if problems:
            for line in problems:
                out(self.style.ERROR("  " + line))
            raise CommandError(f"{len(problems)} database problem(s).")
        out(self.style.SUCCESS("No database problems."))
