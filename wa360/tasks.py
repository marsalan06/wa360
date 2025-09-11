"""
Celery tasks for WhatsApp 360dialog integration
"""
import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

@shared_task
def send_periodic_messages():
    """Send periodic messages to clients for meeting coordination"""
    from .models import WaConversation, WaIntegration
    from .utils import OpenAIManager
    from .services import send_text_sandbox
    
    logger.info("Starting periodic message task")
    
    # Get all open conversations
    conversations = WaConversation.objects.filter(status='open')
    success_count = 0
    error_count = 0
    
    for conv in conversations:
        try:
            # Get LLM config
            llm_config = getattr(conv.integration.organization, 'llm_config', None)
            if not llm_config:
                logger.warning(f"No LLM config for conversation {conv.id}")
                continue
            
            # Check if we should send a message (e.g., no message in last 7 days)
            last_message = conv.messages.order_by('-created_at').first()
            if last_message and last_message.created_at > timezone.now() - timedelta(days=7):
                continue  # Skip if recent message exists
            
            # Generate proactive message using AI
            openai_manager = OpenAIManager.from_llm_config(llm_config)
            
            # Get conversation summary for context
            summary = ""
            if hasattr(conv, 'summary'):
                summary = conv.summary.content
            
            # Generate proactive message
            system_prompt = llm_config.get_system_prompt(summary)
            user_message = "Generate a brief, friendly message to reach out to the client about scheduling a meeting or project update."
            
            ai_message = openai_manager.chat_completion(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.7,
                max_tokens=200
            )
            
            # Send message via WhatsApp
            api_key = llm_config.get_api_key()
            response = send_text_sandbox(api_key, conv.wa_id, ai_message)
            
            # Save message to database
            from .models import WaMessage
            WaMessage.objects.create(
                integration=conv.integration,
                conversation=conv,
                direction='out',
                wa_id=conv.wa_id,
                msg_id=f"periodic_{timezone.now().timestamp()}",
                msg_type='text',
                text=ai_message,
                payload=response
            )
            
            success_count += 1
            logger.info(f"Sent periodic message to {conv.wa_id}")
            
        except Exception as e:
            logger.error(f"Failed to send periodic message to {conv.wa_id}: {str(e)}")
            error_count += 1
            continue
    
    logger.info(f"Periodic message task completed: {success_count} sent, {error_count} errors")
    return f"Sent {success_count} messages, {error_count} errors"

@shared_task
def generate_conversation_summary(conversation_id):
    """Generate AI summary for a specific conversation"""
    from .models import WaConversation
    from .utils import summarize_conversation
    
    try:
        conversation = WaConversation.objects.get(id=conversation_id)
        llm_config = getattr(conversation.integration.organization, 'llm_config', None)
        
        if not llm_config:
            raise Exception("No LLM configuration found")
        
        summary_content = summarize_conversation(llm_config, conversation)
        logger.info(f"Generated summary for conversation {conversation_id}")
        return f"Summary generated for conversation {conversation_id}"
        
    except Exception as e:
        logger.error(f"Failed to generate summary for conversation {conversation_id}: {str(e)}")
        raise

@shared_task
def process_llm_request(llm_config_id, system_prompt, user_message, task_type="chat"):
    """Generic LLM processing task"""
    from .models import LLMConfiguration
    from .utils import OpenAIManager
    
    try:
        llm_config = LLMConfiguration.objects.get(id=llm_config_id)
        openai_manager = OpenAIManager.from_llm_config(llm_config)
        
        response = openai_manager.chat_completion(
            system_prompt=system_prompt,
            user_message=user_message
        )
        
        logger.info(f"Processed LLM request for config {llm_config_id}")
        return response
        
    except Exception as e:
        logger.error(f"Failed to process LLM request: {str(e)}")
        raise
