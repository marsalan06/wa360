from django.urls import path
from . import views

app_name = 'google_integration'

urlpatterns = [
    # API endpoints
    path('api/oauth/initiate/', views.initiate_google_oauth, name='api_oauth_initiate'),
    path('api/oauth/callback/', views.oauth_callback_api, name='api_oauth_callback'),
    path('api/email/send/', views.send_email_api, name='api_send_email'),
    path('api/calendar/create/', views.create_event_api, name='api_create_event'),
    path('api/email/logs/', views.EmailLogAPIView.as_view(), name='api_email_logs'),
    path('api/calendar/events/', views.CalendarEventAPIView.as_view(), name='api_calendar_events'),
    
    # Web interface
    path('', views.GoogleIntegrationView.as_view(), name='dashboard'),
    path('oauth/initiate/', views.initiate_oauth_web, name='oauth_initiate'),
    path('oauth2callback/', views.oauth_callback_web, name='oauth_callback'),
    # Registration via Google OAuth
    path('register/', views.register_redirect, name='register_redirect'),
    path('oauth/register/callback/', views.oauth_register_callback, name='oauth_register_callback'),
]
