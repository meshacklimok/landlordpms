"""KRA eTIMS for our own invoices (D-060 item 11).

The adapter is chosen by the ETIMS_ADAPTER setting (a dotted path). The default does nothing, so
invoices carry no eTIMS reference until a real integration is written and certified.
"""

from django.conf import settings
from django.utils.module_loading import import_string


class EtimsAdapter:
    def submit(self, invoice) -> str:
        """Sends the invoice to eTIMS and returns its reference, or "" when nothing was sent."""
        raise NotImplementedError


class NullEtimsAdapter(EtimsAdapter):
    def submit(self, invoice) -> str:
        return ""


def get_adapter() -> EtimsAdapter:
    path = getattr(settings, "ETIMS_ADAPTER", "") or "subscriptions.etims.NullEtimsAdapter"
    return import_string(path)()
