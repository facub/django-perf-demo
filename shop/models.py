from django.db import models
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=100)


class Product(models.Model):
    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    price = models.DecimalField(max_digits=10, decimal_places=2)


class Customer(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    country = models.CharField(max_length=2)


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        PAID = "paid"
        SHIPPED = "shipped"
        CANCELLED = "cancelled"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=10, choices=Status.choices)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            # Case 1 & 3: "latest orders" and date-range filters.
            models.Index(fields=["-created_at"], name="order_created_idx"),
            # Case 2: tiny partial index, only covers the ~2% pending rows.
            models.Index(
                fields=["created_at"],
                name="order_pending_created_idx",
                condition=models.Q(status="pending"),
            ),
        ]


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
