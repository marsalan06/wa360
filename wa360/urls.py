from django.urls import path
from . import views

urlpatterns = [
    path("integrations/wa360/sandbox/connect", views.connect_sandbox, name="connect_sandbox"),
    path("webhooks/whatsapp/360dialog", views.webhook_360dialog, name="webhook_360dialog"),
    path("api/wa360/send-text", views.send_text, name="send_text"),
]
