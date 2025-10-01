"""
360dialog API Services Module
Handles webhook setup, message sending, and conversation formatting
"""
import logging
import json
import requests
from typing import Dict, Any, List
from datetime import datetime
from .utils import digits_only

logger = logging.getLogger(__name__)

SANDBOX_BASE = "https://waba-sandbox.360dialog.io"

def set_webhook_sandbox(api_key: str, webhook_url: str) -> bool:
    """Set webhook URL for sandbox"""
    try:
        headers = {"D360-API-KEY": api_key, "Content-Type": "application/json"}
        data = {"url": webhook_url}
        
        response = requests.post(f"{SANDBOX_BASE}/v1/configs/webhook", headers=headers, json=data, timeout=15)
        response.raise_for_status()
        
        logger.info("Webhook set successfully")
        return True
        
    except requests.exceptions.HTTPError as http_error:
        if http_error.response.status_code == 401:
            raise Exception("API key is invalid or expired. Please check your 360dialog API key.")
        elif http_error.response.status_code == 403:
            raise Exception("API key lacks webhook configuration permissions.")
        elif http_error.response.status_code == 404:
            raise Exception("Invalid webhook endpoint. Please check the URL.")
        else:
            raise Exception(f"HTTP Error {http_error.response.status_code}: {http_error}")
            
    except Exception as e:
        logger.error(f"Failed to set webhook: {str(e)}")
        raise

def send_text_sandbox(api_key: str, to_msisdn: str, body: str) -> Dict[str, Any]:
    """Send text message via sandbox"""
    try:
        headers = {"D360-API-KEY": api_key, "Content-Type": "application/json"}
        to_digits = digits_only(to_msisdn)
        data = {
            "messaging_product": "whatsapp", 
            "to": to_digits,  # DIGITS ONLY for sandbox
            "type": "text", 
            "text": {"body": body}
        }
        
        response = requests.post(f"{SANDBOX_BASE}/v1/messages", headers=headers, json=data, timeout=15)
        response.raise_for_status()
        
        logger.info("Message sent successfully")
        return response.json()
        
    except Exception as e:
        logger.error(f"Failed to send message: {str(e)}")
        raise

def send_template_sandbox(api_key: str, to: str, template_name: str, components=None, language_code="en"):
    """Send template message via sandbox with comprehensive error handling"""
    try:
        logger.info(f"=== SEND TEMPLATE SANDBOX STARTED ===")
        logger.info(f"Template: {template_name}, To: {to}, Language: {language_code}")
        
        # Prepare request payload with required fields
        headers = {"D360-API-KEY": api_key, "Content-Type": "application/json"}  # Consistent header
        to_digits = digits_only(to)  # DIGITS ONLY for sandbox
        
        payload = {
            "to": to_digits,
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": language_code
                },
                "components": components or []
            }
        }
        
        logger.info(f"Request payload: {json.dumps(payload, indent=2)}")
        logger.info(f"Sending template '{template_name}' to {to_digits} (digits-only)")
        
        # Send request with timeout
        r = requests.post(f"{SANDBOX_BASE}/v1/messages", headers=headers, data=json.dumps(payload), timeout=20)
        r.raise_for_status()
        
        response_data = r.json()
        logger.info(f"✓ Template sent successfully, response: {response_data}")
        
        return response_data
        
    except requests.exceptions.HTTPError as http_error:
        logger.error(f"HTTP error sending template: {http_error.response.status_code} - {http_error.response.text}")
        if http_error.response.status_code == 401:
            raise Exception("API key is invalid or expired. Please check your 360dialog API key.")
        elif http_error.response.status_code == 400:
            raise Exception(f"Invalid template request: {http_error.response.text}")
        elif http_error.response.status_code == 403:
            raise Exception(f"Template permission denied: {http_error.response.text}")
        else:
            raise Exception(f"HTTP Error {http_error.response.status_code}: {http_error}")
            
    except Exception as e:
        logger.error(f"Failed to send template: {str(e)}")
        raise

def format_conversation_for_llm(conversation) -> Dict[str, Any]:
    """Format WhatsApp conversation for LLM consumption"""
    logger.info(f"=== FORMAT CONVERSATION FOR LLM STARTED for conversation {conversation.id} ===")
    
    try:
        messages = conversation.messages.all().order_by('created_at')  # Oldest first, latest at bottom
        logger.info(f"Found {messages.count()} messages in conversation {conversation.id}")
        
        formatted_messages = []
        for msg in messages:
            sender = "Client" if msg.direction == "in" else "Bot"
            message_content = msg.text
            
            # Handle non-text messages
            if msg.msg_type != "text":
                message_content = f"[{msg.msg_type.title()}: {msg.text}]" if msg.text else f"[{msg.msg_type.title()}]"
            
            formatted_messages.append({
                "sender": sender,
                "message": message_content,
                "timestamp": msg.created_at.isoformat()
            })
        
        result = {
            "conversation_id": conversation.id,
            "wa_id": conversation.wa_id,
            "status": conversation.status,
            "messages": formatted_messages
        }
        
        logger.info(f"✓ Conversation {conversation.id} formatted successfully with {len(formatted_messages)} messages")
        return result
        
    except Exception as e:
        logger.error(f"=== FORMAT CONVERSATION FOR LLM FAILED for conversation {conversation.id}: {str(e)} ===")
        raise

def get_latest_open_conversation_by_number(wa_id: str, user) -> Dict[str, Any]:
    """Get latest conversation by WhatsApp number (including closed ones for viewing)"""
    logger.info(f"=== GET LATEST CONVERSATION BY NUMBER STARTED for {wa_id} ===")
    
    try:
        from .utils import normalize_msisdn
        from .models import WaConversation
        
        # Normalize phone number
        normalized_wa_id = normalize_msisdn(wa_id)
        if not normalized_wa_id:
            logger.error("Invalid phone number format")
            return {"error": "Invalid phone number format"}
        
        # Get latest conversation (including closed ones for viewing)
        conversation = (WaConversation.objects.for_user(user)
                       .filter(wa_id=normalized_wa_id)
                       .order_by('-last_msg_at').first())
        
        if not conversation:
            logger.warning(f"No conversation found for {normalized_wa_id}")
            return {"error": "No conversation found for this number"}
        
        logger.info(f"✓ Found latest conversation: {conversation.wa_id} (ID: {conversation.id}, Status: {conversation.status})")
        
        # Format conversation for LLM
        formatted_conversation = format_conversation_for_llm(conversation)
        
        logger.info(f"✓ Latest conversation formatted successfully")
        return formatted_conversation
        
    except Exception as e:
        logger.error(f"=== GET LATEST CONVERSATION BY NUMBER FAILED for {wa_id}: {str(e)} ===")
        return {"error": str(e)}
