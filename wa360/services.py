"""
360dialog API Services Module
Handles webhook setup and message sending
"""
import logging
import json
import requests
from typing import Dict, Any
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
