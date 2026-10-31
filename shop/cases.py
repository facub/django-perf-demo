"""Three real-world slow patterns, each with a "before" and an "after" version.

The before/after functions return the same data, so the benchmark can check
that the optimization did not change the result.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Prefetch, Subquery, Sum
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


def _days_ago(days):
    # Truncated to the day so the before/after runs select the same rows.
    return (timezone.now() - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


def revenue_by_category_before():
    since = _days_ago(REPORT_DAYS)
    revenue = defaultdict(Decimal)
    for item in OrderItem.objects.filter(order__created_at__gte=since):
        revenue[item.product.category.name] += item.quantity * item.unit_price
    return sorted(revenue.items(), key=lambda kv: (-kv[1], kv[0]))


def revenue_by_category_after():
    since = _days_ago(REPORT_DAYS)
    line_total = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=DecimalField())
    rows = (
        OrderItem.objects.filter(order__created_at__gte=since)
        .values("product__category__name")
        .annotate(revenue=Sum(line_total))
    )
    return sorted(((r["product__category__name"], r["revenue"]) for r in rows), key=lambda kv: (-kv[1], kv[0]))


# --- Case 4: nightly batch job (e.g. a Celery task) refreshing order totals --
# Loads every order into memory, lazily fetches its items (N+1) and saves each
# row with its own UPDATE of all columns. Slow, and memory grows with the
# number of rows. The fix is one set-based UPDATE ... SET total = (subquery):
# the work stays in Postgres and Python memory stays flat.

RECALC_DAYS = 14


def _recalc_since():
    return _days_ago(RECALC_DAYS)


def recalc_totals_before():
    for order in Order.objects.filter(created_at__gte=_recalc_since()):
        order.total = sum((item.quantity * item.unit_price for item in order.items.all()), Decimal("0"))
        order.save()


def recalc_totals_after():
    line_total = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=DecimalField())
    per_order = (
        OrderItem.objects.filter(order=OuterRef("pk"))
        .order_by()
        .values("order")
        .annotate(total=Sum(line_total))
        .values("total")
    )
    Order.objects.filter(created_at__gte=_recalc_since()).update(total=Subquery(per_order))


def read_recalc_totals():
    return dict(Order.objects.filter(created_at__gte=_recalc_since()).values_list("id", "total"))


def reset_recalc_totals():
    Order.objects.filter(created_at__gte=_recalc_since()).update(total=None)


CASES = {
    "recent-orders": (recent_orders_before, recent_orders_after),
    "stuck-pending": (stuck_pending_before, stuck_pending_after),
    "revenue-by-category": (revenue_by_category_before, revenue_by_category_after),
    "recalc-totals": (recalc_totals_before, recalc_totals_after),
}

# Cases whose functions write instead of returning data: the benchmark clears
# the target rows first, then reads them back to check before/after produce
# the same state.
WRITE_CASES = {"recalc-totals": (reset_recalc_totals, read_recalc_totals)}
