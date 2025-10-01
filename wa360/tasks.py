"""
Celery tasks for WhatsApp 360dialog integration
Simple periodic messaging system with intelligent conversation evaluation
"""
import logging
from datetime import timedelta

# Celery imports
from celery import shared_task

# Django utilities
from django.utils import timezone

# Local imports
from .models import (
    WaConversation, 
    ConversationSummary, 
    WaIntegration, 
    WaMessage, 
    PeriodicMessageSchedule
)
from .conversation_evaluation import create_evaluator_from_llm_config, ConversationStatus
from .utils import summarize_conversation, get_outreach_message_prompt, OpenAIManager
from .services import send_text_sandbox

# Configure logging for task monitoring
logger = logging.getLogger(__name__)


@shared_task(bind=False)
def evaluate_conversation_statuses(organization_id):
    """
    Evaluate all open conversations for an organization using AI analysis
    
    Args:
        organization_id: ID of the organization to evaluate conversations for
    """
    # Models are now imported at the top of the file
    
    logger.info(f"Starting conversation evaluation for organization {organization_id}")
    
    # Get all open conversations for this organization (including AI evaluation statuses)
    open_conversations = WaConversation.objects.filter(
        integration__organization_id=organization_id,
        status__in=['open', 'continue', 'schedule_later', 'evaluating']
    )
    
    if not open_conversations.exists():
        logger.info(f"No open conversations found for organization {organization_id}")
        return {"status": "completed", "message": f"No open conversations for organization {organization_id}"}
    
    # Get organization's LLM config
    integration = WaIntegration.objects.filter(organization_id=organization_id).first()
    if not integration:
        logger.error(f"No integration found for organization {organization_id}")
        return {"status": "error", "message": f"No integration found for organization {organization_id}"}
    
    llm_config = getattr(integration.organization, 'llm_config', None)
    if not llm_config:
        logger.error(f"No LLM config found for organization {organization_id}")
        return {"status": "error", "message": f"No LLM config found for organization {organization_id}"}
    
    try:
        # Create conversation evaluator
        evaluator = create_evaluator_from_llm_config(llm_config)
        
        evaluated_count = 0
        closed_count = 0
        scheduled_count = 0
        continue_count = 0
        
        for conversation in open_conversations:
            try:
                logger.info(f"Evaluating conversation {conversation.id} for {conversation.wa_id}")
                
                # Get or create conversation summary
                summary_obj, created = ConversationSummary.objects.get_or_create(
                    conversation=conversation,
                    defaults={
                        'content': 'New conversation started',
                        'message_count': 0
                    }
                )
                
                # Check if evaluation is needed (only if there are new messages)
                current_msg_count = conversation.messages.count()
                if not created and current_msg_count == summary_obj.message_count:
                    logger.info(f"Skipping conversation {conversation.id} - no new messages since last evaluation")
                    continue
                
                # Get new messages since last evaluation
                new_messages = conversation.messages.order_by('created_at')[summary_obj.message_count:]
                new_msg_count = new_messages.count()
                
                if new_msg_count == 0:
                    logger.info(f"No new messages for conversation {conversation.id}")
                    continue
                
                logger.info(f"Found {new_msg_count} new messages for conversation {conversation.id}")
                
                # Build incremental summary: previous summary + new messages
                previous_summary = summary_obj.content
                
                # Format new messages with timestamps for better context
                new_messages_text = []
                for msg in new_messages:
                    sender = "Client" if msg.direction == "in" else "Sales Engineer"
                    timestamp = msg.created_at.strftime("%b %d, %I:%M %p")
                    new_messages_text.append(f"[{timestamp}] {sender}: {msg.text}")
                
                # Generate a proper conversation summary with key points
                summary_prompt = f"""You are analyzing a business conversation between a Sales Engineer and a Client.

PREVIOUS SUMMARY (if exists):
{previous_summary if previous_summary != 'New conversation started' else 'This is a new conversation.'}

NEW MESSAGES:
{chr(10).join(new_messages_text)}

Generate a concise summary that includes:
1. **What's been discussed**: Main topics and points
2. **Key highlights**: Important decisions, requests, or information
3. **Client's interests**: What the client cares about
4. **Next steps**: Any commitments or action items mentioned

Keep it concise (2-3 sentences) and focus on business-relevant information."""

                # Use OpenAI to generate the summary
                openai_manager = OpenAIManager.from_llm_config(llm_config)
                conversation_summary = openai_manager.chat_completion(
                    system_prompt="You are a professional business conversation analyst. Create clear, concise summaries.",
                    user_message=summary_prompt,
                    temperature=0.3,
                    max_tokens=300
                )
                
                logger.info(f"Generated conversation summary for {conversation.id}")
                
                # Create incremental context for evaluation (using the generated summary)
                incremental_context = f"""
CONVERSATION SUMMARY:
{conversation_summary}

LATEST MESSAGES:
{chr(10).join(new_messages_text[-5:])}
"""
                
                # Evaluate conversation status with incremental context
                evaluation = evaluator.evaluate_conversation(
                    conversation_summary=incremental_context,
                    conversation_context=f"Conversation with {conversation.wa_id}, started by {conversation.started_by}. Total messages: {current_msg_count}"
                )
                
                logger.info(f"Evaluation result for conversation {conversation.id}: {evaluation.status} (confidence: {evaluation.confidence})")
                
                # Update conversation based on evaluation using new model method
                new_status = conversation.update_ai_status(
                    ai_status=evaluation.status,
                    confidence=evaluation.confidence,
                    reasoning=evaluation.reasoning
                )
                
                # Count based on new status
                if new_status == 'closed':
                    closed_count += 1
                    logger.info(f"Closed conversation {conversation.id} - {evaluation.reasoning}")
                elif new_status == 'schedule_later':
                    scheduled_count += 1
                    logger.info(f"Scheduled conversation {conversation.id} for later - {evaluation.reasoning}")
                else:  # continue or open
                    continue_count += 1
                    logger.info(f"Continuing conversation {conversation.id} - {evaluation.reasoning}")
                
                # Update summary: Store conversation summary + evaluation
                # Create human-readable evaluation
                status_text = {
                    ConversationStatus.CONTINUE: "actively engaged and interested",
                    ConversationStatus.SCHEDULE_LATER: "interested but wants to be contacted later",
                    ConversationStatus.CLOSE: "not interested or disengaged"
                }
                
                sentiment_emoji = {
                    'positive': '😊',
                    'neutral': '😐',
                    'negative': '😟'
                }
                
                engagement_emoji = {
                    'high': '🔥',
                    'medium': '👍',
                    'low': '📉'
                }
                
                # Build complete summary with conversation details and evaluation
                complete_summary = f"""📞 Conversation with {conversation.wa_id}

📝 Summary:
{conversation_summary}

📊 Latest Update ({conversation.last_msg_at.strftime('%b %d, %Y at %I:%M %p')}):
{new_msg_count} new message(s) received. Total messages: {current_msg_count}

💡 Client Analysis:
The client appears to be {status_text.get(evaluation.status, 'engaged')}. {evaluation.reasoning}

{sentiment_emoji.get(evaluation.client_sentiment.lower(), '😐')} Sentiment: {evaluation.client_sentiment.title()}
{engagement_emoji.get(evaluation.engagement_level.lower(), '👍')} Engagement: {evaluation.engagement_level.title()}
✅ Confidence: {int(evaluation.confidence * 100)}%

[EVALUATION]
Status: {evaluation.status}
Confidence: {evaluation.confidence:.2f}
"""
                
                summary_obj.content = complete_summary
                summary_obj.message_count = current_msg_count
                summary_obj.save()
                
                logger.info(f"✓ Updated summary and evaluation for conversation {conversation.id}")
                
                evaluated_count += 1
                
            except Exception as e:
                logger.error(f"Failed to evaluate conversation {conversation.id}: {str(e)}")
                continue
        
        logger.info(f"Conversation evaluation completed for organization {organization_id}: {evaluated_count} evaluated, {closed_count} closed, {scheduled_count} scheduled, {continue_count} continuing")
        
        return {
            "status": "completed",
            "organization_id": organization_id,
            "evaluated_count": evaluated_count,
            "closed_count": closed_count,
            "scheduled_count": scheduled_count,
            "continue_count": continue_count
        }
        
    except Exception as e:
        logger.error(f"Failed to evaluate conversations for organization {organization_id}: {str(e)}")
        return {"status": "error", "message": str(e)}


@shared_task(bind=False)
def send_periodic_messages(organization_id):
    """
    Send periodic messages to conversations that should receive them based on AI evaluation
    
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
            
            # Get the latest conversation for this integration that should receive periodic messages
            latest_conversation = WaConversation.objects.filter(
                integration=integration,
                status__in=['open', 'continue', 'schedule_later', 'evaluating']
            ).order_by('-started_at').first()
            
            # Skip if no conversation exists
            if not latest_conversation:
                logger.info(f"No conversation found for integration {integration.id}, skipping")
                continue
            
            # Check if conversation should receive periodic messages based on AI evaluation status
            if latest_conversation.status == 'continue':
                logger.info(f"Skipping conversation {latest_conversation.id} - client is actively engaged (status: {latest_conversation.status})")
                continue
            elif latest_conversation.status == 'schedule_later':
                logger.info(f"Sending periodic message to conversation {latest_conversation.id} - scheduled for later (status: {latest_conversation.status})")
            elif latest_conversation.status == 'closed':
                logger.info(f"Skipping conversation {latest_conversation.id} - conversation is closed (status: {latest_conversation.status})")
                continue
            else:
                logger.info(f"Sending periodic message to conversation {latest_conversation.id} - status: {latest_conversation.status}")
            
            # Generate AI message
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
            api_key = integration.get_api_key()  # Use WhatsApp API key, not OpenAI API key
            response = send_text_sandbox(api_key, latest_conversation.wa_id, ai_message)
            
            # Save message to database linked to conversation
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
    # Imports are now at the top of the file
    
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
            # For first run (last_sent is None), always trigger
            # For subsequent runs, compare without microseconds
            should_run = next_run and (schedule.last_sent is None or current_time.replace(microsecond=0) >= next_run.replace(microsecond=0))
            logger.info(f"Schedule {schedule.organization.name}: next_run={next_run}, current_time={current_time}, last_sent={schedule.last_sent}, should_run={should_run}")
            if should_run:
                # Re-evaluate conversation statuses before sending periodic messages
                # Note: Real-time evaluation happens on webhook (when client replies)
                # This scheduled evaluation catches any conversations that need periodic re-evaluation
                evaluation_result = evaluate_conversation_statuses.delay(schedule.organization.id)
                logger.info(f"Queued conversation evaluation for {schedule.organization.name} (Task ID: {evaluation_result.id})")
                
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



