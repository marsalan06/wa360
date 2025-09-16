from rest_framework import status, generics, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.generic import TemplateView
from django.http import JsonResponse
from django.urls import reverse
from .models import EmailLog, CalendarEvent, GoogleCredentials
from .serializers import SendEmailSerializer, CreateEventSerializer, EmailLogSerializer, CalendarEventSerializer
from .services import GoogleAuthService, GmailService, CalendarService
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime

# --- Registration via Google OAuth ---
from users.forms import UserRegistrationForm
from users.models import User

def register_redirect(request):
    """Redirect to Google OAuth screen for registration"""
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            request.session['reg_form_data'] = form.cleaned_data
            auth_service = GoogleAuthService()
            authorization_url, state = auth_service.get_authorization_url(None)
            request.session['oauth_state'] = state
            return redirect(authorization_url)
        else:
            return render(request, 'users/register.html', {'form': form})
    else:
        form = UserRegistrationForm()
    return render(request, 'users/register.html', {'form': form})

def oauth_register_callback(request):
    """Handle OAuth callback and register user"""
    state = request.GET.get('state')
    session_state = request.session.get('oauth_state')
    reg_form_data = request.session.get('reg_form_data')
    if not state or state != session_state or not reg_form_data:
        messages.error(request, 'Invalid registration or OAuth state.')
        return redirect('users:register')
    auth_service = GoogleAuthService()
    try:
        authorization_response = request.build_absolute_uri()
        # Use GoogleAuthService to get credentials
        auth_service = GoogleAuthService()
        flow = Flow.from_client_secrets_file(
            auth_service.client_secrets_file,
            scopes=auth_service.scopes,
            state=state,
            redirect_uri=auth_service.redirect_uri
        )
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        # Get Google email from userinfo endpoint
        import requests
        google_email = None
        try:
            userinfo_endpoint = 'https://www.googleapis.com/oauth2/v3/userinfo'
            headers = {'Authorization': f'Bearer {credentials.token}'}
            resp = requests.get(userinfo_endpoint, headers=headers)
            userinfo = resp.json()
            print('DEBUG: Userinfo endpoint response:', userinfo)
            google_email = userinfo.get('email')
        except Exception as e:
            print('DEBUG: Failed to get Google email from userinfo endpoint:', str(e))
        if not google_email:
            print('ERROR: Google email not found in OAuth response. Registration will fail.')
            messages.error(request, 'Could not retrieve Google email. Please try again or use a different account.')
            return redirect('users:register')
        print('DEBUG: Google email from OAuth:', google_email)
        reg_form_data['email'] = google_email
        print('DEBUG: reg_form_data before user creation:', reg_form_data)
        form = UserRegistrationForm(data=reg_form_data)
        if form.is_valid():
            user = form.save()
            # Save Google credentials
            from .models import GoogleCredentials
            google_creds, _ = GoogleCredentials.objects.update_or_create(
                user=user,
                client_id=credentials.client_id,
                defaults={
                    'access_token': credentials.token,
                    'refresh_token': credentials.refresh_token,
                    'token_uri': credentials.token_uri,
                    'client_secret': credentials.client_secret,
                    'scopes': credentials.scopes,
                    'expires_at': datetime.fromtimestamp(credentials.expiry.timestamp()) if credentials.expiry else None,
                    'is_active': True,
                }
            )
            # Update user profile with Google email
            if hasattr(user, 'profile'):
                user.profile.google_email = google_email
                user.email = google_email
                user.is_email_verified = True
                user.profile.save()
                user.save()
            del request.session['reg_form_data']
            del request.session['oauth_state']
            messages.success(request, 'Registration successful! Google account connected.')
            return redirect('users:login')
        else:
            print('DEBUG: Registration form errors:', form.errors)
            messages.error(request, f'Registration form invalid after OAuth: {form.errors}')
            return redirect('users:register')
    except Exception as e:
        print('DEBUG: Registration exception:', str(e))
        messages.error(request, f'Error during registration: {str(e)}')
        return redirect('users:register')

# API Views
@api_view(['GET'])
def initiate_google_oauth(request):
    """Initiate Google OAuth2 flow"""
    auth_service = GoogleAuthService()
    try:
        authorization_url, state = auth_service.get_authorization_url(request.user.id)
        request.session['oauth_state'] = state
        return Response({
            'authorization_url': authorization_url,
            'state': state
        })
    except Exception as e:
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def oauth_callback_api(request):
    """Handle OAuth2 callback"""
    state = request.GET.get('state')
    session_state = request.session.get('oauth_state')
    
    if not state or state != session_state:
        return Response({
            'error': 'Invalid OAuth state'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    auth_service = GoogleAuthService()
    try:
        authorization_response = request.build_absolute_uri()
        google_creds, created = auth_service.handle_oauth_callback(
            request.user, 
            state, 
            authorization_response
        )
        
        # Clean up session
        if 'oauth_state' in request.session:
            del request.session['oauth_state']
        
        return Response({
            'message': 'Google account connected successfully',
            'is_new_connection': created
        })
    
    except Exception as e:
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
def send_email_api(request):
    """Send email via Gmail API"""
    serializer = SendEmailSerializer(data=request.data)
    if serializer.is_valid():
        gmail_service = GmailService(request.user)
        try:
            result = gmail_service.send_email(
                to_email=serializer.validated_data['to_email'],
                subject=serializer.validated_data['subject'],
                body=serializer.validated_data['body']
            )
            return Response({
                'message': 'Email sent successfully',
                'result': result
            })
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
def create_event_api(request):
    """Create calendar event"""
    serializer = CreateEventSerializer(data=request.data)
    if serializer.is_valid():
        calendar_service = CalendarService(request.user)
        try:
            result = calendar_service.create_event(
                title=serializer.validated_data['title'],
                description=serializer.validated_data.get('description', ''),
                start_datetime=serializer.validated_data['start_datetime'],
                end_datetime=serializer.validated_data['end_datetime'],
                attendees=serializer.validated_data.get('attendees', []),
                timezone_str=serializer.validated_data.get('timezone', 'Asia/Karachi')
            )
            return Response({
                'message': 'Calendar event created successfully',
                'result': result
            })
        except Exception as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class EmailLogAPIView(generics.ListAPIView):
    serializer_class = EmailLogSerializer
    
    def get_queryset(self):
        return EmailLog.objects.filter(user=self.request.user)

class CalendarEventAPIView(generics.ListAPIView):
    serializer_class = CalendarEventSerializer
    
    def get_queryset(self):
        return CalendarEvent.objects.filter(user=self.request.user)

# Web Views
class GoogleIntegrationView(TemplateView):
    template_name = 'google_integration/oauth_init.html'
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('users:login')
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Check if user has Google credentials
        has_google_auth = GoogleCredentials.objects.filter(
            user=self.request.user, 
            is_active=True
        ).exists()
        context['has_google_auth'] = has_google_auth
        
        if has_google_auth:
            context['email_logs'] = EmailLog.objects.filter(user=self.request.user)[:5]
            context['calendar_events'] = CalendarEvent.objects.filter(user=self.request.user)[:5]
        
        return context

def initiate_oauth_web(request):
    """Web interface for initiating OAuth"""
    if not request.user.is_authenticated:
        return redirect('users:login')
    
    auth_service = GoogleAuthService()
    try:
        authorization_url, state = auth_service.get_authorization_url(request.user.id)
        request.session['oauth_state'] = state
        return redirect(authorization_url)
    except Exception as e:
        messages.error(request, f'Error initiating Google OAuth: {str(e)}')
        return redirect('google_integration:dashboard')

def oauth_callback_web(request):
    """Web interface OAuth callback"""
    if not request.user.is_authenticated:
        return redirect('users:login')
    
    state = request.GET.get('state')
    session_state = request.session.get('oauth_state')
    
    if not state or state != session_state:
        messages.error(request, 'Invalid OAuth state. Please try again.')
        return redirect('google_integration:dashboard')
    
    auth_service = GoogleAuthService()
    try:
        authorization_response = request.build_absolute_uri()
        google_creds, created = auth_service.handle_oauth_callback(
            request.user, 
            state, 
            authorization_response
        )
        
        # Clean up session
        if 'oauth_state' in request.session:
            del request.session['oauth_state']
        
        if created:
            messages.success(request, 'Google account connected successfully!')
        else:
            messages.success(request, 'Google account credentials updated!')
        
    except Exception as e:
        messages.error(request, f'Error connecting Google account: {str(e)}')
    
    return redirect('google_integration:dashboard')
