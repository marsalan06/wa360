"""Utility functions for WhatsApp 360dialog integration."""
import logging
import re
from typing import Optional, Tuple

import requests
from django.conf import settings
from django.shortcuts import render

logger = logging.getLogger(__name__)

def normalize_msisdn(msisdn: str) -> str:
    """Normalize MSISDN to standard format with + prefix"""
    digits = re.sub(r"[^\d+]", "", msisdn or "")
    if digits and not digits.startswith("+"):
        digits = "+" + digits.lstrip("+")
    return digits

def digits_only(msisdn: str) -> str:
    """Return international number with digits only (no '+'). Sandbox expects this."""
    return re.sub(r"\D", "", msisdn or "")


def get_meta_app_config() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return configured Meta app credentials."""
    logger.debug("Fetching Meta app configuration from settings")
    return (
        getattr(settings, "FB_APP_ID", None),
        getattr(settings, "FB_APP_SECRET", None),
        getattr(settings, "FB_REDIRECT_URI", None),
    )


def render_meta_connect(
    request,
    error_message: Optional[str] = None,
    extra_context: Optional[dict] = None,
):
    """Render Meta connect template with optional error."""
    from organizations.models import Organization

    if error_message:
        logger.warning("Rendering Meta connect template with error: %s", error_message)
    else:
        logger.debug("Rendering Meta connect template without errors")

    fb_app_id, _, fb_redirect_uri = get_meta_app_config()
    active_org = None
    active_org_id = request.session.get("active_org_id")
    if active_org_id:
        active_org = Organization.objects.filter(pk=active_org_id).first()

    context = {
        "fb_app_id": fb_app_id,
        "fb_callback_url": fb_redirect_uri,
        "active_org": active_org,
    }
    if error_message:
        context["error_message"] = error_message
    if extra_context:
        context.update(extra_context)

    return render(request, "wa360/connect_whatsapp.html", context)


def resolve_meta_access_token(
    code: Optional[str],
    direct_token: Optional[str],
    fb_app_id: str,
    fb_app_secret: str,
    fb_redirect_uri: str,
) -> str:
    """Resolve access token from either direct token or code exchange."""
    if direct_token:
        logger.info("Using direct access token provided by Meta callback")
        return direct_token

    if not code:
        raise ValueError("Missing authorization code from Facebook")

    logger.info("Exchanging authorization code for Meta access token")
    token_url = "https://graph.facebook.com/v22.0/oauth/access_token"
    token_params = {
        "client_id": fb_app_id,
        "client_secret": fb_app_secret,
        "redirect_uri": fb_redirect_uri,
        "code": code,
    }
    token_res = requests.get(token_url, params=token_params, timeout=20)
    token_res.raise_for_status()
    token_data = token_res.json()
    access_token = token_data.get("access_token")
    logger.debug("Received token exchange payload: keys=%s", list(token_data.keys()))
    if not access_token:
        raise ValueError("No access token returned from Facebook")
    return access_token


def fetch_primary_waba(access_token: str) -> Optional[str]:
    """Fetch first WhatsApp Business Account ID for the token."""
    logger.info("Fetching WhatsApp Business Accounts using provided access token")
    waba_res = requests.get(
        "https://graph.facebook.com/v22.0/me/whatsapp_business_accounts",
        params={"access_token": access_token},
        timeout=20,
    )
    try:
        waba_res.raise_for_status()
    except requests.exceptions.HTTPError as http_error:
        try:
            error_payload = waba_res.json()
        except ValueError:
            error_payload = waba_res.text
        logger.error("Meta WABA lookup failed: %s", error_payload)
        raise ValueError(f"Meta API error while listing WhatsApp Business Accounts: {error_payload}") from http_error
    waba_data = waba_res.json()
    logger.debug("WABA response keys: %s", list(waba_data.keys()))
    waba_entry = (waba_data.get("data") or [{}])[0]
    logger.info("Selected primary WABA entry: %s", waba_entry)
    return waba_entry.get("id")


def fetch_primary_phone_number(waba_id: Optional[str], access_token: str) -> Tuple[str, str]:
    """Fetch first phone number for the given WABA ID."""
    if not waba_id:
        logger.warning("No WABA ID supplied; skipping phone number lookup")
        return "", ""

    logger.info("Fetching phone numbers for WABA %s", waba_id)
    phone_res = requests.get(
        f"https://graph.facebook.com/v22.0/{waba_id}/phone_numbers",
        params={"access_token": access_token},
        timeout=20,
    )
    phone_res.raise_for_status()
    phone_data = phone_res.json()
    logger.debug("Phone number response keys: %s", list(phone_data.keys()))
    phone_entry = (phone_data.get("data") or [{}])[0]
    logger.info("Selected primary phone entry: %s", phone_entry)
    return (
        phone_entry.get("id", ""),
        phone_entry.get("display_phone_number", ""),
    )


def upsert_meta_integration(org, waba_id: Optional[str], phone_number_id: str, display_phone: str):
    """Create or update Meta integration details for organization."""
    from .models import WaIntegration

    integration_defaults = {
        "meta_waba_id": waba_id or "",
        "meta_phone_number_id": phone_number_id,
        "meta_phone_number": display_phone,
    }

    logger.info(
        "Upserting Meta integration for org %s with defaults %s",
        org.id,
        integration_defaults,
    )

    integration, created = WaIntegration.objects.update_or_create(
        organization=org,
        mode="meta",
        defaults=integration_defaults,
    )

    if not created:
        for field, value in integration_defaults.items():
            setattr(integration, field, value)
        integration.save(update_fields=list(integration_defaults.keys()) + ["updated_at"])
        logger.info("Updated existing Meta integration %s", integration.id)
    else:
        logger.info("Created new Meta integration %s", integration.id)

# ============================================================================
# OPENAI CLIENT MANAGER
# ============================================================================

class OpenAIManager:
    """Manages OpenAI client instances and API calls"""
    
    def __init__(self, api_key, model="gpt-4o-mini", temperature=0.7, max_tokens=1000):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of OpenAI client"""
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(api_key=self.api_key)
            except ImportError:
                raise Exception("OpenAI package not installed. Run: pip install openai")
            except Exception as e:
                raise Exception(f"Failed to initialize OpenAI client: {str(e)}")
        return self._client
    
    def chat_completion(self, system_prompt, user_message, temperature=None, max_tokens=None):
        """Make a chat completion request"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI API call failed: {str(e)}")
            raise Exception(f"AI request failed: {str(e)}")
    
    @classmethod
    def from_llm_config(cls, llm_config):
        """Create OpenAI manager from LLMConfiguration"""
        api_key = llm_config.get_api_key()
        if not api_key:
            raise Exception("No valid API key found in LLM configuration")
        
        return cls(
            api_key=api_key,
            model=llm_config.model,
            temperature=llm_config.temperature,
            max_tokens=llm_config.max_tokens
        )

# ============================================================================
# CONVERSATION SUMMARIZATION
# ============================================================================

def get_summarization_prompt():
    """Generate prompt for conversation summarization"""
    return """You are a professional conversation summarizer for business communications.

    Analyze the WhatsApp conversation between a sales engineer and client, then create a concise summary.

    SUMMARIZATION GUIDELINES:
    - Focus on key business points, decisions, and action items
    - Include meeting schedules, project updates, and important dates
    - Note client preferences, requirements, and concerns
    - Highlight any commitments made by either party
    - Keep the summary professional and factual
    - Structure the summary clearly with key points

    SUMMARY STRUCTURE:
    1. **Conversation Overview**: Brief context of the discussion
    2. **Key Points Discussed**: Main topics and decisions
    3. **Action Items**: Tasks, commitments, and next steps
    4. **Meeting Information**: Any scheduled meetings or follow-ups
    5. **Client Notes**: Important client preferences or requirements

    Provide a clear, concise summary that helps maintain context for future conversations."""

def get_outreach_message_prompt():
    """Generate prompt for proactive outreach message generation"""
    return """Generate a brief, friendly message to reach out to the client about scheduling a meeting or project update.

    MESSAGE GUIDELINES:
    - Keep it concise and professional (under 200 characters)
    - Be warm and friendly but not overly casual
    - Focus on value proposition and next steps
    - Avoid being pushy or sales-heavy
    - Reference previous conversations if context is available
    - Include a clear call-to-action (meeting, call, or response)

    MESSAGE STRUCTURE:
    - Greeting with client name if available
    - Brief context or value reminder
    - Specific ask (meeting, update, feedback)
    - Easy response option

    Generate a message that feels natural and builds on the existing relationship."""

def get_reply_prompt():
    """Generate prompt for responding to engaged client messages"""
    return """Generate a natural, helpful response to the client's latest message.

    RESPONSE GUIDELINES:
    - Keep it conversational and professional (under 300 characters)
    - Address the client's specific question or concern
    - Be warm and personable while staying business-focused
    - Provide value and move the conversation forward
    - Reference conversation context when relevant
    - Include next steps or follow-up actions when appropriate
    
    TONE:
    - Helpful and solution-oriented
    - Professional but friendly
    - Quick to respond and actionable
    - Build rapport and trust
    
    Generate a response that feels natural and maintains engagement."""

def build_conversation_text(conversation):
    """Build formatted conversation text from messages"""
    messages = conversation.messages.order_by('created_at')
    if not messages.exists():
        return "No messages to summarize"
    
    conversation_text = f"Conversation with {conversation.wa_id}:\n\n"
    for msg in messages:
        direction = "Client" if msg.direction == 'in' else "Sales Engineer"
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
        conversation_text += f"[{timestamp}] {direction}: {msg.text}\n"
    
    return conversation_text

def summarize_conversation(llm_config, conversation):
    """Generate AI summary for a conversation"""
    try:
        # Create OpenAI manager
        openai_manager = OpenAIManager.from_llm_config(llm_config)
        
        # Build conversation text
        conversation_text = build_conversation_text(conversation)
        if conversation_text == "No messages to summarize":
            return conversation_text
        
        # Generate summary
        summary_content = openai_manager.chat_completion(
            system_prompt=get_summarization_prompt(),
            user_message=f"Please summarize this conversation:\n\n{conversation_text}",
            temperature=0.3,  # Lower temperature for consistent summaries
            max_tokens=min(llm_config.max_tokens, 800)  # Limit summary length
        )
        
        # Create or update summary record
        from .models import ConversationSummary
        summary, created = ConversationSummary.objects.get_or_create(
            conversation=conversation,
            defaults={
                'content': summary_content,
                'message_count': conversation.messages.count()
            }
        )
        
        if not created:
            summary.content = summary_content
            summary.message_count = conversation.messages.count()
            summary.save()
        
        return summary_content
        
    except Exception as e:
        logger.error(f"Failed to summarize conversation {conversation.id}: {str(e)}")
        raise Exception(f"Summarization failed: {str(e)}")

def generate_ai_reply(llm_config, conversation, recent_message_limit=5):
    """Generate AI reply to client's message based on conversation context"""
    try:
        # Check if client sent the last message
        last_message = conversation.messages.order_by('-created_at').first()
        if not last_message or last_message.direction != 'in':
            logger.info(f"Skipping reply for conversation {conversation.id}: Last message was not from client")
            return None
        
        # Get recent messages for context
        recent_messages = conversation.messages.order_by('-created_at')[:recent_message_limit]
        recent_messages = list(reversed(recent_messages))  # Oldest first
        
        # Build conversation context
        context_text = ""
        for msg in recent_messages:
            sender = "Client" if msg.direction == 'in' else "You"
            timestamp = msg.created_at.strftime("%H:%M")
            context_text += f"[{timestamp}] {sender}: {msg.text}\n"
        
        # Get conversation summary if available
        from .models import ConversationSummary
        summary_obj = ConversationSummary.objects.filter(conversation=conversation).first()
        summary_context = ""
        if summary_obj and summary_obj.content:
            summary_context = f"\n\nPREVIOUS CONVERSATION SUMMARY:\n{summary_obj.content}\n"
        
        # Create OpenAI manager
        openai_manager = OpenAIManager.from_llm_config(llm_config)
        
        # Get system prompt from integration with summary context
        system_prompt = conversation.integration.get_system_prompt(summary_context)
        
        # Generate reply
        user_message = f"{get_reply_prompt()}\n\nRECENT CONVERSATION:\n{context_text}\n\nGenerate a natural response to the client's latest message:"
        
        ai_reply = openai_manager.chat_completion(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.7,
            max_tokens=300
        )
        
        logger.info(f"Generated AI reply for conversation {conversation.id}")
        return ai_reply
        
    except Exception as e:
        logger.error(f"Failed to generate AI reply for conversation {conversation.id}: {str(e)}")
        raise Exception(f"AI reply generation failed: {str(e)}")
