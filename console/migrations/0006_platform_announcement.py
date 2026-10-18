from django.db import migrations


class Migration(migrations.Migration):
    """Announcement was renamed PlatformAnnouncement so it cannot be mistaken for notifications.Announcement."""

    dependencies = [
        ("analytics", "0005_owner_email"),
    ]

    operations = [
        migrations.RenameModel(old_name="Announcement", new_name="PlatformAnnouncement"),
    ]
