from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "notifications"

    def ready(self):
        from django.contrib.auth.signals import user_logged_out

        from . import push

        user_logged_out.connect(push.logged_out, dispatch_uid="notifications.push.logged_out")
