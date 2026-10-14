from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Core"

    def ready(self):
        from . import reportcache, sessions  # noqa: F401  (sessions stamps the login time on every login, D-107)

        reportcache.connect()  # a saved row bumps its organization's report version
