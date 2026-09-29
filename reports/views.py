"""Reports: the annual rental income pack (D-050). Thin views; reports.income does the work."""

import csv

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can

from . import forms, income
from .pdf import render_pack

CSV_KINDS = ("months", "properties", "receipts")


def _year(request, years: list[int]) -> int:
    try:
        year = int(request.GET.get("year") or years[0])
    except ValueError:
        return years[0]
    return year if year in years else years[0]


class IncomeView(CapabilityRequiredMixin, View):
    template_name = "reports/income.html"
    required_capability = "reports.view_financial"

    def get(self, request):
        m = request.membership
        years = income.years(m)
        pack = income.income_pack(m, _year(request, years))
        return render(request, self.template_name, {
            "pack": pack, "years": years, "can_export": can(m, "reports.export"),
            "can_settings": can(m, "organization.manage"), "csv_kinds": CSV_KINDS,
            "form": forms.TaxResidenceForm(initial={"landlord_tax_residence": m.organization.landlord_tax_residence}),
        })


class TaxResidenceView(CapabilityRequiredMixin, View):
    required_capability = "organization.manage"

    def post(self, request):
        form = forms.TaxResidenceForm(request.POST)
        if form.is_valid():
            try:
                income.set_tax_residence(request.membership, form.cleaned_data["landlord_tax_residence"],
                                         request=request)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(request, _("Tax residence saved."))
        else:
            messages.error(request, _("Choose resident or non-resident."))
        url = reverse("reports:income")
        year = request.POST.get("year", "")
        return redirect(f"{url}?year={year}" if year.isdigit() else url)


class IncomeExportView(CapabilityRequiredMixin, View):
    required_capability = "reports.export"

    def get(self, request, year, kind):
        m = request.membership
        if not can(m, "reports.view_financial"):
            raise PermissionDenied("reports.view_financial")
        if year not in income.years(m) or kind not in (*CSV_KINDS, "pdf"):
            raise Http404
        pack = income.income_pack(m, year)
        if kind == "pdf":
            response = HttpResponse(render_pack(pack), content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="rental-income-{year}.pdf"'
            return response
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="rental-income-{year}-{kind}.csv"'
        response.write("﻿")  # BOM so Excel reads UTF-8
        writer = csv.writer(response)
        for row in getattr(self, f"_{kind}")(pack):
            writer.writerow(row)
        writer.writerow([])
        writer.writerow([str(income.DISCLAIMER)])
        return response

    MONEY_HEADER = ["Rent billed", "Other charges billed", "Received", "Rent received", "Other charges received",
                    "Deposits received", "Not yet applied", "Taxable rent"]

    @staticmethod
    def _money(row):
        return [row.rent_billed, row.other_billed, row.received, row.rent_received, row.other_received,
                row.deposit_received, row.unapplied, row.taxable]

    def _months(self, pack):
        yield ["Month", *self.MONEY_HEADER, "Rate", "Tax estimate"]
        for row in [*pack.months, pack.total]:
            month = row.key.strftime("%Y-%m") if row.key else row.label
            yield [month, *self._money(row), "" if row.key is None else row.rate_label,
                   "" if row.tax is None else row.tax]

    def _properties(self, pack):
        yield ["Property", *self.MONEY_HEADER]
        for row in [*pack.properties, pack.total]:
            yield [row.label, *self._money(row)]

    def _receipts(self, pack):
        yield ["Date paid", "Receipt", "Tenant", "Property", "Unit", "Method", "Reference", "Amount", "Rent",
               "Other charges", "Deposit", "Not yet applied"]
        for part in pack.receipts:
            p = part.payment
            receipt = getattr(p, "receipt", None)
            yield [p.paid_at.isoformat(), receipt.number if receipt else "", p.tenant.name if p.tenant else "",
                   p.lease.unit.property.name, p.lease.unit.code, p.get_method_display(), p.reference, p.amount,
                   part.rent, part.other, part.deposit, part.unapplied]
