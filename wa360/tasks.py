"""
Celery tasks for WhatsApp 360dialog integration
Simple periodic messaging system
"""
import logging
from datetime import timedelta

# Celery imports
from celery import shared_task

# Django utilities
from django.utils import timezone

# Configure logging for task monitoring
logger = logging.getLogger(__name__)


@shared_task
def send_periodic_messages(organization_id):
    """
    Send periodic messages to all integration numbers for a specific organization
    
    Args:
        organization_id: ID of the organization to send messages for
    """
    # Import models locally to avoid circular imports
    from .models import WaIntegration
    
    logger.info(f"Starting periodic message task for organization {organization_id}")
    
    # Get all integrations for this organization
    integrations = WaIntegration.objects.filter(organization_id=organization_id)
    
    if not integrations.exists():
        logger.info(f"No integrations found for organization {organization_id}")
        return {"status": "completed", "message": f"No integrations for organization {organization_id}"}
    
    success_count = 0
    error_count = 0
    
    # Send message to each integration's latest conversation
    for integration in integrations:
        try:
            # Get organization's LLM config
            llm_config = getattr(integration.organization, 'llm_config', None)
            if not llm_config:
                logger.warning(f"No LLM config for integration {integration.id}")
                error_count += 1
                continue
            
            # Get the latest conversation for this integration
            from .models import WaConversation
            latest_conversation = WaConversation.objects.filter(
                integration=integration,
                status='open'
            ).order_by('-started_at').first()
            
            # Create conversation if it doesn't exist
            if not latest_conversation:
                latest_conversation = WaConversation.objects.create(
                    integration=integration,
                    wa_id=integration.phone_number,
                    status='open',
                    started_by='periodic_task'
                )
                logger.info(f"Created new conversation for integration {integration.id}")
            
            # Generate AI message
            from .utils import get_outreach_message_prompt, OpenAIManager
            openai_manager = OpenAIManager.from_llm_config(llm_config)
            
            system_prompt = llm_config.get_system_prompt("")
            user_message = get_outreach_message_prompt()
            
            ai_message = openai_manager.chat_completion(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.7,
                max_tokens=200
            )
            
            # Send message via WhatsApp
            from .services import send_text_sandbox
            api_key = integration.get_api_key()  # Use WhatsApp API key, not OpenAI API key
            response = send_text_sandbox(api_key, latest_conversation.wa_id, ai_message)
            
            # Save message to database linked to conversation
            from .models import WaMessage
            WaMessage.objects.create(
                integration=integration,
                conversation=latest_conversation,
                direction='out',
                wa_id=latest_conversation.wa_id,
                msg_id=f"periodic_{timezone.now().timestamp()}",
                msg_type='text',
                text=ai_message,
                payload=response
            )
            
            success_count += 1
            logger.info(f"Sent periodic message to conversation {latest_conversation.id} ({latest_conversation.wa_id})")
            
        except Exception as e:
            logger.error(f"Failed to send message to integration {integration.id}: {str(e)}")
            error_count += 1
            continue
    
    logger.info(f"Periodic messaging completed for organization {organization_id}: {success_count} sent, {error_count} errors")
    return {"status": "completed", "organization_id": organization_id, "success_count": success_count, "error_count": error_count}


@shared_task
def check_and_send_periodic_messages():
    """
    Check all organization schedules and send messages as needed
    
    This task runs every minute and checks which organizations need periodic messages
    based on their individual schedules. Provides both instant and scheduled control.
    """
    from .models import PeriodicMessageSchedule
    from django.utils import timezone
    
    logger.info("Checking periodic message schedules")
    
    # Get all active schedules
    schedules = PeriodicMessageSchedule.objects.filter(is_active=True, frequency__in=['minute', 'daily', 'weekly', 'monthly'])
    
    if not schedules.exists():
        logger.info("No active schedules found")
        return {"status": "completed", "message": "No active schedules"}
    
    current_time = timezone.now()
    processed_count = 0
    sent_count = 0
    
    for schedule in schedules:
        try:
            # Check if it's time to send based on organization's schedule
            next_run = schedule.get_next_run_time()
            if next_run and current_time >= next_run:
                # Send messages for this organization
                result = send_periodic_messages.delay(schedule.organization.id)
                
                # Update last_sent timestamp
                schedule.last_sent = current_time
                schedule.save(update_fields=['last_sent'])
                
                sent_count += 1
                logger.info(f"Queued periodic messages for {schedule.organization.name} (Task ID: {result.id})")
            
            processed_count += 1
            
        except Exception as e:
            logger.error(f"Failed to process schedule for {schedule.organization.name}: {str(e)}")
            continue
    
    logger.info(f"Processed {processed_count} schedules, queued {sent_count} message tasks")
    return {"status": "completed", "processed": processed_count, "queued": sent_count}



