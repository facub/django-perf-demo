# Benchmark results

PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2) on x86_64-pc-linux-gnu, 300,000 orders, 900,000 order items. Median of several runs, warm cache, app and DB on the same machine.

| Case | Before | Queries | Peak memory | After | Queries | Peak memory | Speed-up | Same result |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| recent-orders | 558 ms | 1,012 | 458 KB | 12 ms | 2 | 1.2 MB | x48 | yes |
| stuck-pending | 11 ms | 1 | 14 KB | 0.80 ms | 1 | 14 KB | x14 | yes |
| revenue-by-category | 18,197 ms | 36,327 | 28.8 MB | 37 ms | 1 | 26 KB | x493 | yes |
| recalc-totals | 12,798 ms | 23,575 | 9.1 MB | 129 ms | 1 | 26 KB | x99 | yes |

Peak memory is Python-side allocation (tracemalloc) during one extra run of each version.

## stuck-pending: EXPLAIN (ANALYZE, BUFFERS)

Before (no index):
```
Limit  (cost=7498.65..7510.15 rows=100 width=47) (actual time=8.986..10.170 rows=100 loops=1)
  Buffers: shared hit=2947
  ->  Gather Merge  (cost=7498.65..7997.52 rows=4338 width=47) (actual time=8.986..10.165 rows=100 loops=1)
        Workers Planned: 1
        Workers Launched: 1
        Buffers: shared hit=2947
        ->  Sort  (cost=6498.64..6509.48 rows=4338 width=47) (actual time=7.981..7.984 rows=76 loops=2)
              Sort Key: created_at
              Sort Method: top-N heapsort  Memory: 36kB
              Buffers: shared hit=2947
              Worker 0:  Sort Method: top-N heapsort  Memory: 37kB
              ->  Parallel Seq Scan on shop_order  (cost=0.00..6332.84 rows=4338 width=47) (actual time=0.007..7.606 rows=2902 loops=2)
                    Filter: ((created_at < '2026-10-29 11:50:37.917672+00'::timestamp with time zone) AND ((status)::text = 'pending'::text))
                    Rows Removed by Filter: 147098
                    Buffers: shared hit=2890
Planning Time: 0.044 ms
Execution Time: 10.182 ms
```

After (partial index):
```
Limit  (cost=0.28..159.83 rows=100 width=47) (actual time=0.005..0.030 rows=100 loops=1)
  Buffers: shared hit=102
  ->  Index Scan using order_pending_created_idx on shop_order  (cost=0.28..11766.92 rows=7375 width=47) (actual time=0.004..0.025 rows=100 loops=1)
        Index Cond: (created_at < '2026-10-29 11:50:37.935391+00'::timestamp with time zone)
        Buffers: shared hit=102
Planning:
  Buffers: shared hit=5
Planning Time: 0.051 ms
Execution Time: 0.037 ms
```
