"""
WhatsApp Views
Handles API endpoints for integration, webhooks, and messaging
"""
import json
import logging

from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone

from organizations.models import Organization
from .models import WaIntegration, WaMessage, WaConversation
from .services import format_conversation_for_llm, get_latest_open_conversation_by_number
from .utils import normalize_msisdn

logger = logging.getLogger(__name__)

def _active_org(request) -> Organization:
    """Get active organization from session"""
    try:
        logger.info("Getting active organization from session")
        org_id = request.session.get("active_org_id")
        if not org_id:
            logger.error("No active_org_id found in session")
            raise Exception("No active organization")
        
        org = Organization.objects.get(pk=org_id)
        logger.info(f"Found active organization: {org.name} (ID: {org.id})")
        return org
    except Exception as e:
        logger.error(f"Failed to get active organization: {str(e)}")
        raise

@login_required
@csrf_exempt
def send_text(request):
    """Send text message via WhatsApp"""
    logger.info("=== SEND TEXT VIEW STARTED ===")
    
    try:
        if request.method != "POST":
            logger.warning("Invalid method: %s, expected POST", request.method)
            return HttpResponseBadRequest("POST only")
        
        logger.info("Step 1: Parsing request body...")
        body = json.loads(request.body.decode())
        to = body.get("to")
        text = body.get("text", "")
        
        logger.info(f"Request data - To: {to}, Text length: {len(text)}")
        
        if not to or not text:
            logger.error("Missing required fields: to=%s, text=%s", bool(to), bool(text))
            return HttpResponseBadRequest("to & text required")
        
        logger.info(f"Step 2: Getting active organization...")
        try:
            org = _active_org(request)
            logger.info(f"✓ Organization: {org.name} (ID: {org.id})")
        except Exception as org_error:
            logger.error(f"No active organization found: {str(org_error)}")
            return JsonResponse({"error": "No active organization. Please select an organization first."}, status=400)
        
        logger.info(f"Step 3: Finding integration...")
        # Use organization-aware manager
        integ = WaIntegration.objects.for_user(request.user).filter(organization=org).first()
        
        if not integ:
            logger.error("No integration found for organization")
            return HttpResponseBadRequest("Integration not connected")
        
        logger.info(f"✓ Found integration: ID {integ.id}")
        
        logger.info(f"Step 4: Managing conversation...")
        # Normalize phone number
        to_phone = normalize_msisdn(to)
        if not to_phone:
            logger.error("Invalid phone number format")
            return HttpResponseBadRequest("Invalid phone number format")
        
        # Find or create conversation using organization-aware manager
        conv = (WaConversation.objects.for_user(request.user)
                .filter(integration=integ, wa_id=to_phone, status__in=['open', 'continue', 'schedule_later', 'evaluating'])
                .order_by('-last_msg_at').first())
        
        if not conv:
            logger.info(f"Creating new conversation for {to_phone}")
            conv = WaConversation.objects.create(
                integration=integ, 
                wa_id=to_phone, 
                started_by="admin", 
                status="open"
            )
            logger.info(f"✓ New conversation created: ID {conv.id}")
        else:
            logger.info(f"✓ Using existing conversation: ID {conv.id}")
        
        logger.info(f"Step 5: Sending message...")
        # TODO: Replace with Waha send message function
        logger.error("Message sending not implemented - waiting for Waha integration")
        return JsonResponse({"error": "Message sending not implemented - waiting for Waha integration"}, status=501)
        
        logger.info(f"Step 6: Storing message...")
        try:
            # Extract message ID from response
            msg_id = ""
            try:
                if response and isinstance(response, dict):
                    messages = response.get("messages", [])
                    if messages and isinstance(messages, list) and len(messages) > 0:
                        msg_id = str(messages[0].get("id", ""))
            except Exception:
                pass
            
            message = WaMessage.objects.create(
                integration=integ,
                conversation=conv,
                direction='out',
                wa_id=to_phone,
                msg_id=msg_id,
                msg_type="text",
                text=text,
                payload=response
            )
            
            logger.info(f"✓ Message stored: ID {message.id}")
            
            # Update conversation timestamp
            conv.last_msg_at = timezone.now()
            conv.save(update_fields=['last_msg_at'])
            logger.info(f"✓ Conversation timestamp updated")
            
        except Exception as db_error:
            logger.error(f"Failed to store message: {str(db_error)}")
            return JsonResponse({"error": f"Message sent but failed to store: {str(db_error)}"}, status=500)
        
        logger.info("=== SEND TEXT VIEW COMPLETED SUCCESSFULLY ===")
        return JsonResponse({
            "success": True,
            "message": "Message sent successfully",
            "message_id": message.id
        })
        
    except Exception as e:
        logger.error(f"=== SEND TEXT VIEW FAILED: {str(e)} ===")
        return JsonResponse({"error": str(e)}, status=500)

@login_required
def get_conversation_json(request, conversation_id):
    """Get formatted conversation JSON for LLM consumption"""
    logger.info(f"=== GET CONVERSATION JSON STARTED for ID {conversation_id} ===")
    
    try:
        conversation = WaConversation.objects.for_user(request.user).filter(id=conversation_id).first()
        
        if not conversation:
            logger.error(f"Conversation {conversation_id} not found or access denied")
            return JsonResponse({"error": "Conversation not found"}, status=404)
        
        logger.info(f"✓ Found conversation: {conversation.wa_id} (ID: {conversation.id})")
        
        formatted_conversation = format_conversation_for_llm(conversation)
        
        logger.info(f"✓ Conversation formatted successfully with {len(formatted_conversation['messages'])} messages")
        
        return JsonResponse({
            "success": True,
            "conversation": formatted_conversation
        })
        
    except Exception as e:
        logger.error(f"=== GET CONVERSATION JSON FAILED: {str(e)} ===")
        return JsonResponse({"error": str(e)}, status=500)

@login_required
def get_conversation_by_number(request, wa_id):
    """Get latest open conversation by WhatsApp number"""
    logger.info(f"=== GET CONVERSATION BY NUMBER STARTED for {wa_id} ===")
    
    try:
        result = get_latest_open_conversation_by_number(wa_id, request.user)
        
        if "error" in result:
            logger.error(f"Error getting conversation by number: {result['error']}")
            return JsonResponse(result, status=404)
        
        logger.info(f"✓ Conversation by number retrieved successfully")
        
        # Add conversation status to the response
        return JsonResponse({
            "success": True,
            "conversation": result,
            "status": result.get('status', 'open')
        })
        
    except Exception as e:
        logger.error(f"=== GET CONVERSATION BY NUMBER FAILED: {str(e)} ===")
        return JsonResponse({"error": str(e)}, status=500)

@login_required
def whatsapp_chat(request):
    """WhatsApp chat interface"""
    try:
        # Check if user has an active organization, if not set the first one
        if not request.session.get("active_org_id"):
            user_orgs = Organization.objects.filter(users=request.user)
            if user_orgs.exists():
                first_org = user_orgs.first()
                request.session["active_org_id"] = first_org.id
                logger.info(f"Auto-selected organization: {first_org.name} (ID: {first_org.id}) for user {request.user.username}")
            else:
                logger.error(f"User {request.user.username} has no organizations")
                return render(request, 'wa360/chat.html', {
                    'phone_numbers': [],
                    'error': 'No organizations found for this user'
                })
        
        # Get user's conversations to extract unique phone numbers (including all statuses)
        conversations = WaConversation.objects.for_user(request.user).order_by('-last_msg_at')
        
        # Extract unique phone numbers from conversations (including closed ones)
        phone_numbers = list(conversations.values_list('wa_id', flat=True).distinct())
        
        logger.info(f"Found {len(phone_numbers)} phone numbers for user {request.user.username}")
        
        return render(request, 'wa360/chat.html', {
            'phone_numbers': phone_numbers
        })
        
    except Exception as e:
        logger.error(f"Error loading WhatsApp chat: {str(e)}")
        return render(request, 'wa360/chat.html', {
            'phone_numbers': [],
            'error': str(e)
        })


def privacy_policy(request):
    """Render public privacy policy page."""
    return render(request, "wa360/privacy_policy.html", {})


def create_integration_instructions(request):
    """Show help on how to create a WhatsApp integration before signup."""
    return render(request, "wa360/create_integration.html", {})
