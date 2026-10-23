"""Fill the inventory table with demo stock for the best-selling Olist products.

Olist has no stock levels, product names or costs, so these values are
GENERATED. Say so if you show the inventory page in a demo.

    python manage.py seed_inventory --count 60
"""
import random
from decimal import Decimal

from django.core.management.base import BaseCommand

from core import services
from core.models import InventoryItem

NOUNS = {
    "health_beauty": ["Face serum", "Hair mask", "Body lotion", "Lip balm set"],
    "watches_gifts": ["Steel watch", "Leather strap watch", "Gift box"],
    "bed_bath_table": ["Cotton towel set", "Bed sheet set", "Pillow pair"],
    "sports_leisure": ["Yoga mat", "Water bottle", "Resistance bands"],
    "computers_accessories": ["Wireless mouse", "USB-C hub", "Laptop stand"],
    "furniture_decor": ["Wall shelf", "Table lamp", "Photo frame"],
    "housewares": ["Glass jar set", "Knife block", "Storage box"],
    "cool_stuff": ["Desk gadget", "Mini speaker", "LED cube"],
    "auto": ["Car phone mount", "Seat organizer", "Tyre gauge"],
    "toys": ["Building blocks", "Plush bear", "Puzzle 500pc"],
}


class Command(BaseCommand):
    help = "Create demo inventory items from the top-selling warehouse products"

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=60)
        parser.add_argument("--seed", type=int, default=42)

    def handle(self, *args, **opts):
        rng = random.Random(opts["seed"])
        products = services.top_products(None, opts["count"])
        if not products:
            self.stdout.write(self.style.WARNING(
                "Warehouse returned no products (run the ETL first). Using generic demo rows."))
            cats = list(NOUNS)
            products = [{"product_id": "", "category": rng.choice(cats),
                         "avg_price": rng.uniform(20, 400)} for _ in range(opts["count"])]

        created = 0
        for i, p in enumerate(products, start=1):
            category = p["category"] or "unknown"
            noun = rng.choice(NOUNS.get(category, ["Item"]))
            price = Decimal(str(round(p.get("avg_price") or rng.uniform(20, 300), 2)))
            cost = (price * Decimal(str(rng.uniform(0.45, 0.75)))).quantize(Decimal("0.01"))
            reorder = rng.choice([5, 10, 15, 20])
            stock = rng.choice([0, rng.randint(1, reorder), rng.randint(reorder + 1, 250),
                                rng.randint(reorder + 1, 250)])
            _, was_created = InventoryItem.objects.update_or_create(
                sku=f"SP-{i:05d}",
                defaults={"name": f"{noun} {p['product_id'][:4].upper()}".strip(),
                          "category": category, "product_id": p["product_id"] or "",
                          "unit_price": price, "unit_cost": cost,
                          "stock_qty": stock, "reorder_level": reorder})
            created += was_created
        self.stdout.write(self.style.SUCCESS(
            f"{created} items created, {len(products) - created} updated."))
