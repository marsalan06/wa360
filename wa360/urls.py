from django.urls import path
from . import views

urlpatterns = [
    path("integrations/wa360/sandbox/connect", views.connect_sandbox, name="connect_sandbox"),
    path("webhooks/whatsapp/360dialog", views.webhook_360dialog, name="webhook_360dialog"),
    path("api/wa360/send-text", views.send_text, name="send_text"),
    path("api/wa360/conversations/<int:conversation_id>/json", views.get_conversation_json, name="get_conversation_json"),
    path("api/wa360/conversations/number/<str:wa_id>/json", views.get_conversation_by_number, name="get_conversation_by_number"),
    path("wa360/chat", views.whatsapp_chat, name="whatsapp_chat"),
]
