from django.urls import path
from . import views

urlpatterns = [
    path("api/wa360/send-text", views.send_text, name="send_text"),
    path("api/wa360/conversations/<int:conversation_id>/json", views.get_conversation_json, name="get_conversation_json"),
    path("api/wa360/conversations/number/<str:wa_id>/json", views.get_conversation_by_number, name="get_conversation_by_number"),
    path("wa360/chat", views.whatsapp_chat, name="whatsapp_chat"),
    path("wa360/create-integration", views.create_integration_instructions, name="create_integration_instructions"),
    path("privacy-policy", views.privacy_policy, name="privacy_policy"),
]
