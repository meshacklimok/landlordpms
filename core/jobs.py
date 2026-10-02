"""Scheduled jobs: each run is recorded, and a late or failed job raises an alert (D-062 items 1-2)."""

import datetime
import traceback
from contextlib import contextmanager

from django.core.management.base import BaseCommand
from django.utils import timezone

from .models import JobRun

# How often each job must succeed. The cron table in doc 18 runs them more often than this.
JOBS: dict[str, datetime.timedelta] = {
    "send_due_messages": datetime.timedelta(minutes=30),
    "billing_daily": datetime.timedelta(hours=26),
    "mpesa_daily": datetime.timedelta(hours=26),
    "subscriptions_daily": datetime.timedelta(hours=26),
    "backup": datetime.timedelta(hours=26),
    "purge_otp_codes": datetime.timedelta(days=8),
    "purge_import_previews": datetime.timedelta(days=8),
}
KEEP_RUNS = datetime.timedelta(days=90)


@contextmanager
def track(name: str):
    """Records a run of `name`. A failure is recorded, then raised again so cron sees it."""
    run = JobRun.objects.create(name=name)
    try:
        yield run
    except BaseException:
        run.ok = False
        run.error = traceback.format_exc()[-5000:]
        run.finished_at = timezone.now()
        run.save()
        raise
    run.ok = True
    run.finished_at = timezone.now()
    run.save()


class JobCommand(BaseCommand):
    """A management command whose runs are recorded. Subclasses implement `run()` and return a summary."""

    job_name = ""

    def run(self, *args, **options) -> str:
        raise NotImplementedError

    def handle(self, *args, **options):
        name = self.job_name or self.__module__.rsplit(".", 1)[-1]
        with track(name) as run:
            summary = self.run(*args, **options) or ""
            run.summary = summary[:300]
        self.stdout.write(self.style.SUCCESS(summary))


def summarize(counts: dict) -> str:
    return ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items())


def last_runs() -> dict[str, tuple[JobRun | None, JobRun | None]]:
    """For each known job: (its latest run, its latest successful run)."""
    result = {}
    for name in JOBS:
        runs = JobRun.objects.filter(name=name)
        result[name] = (runs.first(), runs.filter(ok=True).first())
    return result


def problems(now: datetime.datetime | None = None) -> list[str]:
    """Known jobs that are late, never ran, or failed last time, as readable lines."""
    now = now or timezone.now()
    lines = []
    for name, (last, last_ok) in last_runs().items():
        if last is None:
            lines.append(f"{name}: has never run")
        elif last.ok is False:
            lines.append(f"{name}: last run failed at {timezone.localtime(last.started_at):%Y-%m-%d %H:%M}")
        elif last_ok is None or last_ok.finished_at < now - JOBS[name]:
            lines.append(f"{name}: no successful run within {JOBS[name]}")
    return lines


def job_states(now: datetime.datetime | None = None) -> dict[str, str]:
    """"ok", "late" or "failed" for each known job, for the status page."""
    now = now or timezone.now()
    states = {}
    for name, (last, last_ok) in last_runs().items():
        if last is not None and last.ok is False:
            states[name] = "failed"
        elif last_ok is None or last_ok.finished_at < now - JOBS[name]:
            states[name] = "late"
        else:
            states[name] = "ok"
    return states


def purge_old_runs(now: datetime.datetime | None = None) -> int:
    return JobRun.objects.filter(started_at__lt=(now or timezone.now()) - KEEP_RUNS).delete()[0]
