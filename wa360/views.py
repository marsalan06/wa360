"""
WhatsApp 360dialog Views
Handles API endpoints for integration, webhooks, and messaging
"""
import json
import logging
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.conf import settings
from django.utils import timezone

from organizations.models import Organization
from .models import WaIntegration, WaMessage, WaConversation
from .crypto import enc, dec
from .services import set_webhook_sandbox, send_text_sandbox, format_conversation_for_llm, get_latest_open_conversation_by_number
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
def connect_sandbox(request):
    """Connect sandbox: save API key, set webhook, create integration"""
    logger.info("=== CONNECT SANDBOX VIEW STARTED ===")
    
    try:
        if request.method != "POST":
            logger.warning("Invalid method: %s, expected POST", request.method)
            return HttpResponseBadRequest("POST only")
        
        logger.info("Step 1: Parsing request body...")
        body = json.loads(request.body.decode())
        api_key = body.get("api_key")
        tester = body.get("tester_msisdn")
        
        logger.info(f"Request data - API Key length: {len(api_key) if api_key else 0}, Tester: {tester}")
        
        if not api_key or not tester:
            logger.error("Missing required fields: api_key=%s, tester_msisdn=%s", bool(api_key), bool(tester))
            return HttpResponseBadRequest("api_key & tester_msisdn required")
        
        logger.info(f"Step 2: Getting active organization...")
        org = _active_org(request)
        logger.info(f"✓ Organization: {org.name} (ID: {org.id})")
        
        logger.info(f"Step 3: Setting webhook...")
        try:
            webhook_url = getattr(settings, 'D360_WEBHOOK_URL', None)
            if not webhook_url:
                logger.error("D360_WEBHOOK_URL not set in settings")
                return JsonResponse({"error": "D360_WEBHOOK_URL not configured"}, status=500)
            
            logger.info(f"Webhook URL: {webhook_url}")
            set_webhook_sandbox(api_key, webhook_url)
            logger.info("✓ Webhook set successfully")
        except Exception as webhook_error:
            logger.error(f"Failed to set webhook: {str(webhook_error)}")
            return JsonResponse({"error": f"Webhook setup failed: {str(webhook_error)}"}, status=500)
        
        logger.info(f"Step 4: Creating/updating integration...")
        integ, created = WaIntegration.objects.update_or_create(
            organization=org,
            mode="sandbox",
            defaults={
                "raw_api_key": api_key,
                "tester_msisdn": tester
            }
        )
        
        action = "created" if created else "updated"
        logger.info(f"✓ Integration {action}: ID {integ.id}")
        
        logger.info("=== CONNECT SANDBOX VIEW COMPLETED SUCCESSFULLY ===")
        return JsonResponse({
            "success": True,
            "message": f"Integration {action} successfully",
            "integration_id": integ.id
        })
        
    except Exception as e:
        logger.error(f"=== CONNECT SANDBOX VIEW FAILED: {str(e)} ===")
        return JsonResponse({"error": str(e)}, status=500)

@csrf_exempt
def webhook_360dialog(request):
    """Handle incoming webhooks from 360dialog"""
    logger.info("=== WEBHOOK PROCESSING STARTED ===")
    
    try:
        if request.method != "POST":
            logger.warning("Invalid method: %s, expected POST", request.method)
            return HttpResponse(status=405)
        
        logger.info("Step 1: Parsing webhook body...")
        body = json.loads(request.body.decode())
        logger.info(f"Webhook body: {body}")
        
        # Extract message data
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if not messages:
            logger.info("No messages in webhook, ignoring")
            return HttpResponse(status=200)
        
        logger.info(f"Step 2: Processing {len(messages)} message(s)...")
        
        for msg_data in messages:
            try:
                logger.info(f"Processing message: {msg_data}")
                
                # Extract message details
                msg_id = msg_data.get("id", "")
                from_phone = msg_data.get("from", "")
                msg_type = msg_data.get("type", "text")
                timestamp = msg_data.get("timestamp", "")
                
                # Get text content
                text = ""
                if msg_type == "text":
                    text = msg_data.get("text", {}).get("body", "")
                elif msg_type == "image":
                    text = f"[Image: {msg_data.get('image', {}).get('id', 'unknown')}]"
                elif msg_type == "audio":
                    text = f"[Audio: {msg_data.get('audio', {}).get('id', 'unknown')}]"
                elif msg_type == "video":
                    text = f"[Video: {msg_data.get('video', {}).get('id', 'unknown')}]"
                elif msg_type == "template":
                    text = f"[Template: {msg_data.get('template', {}).get('name', 'unknown')}]"
                else:
                    text = f"[{msg_type.title()}: {msg_data.get(msg_type, {}).get('id', 'unknown')}]"
                
                logger.info(f"Message details - ID: {msg_id}, From: {from_phone}, Type: {msg_type}, Text: {text[:50]}...")
                
                # Normalize phone number first
                from_phone = normalize_msisdn(from_phone)
                if not from_phone:
                    logger.error("Invalid phone number format")
                    continue
                
                # Find integration by matching the incoming phone number with tester_msisdn
                # Try different phone number formats to find the correct integration
                integration = None
                
                # Try exact match first
                integration = WaIntegration.objects.filter(
                    mode="sandbox", 
                    tester_msisdn=from_phone
                ).first()
                
                # If not found, try without + prefix
                if not integration:
                    from_phone_no_plus = from_phone.lstrip('+') if from_phone.startswith('+') else from_phone
                    integration = WaIntegration.objects.filter(
                        mode="sandbox", 
                        tester_msisdn=from_phone_no_plus
                    ).first()
                
                # If still not found, try with + prefix
                if not integration and not from_phone.startswith('+'):
                    from_phone_with_plus = '+' + from_phone
                    integration = WaIntegration.objects.filter(
                        mode="sandbox", 
                        tester_msisdn=from_phone_with_plus
                    ).first()
                
                if not integration:
                    logger.warning(f"No integration found for incoming phone: {from_phone}")
                    continue
                
                logger.info(f"✓ Found integration: {integration.organization.name} (ID: {integration.id}) for phone: {from_phone}")
                
                # Find or create conversation
                # Note: Webhook doesn't have user context, so we use the integration's organization
                conv = (WaConversation.objects
                        .filter(integration=integration, wa_id=from_phone, status__in=['open', 'continue', 'schedule_later', 'evaluating'])
                        .order_by('-last_msg_at').first())
                
                if not conv:
                    logger.info(f"Creating new conversation for {from_phone}")
                    conv = WaConversation.objects.create(
                        integration=integration, 
                        wa_id=from_phone, 
                        started_by="contact", 
                        status="open"
                    )
                    logger.info(f"✓ New conversation created: ID {conv.id}")
                else:
                    logger.info(f"✓ Using existing conversation: ID {conv.id}")
                
                # Create message record
                message = WaMessage.objects.create(
                    integration=integration,
                    conversation=conv,
                    direction='in',
                    wa_id=from_phone,
                    msg_id=msg_id,
                    msg_type=msg_type,
                    text=text,
                    payload=msg_data
                )
                
                logger.info(f"✓ Message stored: ID {message.id}")
                
                # Update conversation timestamp
                conv.last_msg_at = timezone.now()
                conv.save(update_fields=['last_msg_at'])
                logger.info(f"✓ Conversation timestamp updated")
                
                # Trigger AI evaluation automatically when client sends a message
                # This evaluates conversation status in real-time
                try:
                    from .tasks import evaluate_conversation_statuses
                    logger.info(f"Triggering AI evaluation for conversation {conv.id}")
                    evaluate_conversation_statuses.delay(integration.organization.id)
                    logger.info(f"✓ AI evaluation task queued for organization {integration.organization.id}")
                except Exception as eval_error:
                    logger.warning(f"Failed to queue AI evaluation: {str(eval_error)}")
                
            except Exception as msg_error:
                logger.error(f"Failed to process message: {str(msg_error)}")
                continue
        
        logger.info("=== WEBHOOK PROCESSING COMPLETED SUCCESSFULLY ===")
        return HttpResponse(status=200)
        
    except Exception as e:
        logger.error(f"=== WEBHOOK PROCESSING FAILED: {str(e)} ===")
        return HttpResponse(status=200)  # Always return 200 to avoid retries

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
        integ = WaIntegration.objects.for_user(request.user).filter(organization=org, mode="sandbox").first()
        
        if not integ:
            logger.error("No sandbox integration found for organization")
            return HttpResponseBadRequest("Sandbox not connected")
        
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
        try:
            response = send_text_sandbox(integ.get_api_key(), to_phone, text)
            logger.info(f"✓ Message sent successfully, API response: {response}")
        except Exception as send_error:
            logger.error(f"Failed to send message: {str(send_error)}")
            return JsonResponse({"error": f"Failed to send message: {str(send_error)}"}, status=500)
        
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
