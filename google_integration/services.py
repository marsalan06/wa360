import base64
import json
from datetime import datetime, timezone
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from django.conf import settings
from django.utils import timezone as django_timezone
from .models import GoogleCredentials, EmailLog, CalendarEvent

class GoogleAuthService:
    def __init__(self):
        self.scopes = settings.GOOGLE_API_SCOPES
        self.client_secrets_file = settings.GOOGLE_CLIENT_SECRETS_FILE
        print("self.client_secrets_file = ", self.client_secrets_file)
        self.redirect_uri = settings.GOOGLE_REDIRECT_URI
    
    def get_authorization_url(self, user_id):
        """Generate Google OAuth2 authorization URL"""
        flow = Flow.from_client_secrets_file(
            self.client_secrets_file,
            scopes=self.scopes,
            redirect_uri=self.redirect_uri
        )
        flow.user_id = user_id  # Store user_id for later use
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'  # Forces refresh_token
        )
        
        return authorization_url, state
    
    def handle_oauth_callback(self, user, state, authorization_response):
        """Handle OAuth2 callback and store credentials"""
        try:
            flow = Flow.from_client_secrets_file(
                self.client_secrets_file,
                scopes=self.scopes,
                state=state,
                redirect_uri=self.redirect_uri
            )
            
            flow.fetch_token(authorization_response=authorization_response)
            credentials = flow.credentials
            
            # Store credentials in database
            google_creds, created = GoogleCredentials.objects.update_or_create(
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
            try:
                gmail_service = build('gmail', 'v1', credentials=credentials)
                profile = gmail_service.users().getProfile(userId='me').execute()
                user.profile.google_email = profile.get('emailAddress')
                user.profile.save()
            except:
                pass
            
            return google_creds, created
        except Exception as e:
            raise Exception(f"OAuth callback error: {str(e)}")
    
    def get_user_credentials(self, user):
        """Get user's Google credentials"""
        try:
            google_creds = GoogleCredentials.objects.filter(user=user, is_active=True).first()
            if not google_creds:
                return None
            
            credentials = Credentials(
                token=google_creds.access_token,
                refresh_token=google_creds.refresh_token,
                token_uri=google_creds.token_uri,
                client_id=google_creds.client_id,
                client_secret=google_creds.client_secret,
                scopes=google_creds.scopes
            )
            
            return credentials
        except Exception:
            return None

class GmailService:
    def __init__(self, user):
        self.user = user
        self.auth_service = GoogleAuthService()
        self.credentials = self.auth_service.get_user_credentials(user)
        if self.credentials:
            self.service = build('gmail', 'v1', credentials=self.credentials)
        else:
            self.service = None
    
    def create_message(self, to_email, subject, body_text):
        """Create a MIMEText email message"""
        message = MIMEText(body_text)
        message['to'] = to_email
        message['subject'] = subject
        raw_message = base64.urlsafe_b64encode(message.as_bytes())
        return {'raw': raw_message.decode()}
    
    def send_email(self, to_email, subject, body):
        """Send email via Gmail API"""
        if not self.service:
            raise Exception("Gmail service not initialized. Please authenticate first.")
        
        # Create email log entry
        email_log = EmailLog.objects.create(
            user=self.user,
            recipient_email=to_email,
            subject=subject,
            body=body,
            status=EmailLog.PENDING
        )
        
        try:
            message = self.create_message(to_email, subject, body)
            sent_message = self.service.users().messages().send(
                userId='me', 
                body=message
            ).execute()
            
            # Update email log
            email_log.gmail_message_id = sent_message['id']
            email_log.status = EmailLog.SENT
            email_log.sent_at = django_timezone.now()
            email_log.save()
            
            return {
                'success': True,
                'message_id': sent_message['id'],
                'email_log_id': str(email_log.id)
            }
        
        except HttpError as error:
            email_log.status = EmailLog.FAILED
            email_log.error_message = str(error)
            email_log.save()
            raise Exception(f"Gmail API error: {error}")
        
        except Exception as error:
            email_log.status = EmailLog.FAILED
            email_log.error_message = str(error)
            email_log.save()
            raise Exception(f"Email sending error: {error}")

class CalendarService:
    def __init__(self, user):
        self.user = user
        self.auth_service = GoogleAuthService()
        self.credentials = self.auth_service.get_user_credentials(user)
        if self.credentials:
            self.service = build('calendar', 'v3', credentials=self.credentials)
        else:
            self.service = None
    
    def create_event(self, title, description, start_datetime, end_datetime, attendees=None, timezone_str='Asia/Karachi'):
        """Create a calendar event"""
        if not self.service:
            raise Exception("Calendar service not initialized. Please authenticate first.")
        
        attendees_list = []
        if attendees:
            for email in attendees:
                attendees_list.append({'email': email})
        
        event = {
            'summary': title,
            'description': description,
            'start': {
                'dateTime': start_datetime.isoformat(),
                'timeZone': timezone_str,
            },
            'end': {
                'dateTime': end_datetime.isoformat(),
                'timeZone': timezone_str,
            },
            'attendees': attendees_list,
            'reminders': {
                'useDefault': True,
            },
        }
        
        try:
            created_event = self.service.events().insert(
                calendarId='primary',
                body=event,
                sendUpdates='all'
            ).execute()
            
            # Save to database
            calendar_event = CalendarEvent.objects.create(
                user=self.user,
                google_event_id=created_event['id'],
                title=title,
                description=description,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                attendees=attendees or [],
                google_html_link=created_event.get('htmlLink'),
                status=CalendarEvent.CREATED
            )
            
            return {
                'success': True,
                'event_id': created_event['id'],
                'html_link': created_event.get('htmlLink'),
                'calendar_event_id': str(calendar_event.id)
            }
        
        except HttpError as error:
            raise Exception(f"Calendar API error: {error}")
        
        except Exception as error:
            raise Exception(f"Event creation error: {error}")
    
    def list_events(self, max_results=10):
        """List upcoming calendar events"""
        if not self.service:
            raise Exception("Calendar service not initialized. Please authenticate first.")
        
        try:
            now = datetime.utcnow().isoformat() + 'Z'
            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            return events
        
        except HttpError as error:
            raise Exception(f"Calendar API error: {error}")
