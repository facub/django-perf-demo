"""Three real-world slow patterns, each with a "before" and an "after" version.

The before/after functions return the same data, so the benchmark can check
that the optimization did not change the result.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Prefetch, Sum
from django.utils import timezone

from .models import Order, OrderItem


# --- Case 1: "recent orders" API endpoint -----------------------------------
# Classic DRF-style N+1: one query per order for the customer, another for the
# items, and one more per item for the product name.

def recent_orders_before(limit=200):
    data = []
    for order in Order.objects.order_by("-created_at")[:limit]:
        items = order.items.all()
        data.append({
            "id": order.id,
            "customer": order.customer.name,
            "products": [item.product.name for item in items],
            "total": sum((item.quantity * item.unit_price for item in items), Decimal("0")),
        })
    return data


def recent_orders_after(limit=200):
    items = OrderItem.objects.select_related("product").only(
        "order_id", "quantity", "unit_price", "product__name"
    )
    orders = (
        Order.objects.select_related("customer")
        .only("id", "created_at", "customer__name")
        .prefetch_related(Prefetch("items", queryset=items))
        .order_by("-created_at")[:limit]
    )
    data = []
    for order in orders:
        items = order.items.all()
        data.append({
            "id": order.id,
            "customer": order.customer.name,
            "products": [item.product.name for item in items],
            "total": sum((item.quantity * item.unit_price for item in items), Decimal("0")),
        })
    return data


# --- Case 2: "stuck pending orders" ops dashboard ----------------------------
# The code is fine; the database is not. Only ~2% of orders are pending, but
# without an index Postgres scans the whole table to find them. The fix is a
# partial index (see migration 0002), no code change.

def stuck_pending_queryset():
    cutoff = timezone.now() - timedelta(days=2)
    return Order.objects.filter(status=Order.Status.PENDING, created_at__lt=cutoff).order_by("created_at")[:100]


def stuck_pending_before():
    return list(stuck_pending_queryset().values_list("id", flat=True))


stuck_pending_after = stuck_pending_before


# --- Case 3: "revenue by category" report ------------------------------------
# Aggregating in Python: loads every item of the last week and walks
# item -> product -> category lazily. The fix pushes the GROUP BY to Postgres.

REPORT_DAYS = 7


def revenue_by_category_before():
    since = timezone.now() - timedelta(days=REPORT_DAYS)
    revenue = defaultdict(Decimal)
    for item in OrderItem.objects.filter(order__created_at__gte=since):
        revenue[item.product.category.name] += item.quantity * item.unit_price
    return sorted(revenue.items(), key=lambda kv: (-kv[1], kv[0]))


def revenue_by_category_after():
    since = timezone.now() - timedelta(days=REPORT_DAYS)
    line_total = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=DecimalField())
    rows = (
        OrderItem.objects.filter(order__created_at__gte=since)
        .values("product__category__name")
        .annotate(revenue=Sum(line_total))
    )
    return sorted(((r["product__category__name"], r["revenue"]) for r in rows), key=lambda kv: (-kv[1], kv[0]))


CASES = {
    "recent-orders": (recent_orders_before, recent_orders_after),
    "stuck-pending": (stuck_pending_before, stuck_pending_after),
    "revenue-by-category": (revenue_by_category_before, revenue_by_category_after),
}
