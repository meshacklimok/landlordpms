from django.urls import path

from . import views, web_api

app_name = "properties"

urlpatterns = [
    path("", views.PropertyListView.as_view(), name="list"),
    path("owners/", views.OwnerListView.as_view(), name="owners"),
    path("owners/<uuid:public_id>/", views.OwnerEditView.as_view(), name="owner_edit"),
    path("new/", views.PropertyCreateView.as_view(), name="create"),
    path("web-api/hostel/<uuid:public_id>/rooms/", web_api.RoomMapDataView.as_view(), name="web_rooms"),
    path("web-api/hostel/<uuid:public_id>/rooms/new/", web_api.RoomsCreateDataView.as_view(), name="web_rooms_new"),
    path("web-api/hostel/room/<uuid:public_id>/", web_api.RoomEditDataView.as_view(), name="web_room"),
    path("web-api/hostel/<uuid:public_id>/rollover/", web_api.RolloverDataView.as_view(), name="web_rollover"),
    path("web-api/hostel/<uuid:public_id>/waitlist/", web_api.WaitlistDataView.as_view(), name="web_waitlist"),
    path("<uuid:public_id>/", views.PropertyDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.PropertyEditView.as_view(), name="edit"),
    path("<uuid:public_id>/owners/", views.PropertySharesView.as_view(), name="shares"),
    path("<uuid:public_id>/archive/", views.PropertyArchiveView.as_view(), name="archive"),
    path("<uuid:public_id>/team/", views.PropertyTeamView.as_view(), name="team"),
    path("<uuid:public_id>/charges/", views.PropertyChargeView.as_view(), name="charges"),
    path("<uuid:public_id>/buildings/new/", views.BuildingCreateView.as_view(), name="building_create"),
    path("<uuid:public_id>/units/new/", views.UnitCreateView.as_view(), name="unit_create"),
    path("<uuid:public_id>/rooms/", views.RoomMapView.as_view(), name="rooms"),
    path("<uuid:public_id>/rooms/new/", views.RoomsCreateView.as_view(), name="rooms_create"),
    path("<uuid:public_id>/booking-link/", views.BookingLinkView.as_view(), name="booking_link"),
    path("<uuid:public_id>/waitlist/", views.WaitlistView.as_view(), name="waitlist"),
    path("<uuid:public_id>/rollover/", views.RolloverView.as_view(), name="rollover"),
    path("rooms/<uuid:public_id>/", views.RoomEditView.as_view(), name="room"),
    path("buildings/<uuid:public_id>/", views.BuildingEditView.as_view(), name="building_edit"),
    path("units/", views.UnitListView.as_view(), name="units"),
    path("units/bulk/", views.UnitBulkView.as_view(), name="units_bulk"),
    path("units/<uuid:public_id>/", views.UnitDetailView.as_view(), name="unit"),
]
