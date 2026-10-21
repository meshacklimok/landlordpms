from django.urls import path

from . import views

app_name = "expenses"

urlpatterns = [
    path("", views.ExpenseListView.as_view(), name="list"),
    path("new/", views.ExpenseCreateView.as_view(), name="create"),
    path("new/shared/", views.ExpenseSplitView.as_view(), name="create_split"),
    path("suppliers/", views.SupplierListView.as_view(), name="suppliers"),
    path("suppliers/new/", views.SupplierFormView.as_view(), name="supplier_create"),
    path("suppliers/<uuid:public_id>/", views.SupplierFormView.as_view(), name="supplier_edit"),
    path("categories/", views.CategoryView.as_view(), name="categories"),
    path("<uuid:public_id>/", views.ExpenseDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.ExpenseEditView.as_view(), name="edit"),
    path("<uuid:public_id>/receipt/", views.ReceiptView.as_view(), name="receipt"),
]
