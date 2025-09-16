from rest_framework import serializers
from datetime import datetime
from .models import EmailLog, CalendarEvent

class SendEmailSerializer(serializers.Serializer):
    to_email = serializers.EmailField()
    subject = serializers.CharField(max_length=500)
    body = serializers.CharField()
    
    def validate_to_email(self, value):
        if not value:
            raise serializers.ValidationError("Recipient email is required.")
        return value

class CreateEventSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=500)
    description = serializers.CharField(required=False, allow_blank=True)
    start_datetime = serializers.DateTimeField()
    end_datetime = serializers.DateTimeField()
    attendees = serializers.ListField(
        child=serializers.EmailField(),
        required=False,
        allow_empty=True
    )
    timezone = serializers.CharField(default='Asia/Karachi')
    
    def validate(self, attrs):
        if attrs['start_datetime'] >= attrs['end_datetime']:
            raise serializers.ValidationError("End time must be after start time.")
        
        if attrs['start_datetime'] < datetime.now(attrs['start_datetime'].tzinfo):
            raise serializers.ValidationError("Event cannot be scheduled in the past.")
        
        return attrs

class EmailLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    
    class Meta:
        model = EmailLog
        fields = ['id', 'user_name', 'recipient_email', 'subject', 'status', 
                 'gmail_message_id', 'error_message', 'created_at', 'sent_at']
        read_only_fields = ['id', 'created_at', 'sent_at']

class CalendarEventSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    
    class Meta:
        model = CalendarEvent
        fields = ['id', 'user_name', 'google_event_id', 'title', 'description', 
                 'start_datetime', 'end_datetime', 'attendees', 'status', 
                 'google_html_link', 'created_at', 'updated_at']
        read_only_fields = ['id', 'google_event_id', 'google_html_link', 'created_at', 'updated_at']
