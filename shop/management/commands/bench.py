import statistics
import time
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.test.utils import override_settings

from shop import cases

INDEXES = ["order_created_idx", "order_pending_created_idx"]
RUNS = {"recent-orders": 5, "stuck-pending": 5, "revenue-by-category": 1}


class _Rollback(Exception):
    pass


@contextmanager
def without_indexes():
    """Run the "before" state on the same data: DDL is transactional in
    Postgres, so drop the indexes inside a transaction and roll it back."""
    try:
        with transaction.atomic():
            with connection.cursor() as cur:
                for name in INDEXES:
                    cur.execute(f"DROP INDEX {name}")
            yield
            raise _Rollback
    except _Rollback:
        pass


def measure(fn, runs):
    count = 0

    def counter(execute, sql, params, many, context):
        nonlocal count
        count += 1
        return execute(sql, params, many, context)

    timings = []
    with connection.execute_wrapper(counter):
        for _ in range(runs):
            count = 0
            start = time.perf_counter()
            result = fn()
            timings.append(time.perf_counter() - start)
    return statistics.median(timings), count, result


def normalize(case, result):
    if case == "recent-orders":
        return [(r["id"], r["customer"], sorted(r["products"]), r["total"]) for r in result]
    return result


def fmt_ms(seconds):
    return f"{seconds * 1000:,.0f} ms" if seconds >= 0.01 else f"{seconds * 1000:.2f} ms"


class Command(BaseCommand):
    help = "Benchmark every case before/after and write results/RESULTS.md."

    @override_settings(DEBUG=False)
    def handle(self, *args, **options):
        rows, plans = [], {}
        for case, (before, after) in cases.CASES.items():
            after()  # warm-up so both versions hit a warm buffer cache
            with without_indexes():
                t_before, q_before, r_before = measure(before, RUNS[case])
                if case == "stuck-pending":
                    plans["before"] = cases.stuck_pending_queryset().explain(analyze=True, buffers=True)
            t_after, q_after, r_after = measure(after, RUNS[case])
            if case == "stuck-pending":
                plans["after"] = cases.stuck_pending_queryset().explain(analyze=True, buffers=True)

            same = normalize(case, r_before) == normalize(case, r_after)
            rows.append((case, t_before, q_before, t_after, q_after, same))
            self.stdout.write(
                f"{case:22} before {fmt_ms(t_before):>10} ({q_before:,} queries)  "
                f"after {fmt_ms(t_after):>10} ({q_after:,} queries)  "
                f"x{t_before / t_after:,.0f}  same_result={same}"
            )

        with connection.cursor() as cur:
            cur.execute("SELECT version()")
            pg_version = cur.fetchone()[0].split(",")[0]
            cur.execute("SELECT (SELECT count(*) FROM shop_order), (SELECT count(*) FROM shop_orderitem)")
            n_orders, n_items = cur.fetchone()

        lines = [
            "# Benchmark results",
            "",
            f"{pg_version}, {n_orders:,} orders, {n_items:,} order items. "
            "Median of several runs, warm cache, app and DB on the same machine.",
            "",
            "| Case | Before | Queries | After | Queries | Speed-up | Same result |",
            "|---|---:|---:|---:|---:|---:|:---:|",
        ]
        for case, t_b, q_b, t_a, q_a, same in rows:
            lines.append(
                f"| {case} | {fmt_ms(t_b)} | {q_b:,} | {fmt_ms(t_a)} | {q_a:,} | "
                f"x{t_b / t_a:,.0f} | {'yes' if same else 'NO'} |"
            )
        lines += [
            "",
            "## stuck-pending: EXPLAIN (ANALYZE, BUFFERS)",
            "",
            "Before (no index):",
            "```",
            plans["before"],
            "```",
            "",
            "After (partial index):",
            "```",
            plans["after"],
            "```",
            "",
        ]
        out = Path(settings.BASE_DIR) / "results" / "RESULTS.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        self.stdout.write(f"Wrote {out}")
