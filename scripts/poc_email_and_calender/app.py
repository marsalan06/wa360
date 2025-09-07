# -*- coding: utf-8 -*-
import base64
from email.mime.text import MIMEText
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scopes for Gmail Send + Calendar
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events"
]

def create_message(to, subject, body_text):
    """Create a MIMEText email and return as base64url string."""
    message = MIMEText(body_text)
    message["to"] = to
    message["subject"] = subject
    raw_message = base64.urlsafe_b64encode(message.as_bytes())
    return {"raw": raw_message.decode()}

def main():
    # OAuth2 flow for any Gmail user
    flow = InstalledAppFlow.from_client_secrets_file("creds.json", SCOPES)
    creds = flow.run_local_server(port=0)

    # --- Gmail: send email ---
    gmail_service = build("gmail", "v1", credentials=creds)

    to = "<TO_EMAIL>"  # recipient
    subject = "POC Test Email with Calendar Invite"
    body = "This is a test email sent via Gmail API. A calendar invite is also created."
    message = create_message(to, subject, body)

    sent_message = gmail_service.users().messages().send(userId="me", body=message).execute()
    print(f"Email sent successfully. Message ID: {sent_message['id']}")

    # --- Calendar: create event ---
    calendar_service = build("calendar", "v3", credentials=creds)

    event = {
        "summary": "POC Meeting",
        "description": "Meeting created via Gmail + Calendar API POC.",
        "start": {
            "dateTime": "2025-09-10T10:00:00",
            "timeZone": "Asia/Karachi",
        },
        "end": {
            "dateTime": "2025-09-10T11:00:00",
            "timeZone": "Asia/Karachi",
        },
        "attendees": [
            {"email": "<ENTER_EMAIL>"}
        ],
        "reminders": {
            "useDefault": True,
        },
    }

    created_event = calendar_service.events().insert(
        calendarId="primary", body=event, sendUpdates="all"
    ).execute()

    print(f"Calendar event created successfully: {created_event.get('htmlLink')}")

if __name__ == "__main__":
    main()
