"""
360dialog API Services Module
Handles webhook setup and message sending
"""
import logging
import requests
from typing import Dict, Any

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
        data = {"messaging_product": "whatsapp", "to": to_msisdn, "type": "text", "text": {"body": body}}
        response = requests.post(f"{SANDBOX_BASE}/v1/messages", headers=headers, json=data, timeout=15)
        response.raise_for_status()
        
        logger.info("Message sent successfully")
        return response.json()
        
    except Exception as e:
        logger.error(f"Failed to send message: {str(e)}")
        raise
