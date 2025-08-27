"""
WhatsApp 360dialog Views
Handles API endpoints for integration, webhooks, and messaging
"""
import json
import logging
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.conf import settings
from django.utils import timezone

from organizations.models import Organization
from .models import WaIntegration, WaMessage, WaConversation
from .crypto import enc, dec
from .services import set_webhook_sandbox, send_text_sandbox
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
            organization=org, mode="sandbox",
            defaults={"api_key_encrypted": enc(api_key), "tester_msisdn": tester}
        )
        
        action = "created" if created else "updated"
        logger.info(f"✓ Integration {action} successfully: ID {integ.id}")
        
        logger.info(f"Step 5: Creating conversation for tester...")
        try:
            # Normalize tester phone number
            tester_phone = normalize_msisdn(tester)
            if tester_phone:
                # Create conversation for the tester
                conv, conv_created = WaConversation.objects.get_or_create(
                    integration=integ,
                    wa_id=tester_phone,
                    status='open',
                    defaults={"started_by": "admin"}
                )
                conv_action = "created" if conv_created else "found existing"
                logger.info(f"✓ Conversation {conv_action}: ID {conv.id}")
            else:
                logger.warning("Could not normalize tester phone number")
        except Exception as conv_error:
            logger.warning(f"Failed to create conversation: {str(conv_error)}")
        
        logger.info("=== CONNECT SANDBOX VIEW COMPLETED SUCCESSFULLY ===")
        return JsonResponse({"ok": True, "integration_id": integ.id})
        
    except Exception as e:
        logger.error(f"=== CONNECT SANDBOX VIEW FAILED: {str(e)} ===")
        return JsonResponse({"error": str(e)}, status=500)

@csrf_exempt
def webhook_360dialog(request):
    """Receive webhook messages from 360dialog with conversation management and idempotency"""
    logger.info("=== WEBHOOK RECEIVED ===")
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request headers: {dict(request.headers)}")
    logger.info(f"Request path: {request.path}")
    logger.info(f"Request body length: {len(request.body) if request.body else 0}")
    logger.info(f"Request body preview: {request.body[:200] if request.body else 'None'}")
    
    try:
        if request.method != "POST":
            logger.info("Non-POST request, returning OK")
            return HttpResponse("OK")
        
        logger.info("Step 1: Parsing webhook payload...")
        payload = json.loads(request.body.decode("utf-8"))
        logger.info(f"Webhook payload keys: {list(payload.keys())}")
        
        # Process messages with conversation management
        logger.info("Step 2: Processing messages...")
        messages = []
        entry = payload.get("entry") or []
        logger.info(f"Found {len(entry)} entries in payload")
        
        for e in entry:
            changes = e.get("changes", [])
            logger.info(f"Entry has {len(changes)} changes")
            for ch in changes:
                val = ch.get("value", {})
                msg_list = val.get("messages", []) or []
                logger.info(f"Change has {len(msg_list)} messages")
                for m in msg_list:
                    messages.append((val, m))
        
        # Fallback for flat payloads
        if not messages and payload.get("messages"):
            logger.info("Using fallback message processing")
            for m in payload["messages"]:
                messages.append((payload, m))
        
        logger.info(f"Total messages to process: {len(messages)}")
        
        # Process messages with conversation management
        processed_count = 0
        for i, (val, m) in enumerate(messages):
            try:
                logger.info(f"Processing message {i+1}/{len(messages)}...")
                
                # Extract message details
                from_phone = normalize_msisdn(m.get("from") or m.get("wa_id") or "")
                msg_id = m.get("id") or ""
                msg_type = m.get("type") or "text"
                text = ""
                if msg_type == "text":
                    text = (m.get("text") or {}).get("body", "")
                
                logger.info(f"Message details - From: {from_phone}, ID: {msg_id}, Type: {msg_type}")
                
                if not from_phone:
                    logger.warning("No phone number found in message")
                    continue
                
                # Find integration by phone number (organization routing)
                logger.info(f"Looking for integration with tester_msisdn: {from_phone}")
                
                # Try to find integration with normalized phone number (both with and without +)
                integration = WaIntegration.objects.filter(
                    tester_msisdn=from_phone
                ).first()
                
                # If not found, try without + prefix
                if not integration:
                    from_phone_no_plus = from_phone.lstrip('+') if from_phone.startswith('+') else from_phone
                    logger.info(f"Trying without + prefix: {from_phone_no_plus}")
                    integration = WaIntegration.objects.filter(
                        tester_msisdn=from_phone_no_plus
                    ).first()
                
                # If still not found, try with + prefix
                if not integration and not from_phone.startswith('+'):
                    from_phone_with_plus = '+' + from_phone
                    logger.info(f"Trying with + prefix: {from_phone_with_plus}")
                    integration = WaIntegration.objects.filter(
                        tester_msisdn=from_phone_with_plus
                    ).first()
                
                if not integration:
                    logger.warning(f"No integration found for phone: {from_phone}")
                    continue
                
                logger.info(f"✓ Found integration: {integration.organization.name} (ID: {integration.id})")
                
                # Find or create conversation
                logger.info(f"Step 3: Managing conversation for {from_phone}...")
                conv = (WaConversation.objects
                        .filter(integration=integration, wa_id=from_phone, status='open')
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
                
                # Idempotent message creation
                logger.info(f"Step 4: Creating message record (idempotent)...")
                if msg_id:
                    # Try to find existing message by msg_id
                    existing_msg = WaMessage.objects.filter(
                        integration=integration,
                        msg_id=msg_id
                    ).first()
                    
                    if existing_msg:
                        logger.info(f"✓ Message already exists (idempotency): ID {existing_msg.id}")
                        # Update conversation timestamp
                        conv.last_msg_at = timezone.now()
                        conv.save(update_fields=['last_msg_at'])
                        processed_count += 1
                        continue
                else:
                    # Generate fallback ID for messages without msg_id
                    msg_id = f"in_{from_phone}_{m.get('timestamp','')}"
                    logger.info(f"Generated fallback msg_id: {msg_id}")
                
                # Create new message
                try:
                    message = WaMessage.objects.create(
                        integration=integration,
                        conversation=conv,
                        direction='in',
                        wa_id=from_phone,
                        msg_id=msg_id,
                        msg_type='text' if msg_type == 'text' else msg_type,
                        text=text,
                        payload=payload
                    )
                    logger.info(f"✓ Message stored with ID: {message.id}")
                    
                    # Update conversation timestamp
                    conv.last_msg_at = timezone.now()
                    conv.save(update_fields=['last_msg_at'])
                    logger.info(f"✓ Conversation timestamp updated")
                    
                    processed_count += 1
                    
                except Exception as create_error:
                    logger.error(f"Failed to create message: {str(create_error)}")
                    continue
                
            except Exception as msg_error:
                logger.error(f"Failed to process message {i+1}: {str(msg_error)}")
                continue
        
        logger.info(f"=== WEBHOOK PROCESSING COMPLETED: {processed_count}/{len(messages)} messages processed ===")
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
        org = _active_org(request)
        logger.info(f"✓ Organization: {org.name} (ID: {org.id})")
        
        logger.info(f"Step 3: Finding integration...")
        integ = WaIntegration.objects.filter(organization=org, mode="sandbox").first()
        
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
        
        # Find or create conversation
        conv = (WaConversation.objects
                .filter(integration=integ, wa_id=to_phone, status='open')
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
        
        logger.info(f"Step 5: Decrypting API key...")
        try:
            api_key = dec(integ.api_key_encrypted)
            logger.info(f"✓ API key decrypted successfully, length: {len(api_key)}")
        except Exception as decrypt_error:
            logger.error(f"Failed to decrypt API key: {str(decrypt_error)}")
            return JsonResponse({"error": f"API key decryption failed: {str(decrypt_error)}"}, status=500)
        
        logger.info(f"Step 6: Sending message via 360dialog...")
        try:
            resp = send_text_sandbox(api_key, to_phone, text)
            logger.info(f"✓ Message sent successfully, response: {resp}")
        except Exception as send_error:
            logger.error(f"Failed to send message: {str(send_error)}")
            return JsonResponse({"error": f"Message sending failed: {str(send_error)}"}, status=500)
        
        logger.info(f"Step 7: Storing message in database...")
        try:
            # Extract message ID from response, with fallback
            msg_id = str(resp.get("messages", [{}])[0].get("id", "")) if isinstance(resp, dict) else ""
            if not msg_id:
                logger.warning("No message ID in response, generating fallback")
                import uuid
                msg_id = f"out_{uuid.uuid4().hex[:16]}"
            
            message = WaMessage.objects.create(
                integration=integ,
                conversation=conv,
                direction='out',
                wa_id=to_phone,
                msg_id=msg_id,
                msg_type="text",
                text=text,
                payload=resp
            )
            logger.info(f"✓ Message stored with ID: {message.id}")
            
            # Update conversation timestamp
            conv.last_msg_at = timezone.now()
            conv.save(update_fields=['last_msg_at'])
            logger.info(f"✓ Conversation timestamp updated")
            
        except Exception as db_error:
            logger.error(f"Failed to store message: {str(db_error)}")
            return JsonResponse({"error": f"Message sent but storage failed: {str(db_error)}"}, status=500)
        
        logger.info("=== SEND TEXT VIEW COMPLETED SUCCESSFULLY ===")
        return JsonResponse({"ok": True, "resp": resp, "conversation_id": conv.id})
        
    except Exception as e:
        logger.error(f"=== SEND TEXT VIEW FAILED: {str(e)} ===")
        return JsonResponse({"error": str(e)}, status=500)
