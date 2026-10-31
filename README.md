# Django + PostgreSQL performance: four real-world fixes

A small, reproducible case study. It shows the problems I see most often in Django apps that "got slow as the data grew", how to diagnose each one, and how to fix it. Every number below comes from `python manage.py bench` against the included dataset (300k orders, 900k order items).

| Case | Problem | Fix | Before | After | Speed-up |
|---|---|---|---:|---:|---:|
| Recent orders API | N+1 queries in the ORM (1,012 queries) | `select_related` + `Prefetch` + index on `created_at` | 558 ms | 12 ms | **x48** |
| Stuck pending orders | Sequential scan over the whole table | Partial index `WHERE status = 'pending'` | 9.5 ms DB time | 0.037 ms DB time | **x250** in DB time |
| Revenue by category | Aggregating in Python (36,327 queries) | `values()` + `annotate(Sum(...))`, one `GROUP BY` | 18.2 s | 37 ms | **x493** |
| Nightly batch job (Celery-style) | Row-by-row recalculation: 23,575 queries, 9.1 MB of Python memory | One set-based `UPDATE ... SET total = (subquery)` | 12.8 s | 129 ms | **x99**, memory 9.1 MB → 26 KB |

Every version returns (or writes) exactly the same data; the benchmark checks this. Timings vary a few percent between runs. Full output, peak memory per case and query plans are in [results/RESULTS.md](results/RESULTS.md).

---

## Case 1: the N+1 that hides in a serializer

```python
for order in Order.objects.order_by("-created_at")[:200]:
    order.customer.name                       # +1 query per order
    for item in order.items.all():            # +1 query per order
        item.product.name                     # +1 query per item
```

200 orders turned into **1,012 queries**. Each query is fast on its own, so nothing shows up in the slow query log. The cost is the round trips: more than half a second on localhost, and much more when the database is in another availability zone.

**Fix** ([shop/cases.py](shop/cases.py), `recent_orders_after`):

```python
items = OrderItem.objects.select_related("product").only("order_id", "quantity", "unit_price", "product__name")
Order.objects.select_related("customer").only("id", "created_at", "customer__name") \
     .prefetch_related(Prefetch("items", queryset=items)).order_by("-created_at")[:200]
```

**2 queries, 12 ms.** An index on `created_at` lets `ORDER BY ... LIMIT` read only the first 200 rows instead of sorting 300k.

**How to spot it:** django-debug-toolbar or a query counter in tests (`assertNumQueries`). Look for the same SQL repeated with a different id.

## Case 2: the query that's fine today and a disaster next year

```python
Order.objects.filter(status="pending", created_at__lt=cutoff).order_by("created_at")[:100]
```

Only 2% of orders are pending, yet Postgres read the **entire table** (2,279 buffers, `Parallel Seq Scan`, 147k rows removed by the filter per worker). That takes about 9.5 ms at 300k rows. It grows linearly with the table, so at 30M rows it would take about a second on every dashboard refresh.

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

The loop loads every line item from the last week and lazily follows item → product → category, which adds two queries per item. The result is **36,327 queries and 18 seconds**, which is where admin actions and Celery reports end up timing out.

**Fix:**

```python
OrderItem.objects.filter(order__created_at__gte=since) \
    .values("product__category__name") \
    .annotate(revenue=Sum(F("quantity") * F("unit_price")))
```

The fix is **1 query and 37 ms**. Postgres does the join and the `GROUP BY`, and only 50 rows travel back to Python.

## Case 4: the batch job that eats memory and runs for hours

A typical nightly job (in real projects, a Celery task) refreshes a denormalized `total` on each order:

```python
for order in Order.objects.filter(created_at__gte=since):
    order.total = sum(i.quantity * i.unit_price for i in order.items.all())  # +1 query per order
    order.save()                                                            # +1 UPDATE per order
```

For two weeks of orders (~11,800 rows) that is **23,575 queries, 12.8 seconds and 9.1 MB of Python memory**. Every order object stays in the queryset cache until the loop ends, so memory grows linearly with the number of rows. On a full year of data, the same task takes minutes and can get the worker killed by its memory limit.

**Fix:** let Postgres do the whole thing in one statement:

```python
per_order = (OrderItem.objects.filter(order=OuterRef("pk"))
             .order_by().values("order")
             .annotate(total=Sum(F("quantity") * F("unit_price"))).values("total"))
Order.objects.filter(created_at__gte=since).update(total=Subquery(per_order))
```

The result is **1 query, 129 ms and 26 KB of Python memory**, flat no matter how many rows there are. The benchmark clears the totals before each version and checks that both write exactly the same values.

When the logic can't be expressed in SQL, the next best options are `.iterator(chunk_size=...)` so rows aren't cached, `values_list()` instead of full model instances, and `bulk_update()` in batches instead of one `save()` per row.

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

To look at the JSON each version returns, run `python manage.py runserver` and open `/before/<case>/` or `/after/<case>/`. The case names are `recent-orders`, `stuck-pending`, `revenue-by-category` and `recalc-totals`.

## Environment

PostgreSQL 16 (Docker), Django 5.2, psycopg 3. App and database run on the same machine with a warm cache, and each figure is the median of 5 runs (1 run for the 17-second case). With a remote database, the N+1 cases get much worse, because each extra query adds network latency.
