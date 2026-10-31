# Django + PostgreSQL performance: three real-world fixes

A small, reproducible case study. It shows the three problems I see most often in Django apps that "got slow as the data grew", how to diagnose each one, and how to fix it. Every number below comes from `python manage.py bench` against the included dataset (300k orders, 900k order items).

| Case | Problem | Fix | Before | After | Speed-up |
|---|---|---|---:|---:|---:|
| Recent orders API | N+1 queries in the ORM (1,012 queries) | `select_related` + `Prefetch` + index on `created_at` | 535 ms | 11 ms | **x48** |
| Stuck pending orders | Sequential scan over the whole table | Partial index `WHERE status = 'pending'` | 9.26 ms DB time | 0.037 ms DB time | **x250** in DB time |
| Revenue by category | Aggregating in Python (34,165 queries) | `values()` + `annotate(Sum(...))`, one `GROUP BY` | 17.0 s | 31 ms | **x554** |

All three versions return exactly the same data; the benchmark checks this. Full output and query plans are in [results/RESULTS.md](results/RESULTS.md).

---

## Case 1: the N+1 that hides in a serializer

```python
for order in Order.objects.order_by("-created_at")[:200]:
    order.customer.name                       # +1 query per order
    for item in order.items.all():            # +1 query per order
        item.product.name                     # +1 query per item
```

200 orders turned into **1,012 queries**. Each query is fast on its own, so nothing shows up in the slow query log. The cost is the round trips: half a second on localhost, and much more when the database is in another availability zone.

**Fix** ([shop/cases.py](shop/cases.py), `recent_orders_after`):

```python
items = OrderItem.objects.select_related("product").only("order_id", "quantity", "unit_price", "product__name")
Order.objects.select_related("customer").only("id", "created_at", "customer__name") \
     .prefetch_related(Prefetch("items", queryset=items)).order_by("-created_at")[:200]
```

**2 queries, 11 ms.** An index on `created_at` lets `ORDER BY ... LIMIT` read only the first 200 rows instead of sorting 300k.

**How to spot it:** django-debug-toolbar or a query counter in tests (`assertNumQueries`). Look for the same SQL repeated with a different id.

## Case 2: the query that's fine today and a disaster next year

```python
Order.objects.filter(status="pending", created_at__lt=cutoff).order_by("created_at")[:100]
```

Only 2% of orders are pending, yet Postgres read the **entire table** (2,279 buffers, `Parallel Seq Scan`, 147k rows removed by the filter per worker). That takes 9 ms at 300k rows. It grows linearly with the table, so at 30M rows it would take about a second on every dashboard refresh.

**Fix:** a partial index, with no code change ([migration 0002](shop/migrations/0002_add_order_indexes.py)):

```python
models.Index(fields=["created_at"], name="order_pending_created_idx", condition=Q(status="pending"))
```

With the index, Postgres reads **102 buffers** and the query takes 0.037 ms of DB time. Because the index only covers pending rows, it stays tiny and adds almost no write overhead. That makes it a better choice than a composite index on `(status, created_at)` over every row.

**How to spot it:** `pg_stat_statements` sorted by `total_exec_time`, then `EXPLAIN (ANALYZE, BUFFERS)`. A `Seq Scan` with a large "Rows Removed by Filter" count is the giveaway.

## Case 3: the report that does the database's job in Python

```python
for item in OrderItem.objects.filter(order__created_at__gte=since):
    revenue[item.product.category.name] += item.quantity * item.unit_price
```

The loop loads every line item from the last week and lazily follows item → product → category, which adds two queries per item. The result is **34,165 queries and 17 seconds**, which is where admin actions and Celery reports end up timing out.

**Fix:**

```python
OrderItem.objects.filter(order__created_at__gte=since) \
    .values("product__category__name") \
    .annotate(revenue=Sum(F("quantity") * F("unit_price")))
```

The fix is **1 query and 31 ms**. Postgres does the join and the `GROUP BY`, and only 50 rows travel back to Python.

---

## Run it yourself

Requires Docker and Python 3.12+.

```bash
docker compose up -d
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux/macOS: .venv/bin/pip
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py seed               # ~10 s, 300k orders / 900k items
.venv/Scripts/python manage.py bench              # writes results/RESULTS.md
```

The "before" measurements run on the same data with the indexes dropped inside a transaction that is rolled back. DDL in Postgres is transactional, so there's no need to keep two databases.

To look at the JSON each version returns, run `python manage.py runserver` and open `/before/<case>/` or `/after/<case>/`. The case names are `recent-orders`, `stuck-pending` and `revenue-by-category`.

## Environment

PostgreSQL 16 (Docker), Django 5.2, psycopg 3. App and database run on the same machine with a warm cache, and each figure is the median of 5 runs (1 run for the 17-second case). With a remote database, the N+1 cases get much worse, because each extra query adds network latency.
