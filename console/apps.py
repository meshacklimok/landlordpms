from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    name = "console"
    # The app was called analytics before it was renamed. Keeping the label keeps its tables, migration history and
    # constraint names as they are, so the rename needs no data migration.
    label = "analytics"
