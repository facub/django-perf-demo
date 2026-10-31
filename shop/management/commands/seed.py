import time

from django.core.management.base import BaseCommand
from django.db import connection

# Generated server-side with generate_series: ~1M rows in seconds instead of
# minutes of bulk_create round trips.
SEED_SQL = """
TRUNCATE shop_orderitem, shop_order, shop_customer, shop_product, shop_category RESTART IDENTITY CASCADE;

INSERT INTO shop_category (name)
SELECT 'Category ' || g FROM generate_series(1, %(categories)s) g;

INSERT INTO shop_product (name, category_id, price)
SELECT 'Product ' || g, 1 + (g %% %(categories)s), round((5 + random() * 495)::numeric, 2)
FROM generate_series(1, %(products)s) g;

INSERT INTO shop_customer (name, email, country)
SELECT 'Customer ' || g, 'customer' || g || '@example.com', (ARRAY['AR','US','ES','MX','BR'])[1 + g %% 5]
FROM generate_series(1, %(customers)s) g;

INSERT INTO shop_order (customer_id, status, created_at)
SELECT 1 + floor(random() * %(customers)s)::int,
       CASE WHEN r < 0.02 THEN 'pending' WHEN r < 0.05 THEN 'cancelled'
            WHEN r < 0.30 THEN 'paid' ELSE 'shipped' END,
       now() - random() * interval '365 days'
FROM (SELECT random() AS r FROM generate_series(1, %(orders)s)) s;

INSERT INTO shop_orderitem (order_id, product_id, quantity, unit_price)
SELECT x.order_id, p.id, x.qty, p.price
FROM (
    SELECT o.id AS order_id,
           (1 + ((o.id * 7919 + n * 104729) %% %(products)s))::int AS product_id,
           1 + floor(random() * 4)::int AS qty
    FROM shop_order o
    CROSS JOIN LATERAL generate_series(1, 1 + (o.id %% 5)::int) n
) x
JOIN shop_product p ON p.id = x.product_id;

ANALYZE;
"""


class Command(BaseCommand):
    help = "Fill the database with realistic-looking fake data."

    def add_arguments(self, parser):
        parser.add_argument("--orders", type=int, default=300_000)

    def handle(self, *args, orders, **options):
        params = {"categories": 50, "products": 5_000, "customers": 20_000, "orders": orders}
        start = time.perf_counter()
        with connection.cursor() as cur:
            cur.execute(SEED_SQL, params)
            cur.execute("SELECT count(*) FROM shop_order")
            n_orders = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM shop_orderitem")
            n_items = cur.fetchone()[0]
        self.stdout.write(
            f"Seeded {n_orders:,} orders / {n_items:,} items in {time.perf_counter() - start:.1f}s"
        )
