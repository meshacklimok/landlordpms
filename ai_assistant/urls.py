from django.urls import path

from . import views

app_name = "ai_assistant"

urlpatterns = [
    path("", views.AskView.as_view(), name="ask"),
    path("confirm/", views.ConfirmView.as_view(), name="confirm"),
    path("switch/", views.SwitchView.as_view(), name="switch"),
    path("knowledge/", views.KnowledgeView.as_view(), name="knowledge"),
    path("knowledge/<uuid:public_id>/", views.KnowledgeEntryView.as_view(), name="knowledge_entry"),
]
