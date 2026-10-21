from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Core"

    def ready(self):
        from . import (  # noqa: F401  (sessions stamps the login time on every login, D-107)
            platform_config,
            reportcache,
            sessions,
        )

        platform_config.install()  # settings typed into admin beat the environment file (D-207)
        reportcache.connect()  # a saved row bumps its organization's report version
