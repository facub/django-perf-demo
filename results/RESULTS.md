# Benchmark results

PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2) on x86_64-pc-linux-gnu, 300,000 orders, 900,000 order items. Median of several runs, warm cache, app and DB on the same machine.

| Case | Before | Queries | After | Queries | Speed-up | Same result |
|---|---:|---:|---:|---:|---:|:---:|
| recent-orders | 535 ms | 1,012 | 11 ms | 2 | x48 | yes |
| stuck-pending | 9.93 ms | 1 | 0.70 ms | 1 | x14 | yes |
| revenue-by-category | 17,036 ms | 34,165 | 31 ms | 1 | x554 | yes |

## stuck-pending: EXPLAIN (ANALYZE, BUFFERS)

Before (no index):
```
Limit  (cost=5995.65..6007.15 rows=100 width=31) (actual time=8.236..9.250 rows=100 loops=1)
  Buffers: shared hit=2279
  ->  Gather Merge  (cost=5995.65..6376.53 rows=3312 width=31) (actual time=8.235..9.244 rows=100 loops=1)
        Workers Planned: 1
        Workers Launched: 1
        Buffers: shared hit=2279
        ->  Sort  (cost=4995.64..5003.92 rows=3312 width=31) (actual time=7.393..7.396 rows=78 loops=2)
              Sort Key: created_at
              Sort Method: top-N heapsort  Memory: 36kB
              Buffers: shared hit=2279
              Worker 0:  Sort Method: top-N heapsort  Memory: 36kB
              ->  Parallel Seq Scan on shop_order  (cost=0.00..4869.06 rows=3312 width=31) (actual time=0.006..7.062 rows=2902 loops=2)
                    Filter: ((created_at < '2026-10-29 10:55:01.158329+00'::timestamp with time zone) AND ((status)::text = 'pending'::text))
                    Rows Removed by Filter: 147098
                    Buffers: shared hit=2222
Planning Time: 0.044 ms
Execution Time: 9.264 ms
```

After (partial index):
```
Limit  (cost=0.28..161.36 rows=100 width=31) (actual time=0.005..0.030 rows=100 loops=1)
  Buffers: shared hit=102
  ->  Index Scan using order_pending_created_idx on shop_order  (cost=0.28..9070.73 rows=5631 width=31) (actual time=0.004..0.025 rows=100 loops=1)
        Index Cond: (created_at < '2026-10-29 10:55:01.173837+00'::timestamp with time zone)
        Buffers: shared hit=102
Planning:
  Buffers: shared hit=4
Planning Time: 0.055 ms
Execution Time: 0.037 ms
```
