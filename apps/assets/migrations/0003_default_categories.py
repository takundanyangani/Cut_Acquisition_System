from django.db import migrations

CATEGORIES = [
    ("LAP", "Laptop", True),
    ("DSK", "Desktop computer", True),
    ("MON", "Monitor", True),
    ("PRN", "Printer or scanner", True),
    ("PRJ", "Projector", True),
    ("TAB", "Tablet", True),
    ("NET", "Network equipment", True),
    ("UPS", "UPS or power supply", True),
    ("PHN", "Desk phone", True),
    ("PER", "Peripheral (mouse, keyboard, headset)", False),
    ("CAB", "Cable or adapter", False),
    ("OTH", "Other ICT item", False),
]


def add_categories(apps, schema_editor):
    AssetCategory = apps.get_model("assets", "AssetCategory")
    for code, name, requires_serial in CATEGORIES:
        AssetCategory.objects.get_or_create(code=code, defaults={"name": name, "requires_serial": requires_serial})


class Migration(migrations.Migration):
    dependencies = [("assets", "0002_initial")]

    operations = [migrations.RunPython(add_categories, migrations.RunPython.noop)]
