from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_catalog(sender, **kwargs):
    from .services import sync_access_catalog

    sync_access_catalog()


class AccountsConfig(AppConfig):
    name = "accounts"
    verbose_name = "Accounts and access"

    def ready(self):
        # Keeps capabilities in the database equal to the code after every migrate.
        post_migrate.connect(_sync_catalog, sender=self, dispatch_uid="accounts_sync_catalog")
