"""
WhatsApp Services Module
Handles conversation formatting and utilities
"""
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

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
