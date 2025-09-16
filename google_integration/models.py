from django.db import models
from django.conf import settings
import uuid

class GoogleCredentials(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='google_credentials')
    access_token = models.TextField()
    refresh_token = models.TextField(null=True, blank=True)
    token_uri = models.URLField()
    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    scopes = models.JSONField()
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'google_credentials'
        unique_together = ['user', 'client_id']
    
    def __str__(self):
        return f"Google credentials for {self.user.email}"

class EmailLog(models.Model):
    SENT = 'sent'
    FAILED = 'failed'
    PENDING = 'pending'
    
    STATUS_CHOICES = [
        (SENT, 'Sent'),
        (FAILED, 'Failed'),
        (PENDING, 'Pending'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_emails')
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=500)
    body = models.TextField()
    gmail_message_id = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'email_logs'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Email to {self.recipient_email} - {self.status}"

class CalendarEvent(models.Model):
    CREATED = 'created'
    UPDATED = 'updated'
    CANCELLED = 'cancelled'
    
    STATUS_CHOICES = [
        (CREATED, 'Created'),
        (UPDATED, 'Updated'),
        (CANCELLED, 'Cancelled'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='calendar_events')
    google_event_id = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    attendees = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=CREATED)
    google_html_link = models.URLField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'calendar_events'
        ordering = ['-start_datetime']
    
    def __str__(self):
        return f"{self.title} - {self.start_datetime}"
