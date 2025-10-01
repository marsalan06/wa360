"""
WhatsApp 360dialog Integration Models
"""
import logging
from django.db import models
from django.contrib.auth.models import User
from organizations.models import Organization
from .crypto import enc, dec
from .utils import summarize_conversation
from .conversation_evaluation import ConversationStatus


logger = logging.getLogger(__name__)

# ============================================================================
# ORGANIZATION-AWARE MODEL MANAGERS
# ============================================================================

class OrganizationAwareManager(models.Manager):
    """Manager that filters queryset by user's organizations"""
    
    def get_queryset(self):
        """Get base queryset"""
        return super().get_queryset()
    
    def for_user(self, user):
        """Filter queryset for specific user's organizations"""
        if user.is_superuser:
            return self.get_queryset()
        
        # Get user's organizations
        user_orgs = Organization.objects.filter(users=user)
        return self.get_queryset().filter(organization__in=user_orgs)

class WaIntegrationManager(OrganizationAwareManager):
    """Manager for WaIntegration with organization filtering"""
    
    def for_user(self, user):
        """Filter integrations by user's organizations"""
        if user.is_superuser:
            return self.get_queryset()
        
        user_orgs = Organization.objects.filter(users=user)
        return self.get_queryset().filter(organization__in=user_orgs)

class WaConversationManager(OrganizationAwareManager):
    """Manager for WaConversation with organization filtering"""
    
    def for_user(self, user):
        """Filter conversations by user's organizations"""
        if user.is_superuser:
            return self.get_queryset()
        
        user_orgs = Organization.objects.filter(users=user)
        return self.get_queryset().filter(integration__organization__in=user_orgs)

class WaMessageManager(OrganizationAwareManager):
    """Manager for WaMessage with organization filtering"""
    
    def for_user(self, user):
        """Filter messages by user's organizations"""
        if user.is_superuser:
            return self.get_queryset()
        
        user_orgs = Organization.objects.filter(users=user)
        return self.get_queryset().filter(integration__organization__in=user_orgs)

# ============================================================================
# MODELS
# ============================================================================

class WaIntegration(models.Model):
    """WhatsApp Integration Model"""
    MODE_CHOICES = [('sandbox', 'Sandbox'), ('prod', 'Production')]
    
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="wa_integrations")
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="sandbox")
    raw_api_key = models.CharField(max_length=200, blank=True, help_text="Raw API key (will be encrypted automatically)")
    api_key_encrypted = models.TextField(blank=True, help_text="Encrypted API key (auto-generated)")
    tester_msisdn = models.CharField(max_length=32, blank=True, default="")
    
    # Context fields for AI personalization per integration/number
    client_context = models.TextField(blank=True, help_text="Client details and context for this WhatsApp number")
    project_context = models.TextField(blank=True, help_text="Project details and context for this WhatsApp number")
    custom_instructions = models.TextField(blank=True, help_text="Custom behavior instructions for this WhatsApp number")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Use organization-aware manager
    objects = WaIntegrationManager()
    
    class Meta:
        indexes = [models.Index(fields=['organization', 'mode'])]
    
    def __str__(self):
        phone_display = f" ({self.tester_msisdn})" if self.tester_msisdn else ""
        return f"{self.organization.name} - {self.mode}{phone_display}"
    
    def save(self, *args, **kwargs):
        """Override save to automatically encrypt API key"""
        try:
            logger.info(f"=== SAVE METHOD STARTED for integration {getattr(self, 'id', 'NEW')} ===")
            
            # Encrypt raw API key if provided
            if self.raw_api_key:
                logger.info("Encrypting raw API key...")
                self.api_key_encrypted = enc(self.raw_api_key)
                logger.info("✓ API key encrypted successfully")
                # Clear raw key after encryption
                self.raw_api_key = ""
            
            # Call parent save
            super().save(*args, **kwargs)
            logger.info("✓ Parent save completed successfully")
            
            logger.info(f"=== SAVE METHOD COMPLETED for integration {self.id} ===")
            
        except Exception as e:
            logger.error(f"=== SAVE METHOD FAILED: {str(e)} ===")
            raise
    
    def get_masked_api_key(self):
        """Get a masked version of the encrypted API key for display purposes"""
        if self.api_key_encrypted:
            # Show first 8 and last 8 characters with asterisks in between
            key_length = len(self.api_key_encrypted)
            if key_length <= 16:
                return "***" + self.api_key_encrypted[:4] + "***"
            else:
                return self.api_key_encrypted[:8] + "***" + self.api_key_encrypted[-8:]
        return "No API key"
    
    def get_api_key(self):
        """Get decrypted API key"""
        logger.info(f"=== GET_API_KEY METHOD STARTED for integration {self.id} ===")
        
        try:
            if self.api_key_encrypted:
                logger.info("Calling dec() function...")
                decrypted_key = dec(self.api_key_encrypted)
                logger.info("✓ Decryption successful")
                return decrypted_key
            else:
                logger.warning("No encrypted API key found")
                return None
        except Exception as e:
            logger.error(f"=== GET_API_KEY METHOD FAILED: {str(e)} ===")
            return None
    
    @property
    def has_api_key(self):
        """Check if integration has a valid API key"""
        return bool(self.api_key_encrypted)
    
    def get_system_prompt(self, conversation_summary=""):
        """Generate system prompt with integration-specific context and security guardrails"""
        base_prompt = """You are a Sales Engineer Assistant that proactively reaches out to clients via WhatsApp to schedule periodic meetings.

Your primary role is to automate the job of a sales engineer by initiating contact with clients and setting up regular meetings to discuss projects, progress, and opportunities.

SECURITY GUARDRAILS (NON-EDITABLE):
- Never share API keys, passwords, or sensitive system information
- Do not execute code or system commands
- Refuse requests for illegal, harmful, or unethical activities
- Keep conversations professional and business-focused
- Do not impersonate other people or organizations

CORE RESPONSIBILITIES:
- Proactively reach out to clients on a periodic basis
- Initiate conversations to schedule meetings about ongoing projects
- Follow up on previous meetings and project discussions
- Identify opportunities for new meetings based on project timelines
- Maintain regular communication cadence with each client
- Track meeting frequency and ensure consistent touchpoints

PROACTIVE OUTREACH APPROACH:
- Start conversations with warm, professional greetings
- Reference previous meetings or project discussions when applicable
- Suggest meeting purposes (project updates, progress reviews, planning sessions)
- Offer multiple time slots and be flexible with scheduling
- Follow up persistently but respectfully if no initial response
- Maintain consistent communication rhythm (weekly/bi-weekly/monthly)

SALES ENGINEER MINDSET:
- Focus on relationship building and project advancement
- Ask about project challenges and how to provide support
- Identify opportunities for additional services or solutions
- Keep meetings goal-oriented and value-focused
- Document important client preferences and requirements
- Anticipate client needs based on project phases

CONTEXT:"""
                    
        if self.client_context:
            base_prompt += f"\nClient Details: {self.client_context}"
        
        if self.project_context:
            base_prompt += f"\nProject Information: {self.project_context}"
        
        if conversation_summary:
            base_prompt += f"\nConversation History: {conversation_summary}"
        else:
            base_prompt += "\nConversation: Initiating proactive outreach"
        
        if self.custom_instructions:
            base_prompt += f"\nAdditional Instructions: {self.custom_instructions}"
        
        base_prompt += "\n\nBe proactive, professional, and persistent in reaching out to clients. Focus on building relationships and ensuring regular project touchpoints through scheduled meetings."
        
        return base_prompt

class WaConversation(models.Model):
    """WhatsApp Conversation Model - Groups messages between a contact and integration"""
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('closed', 'Closed'),
        ('continue', 'Continue - Client Engaged'),
        ('schedule_later', 'Schedule Later - Client Postponed'),
        ('evaluating', 'Evaluating - AI Analysis in Progress'),
    ]
    
    integration = models.ForeignKey('WaIntegration', on_delete=models.CASCADE, related_name='conversations')
    wa_id = models.CharField(max_length=32, db_index=True, help_text="WhatsApp ID of the contact")
    started_by = models.CharField(max_length=32, blank=True, default="admin", 
                                help_text="Who started the conversation: admin|contact|system")
    status = models.CharField(max_length=60, choices=STATUS_CHOICES, default='open')
    started_at = models.DateTimeField(auto_now_add=True)
    last_msg_at = models.DateTimeField(auto_now=True)

    # Use organization-aware manager
    objects = WaConversationManager()

    class Meta:
        indexes = [
            models.Index(fields=['integration', 'wa_id']),
            models.Index(fields=['status', 'last_msg_at']),
        ]
        ordering = ['-last_msg_at']

    def __str__(self):
        return f"Conv #{self.id} [{self.status}] with {self.wa_id} ({self.integration.organization.name})"

    @property
    def is_open(self):
        """Check if conversation is currently open (includes AI evaluation statuses)"""
        return self.status in ['open', 'continue', 'schedule_later', 'evaluating']

    def close(self):
        """Close the conversation and update timestamp"""
        logger.info(f"Closing conversation {self.id} for {self.wa_id}")
        self.status = 'closed'
        self.save(update_fields=['status', 'last_msg_at'])
        logger.info(f"✓ Conversation {self.id} closed successfully")
    
    def update_ai_status(self, ai_status, confidence=None, reasoning=None):
        """Update conversation status based on AI evaluation"""
        # Map AI status to conversation status
        status_mapping = {
            ConversationStatus.CONTINUE: 'continue',
            ConversationStatus.SCHEDULE_LATER: 'schedule_later', 
            ConversationStatus.CLOSE: 'closed'
        }
        
        new_status = status_mapping.get(ai_status, 'open')
        
        logger.info(f"Updating conversation {self.id} status from {self.status} to {new_status} (AI: {ai_status})")
        self.status = new_status
        self.save(update_fields=['status', 'last_msg_at'])
        
        if confidence and reasoning:
            logger.info(f"AI Evaluation - Confidence: {confidence:.2f}, Reasoning: {reasoning}")
        
        return new_status

class LLMConfiguration(models.Model):
    """LLM Configuration for Organizations - Model and API settings only"""
    MODEL_CHOICES = [
        ('gpt-4o', 'GPT-4o'),
        ('gpt-4o-mini', 'GPT-4o Mini'),
        ('gpt-4.1', 'GPT-4.1'),
    ]
    
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="llm_config")
    raw_api_key = models.CharField(max_length=200, blank=True, help_text="OpenAI API key (will be encrypted)")
    api_key_encrypted = models.TextField(blank=True, help_text="Encrypted API key")
    model = models.CharField(max_length=20, choices=MODEL_CHOICES, default='gpt-4o-mini')
    temperature = models.FloatField(default=0.7, help_text="0.0 to 1.0")
    max_tokens = models.IntegerField(default=1000, help_text="Maximum response tokens")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    objects = OrganizationAwareManager()
    
    class Meta:
        indexes = [models.Index(fields=['organization'])]
    
    def __str__(self):
        return f"LLM Config for {self.organization.name}"
    
    def save(self, *args, **kwargs):
        """Encrypt API key on save"""
        if self.raw_api_key:
            self.api_key_encrypted = enc(self.raw_api_key)
            self.raw_api_key = ""
        super().save(*args, **kwargs)
    
    def get_api_key(self):
        """Get decrypted API key"""
        if self.api_key_encrypted:
            try:
                return dec(self.api_key_encrypted)
            except Exception:
                return None
        return None

class ConversationSummary(models.Model):
    """AI-generated summaries for conversations"""
    conversation = models.OneToOneField('WaConversation', on_delete=models.CASCADE, related_name='summary')
    content = models.TextField(help_text="AI-generated conversation summary")
    message_count = models.IntegerField(default=0, help_text="Number of messages when summary was generated")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    objects = OrganizationAwareManager()
    
    class Meta:
        indexes = [models.Index(fields=['conversation', 'updated_at'])]
    
    def __str__(self):
        return f"Summary for Conv #{self.conversation.id}"
    
    def needs_update(self):
        """Check if summary needs updating based on new messages"""
        current_count = self.conversation.messages.count()
        return current_count > self.message_count + 3  # Update every 3 new messages
    
    @classmethod
    def generate_for_conversation(cls, conversation):
        """Generate summary for a conversation using organization's LLM config"""
        try:
            llm_config = getattr(conversation.integration.organization, 'llm_config', None)
            if not llm_config:
                raise Exception("No LLM configuration found for organization")
            
            return summarize_conversation(llm_config, conversation)
            
        except Exception as e:
            logger.error(f"Failed to generate summary for conversation {conversation.id}: {str(e)}")
            raise

class WaMessage(models.Model):
    """WhatsApp Message Model - Individual messages within conversations"""
    DIRECTION_CHOICES = [('in', 'Incoming'), ('out', 'Outgoing')]
    MSG_TYPE_CHOICES = [
        ('text', 'Text'), ('image', 'Image'), ('video', 'Video'), 
        ('audio', 'Audio'), ('document', 'Document'), ('location', 'Location'),
        ('contact', 'Contact'), ('sticker', 'Sticker'), ('template', 'Template')
    ]
    
    integration = models.ForeignKey('WaIntegration', on_delete=models.CASCADE, related_name='messages')
    conversation = models.ForeignKey('WaConversation', on_delete=models.CASCADE, related_name='messages', null=True, blank=True)
    direction = models.CharField(max_length=8, choices=DIRECTION_CHOICES)
    wa_id = models.CharField(max_length=32, db_index=True, help_text="WhatsApp ID of the contact")
    msg_id = models.CharField(max_length=128, blank=True, help_text="WhatsApp message ID")
    msg_type = models.CharField(max_length=16, choices=MSG_TYPE_CHOICES, default='text')
    text = models.TextField(blank=True, help_text="Message text content")
    payload = models.JSONField(default=dict, help_text="Full message payload from WhatsApp")
    created_at = models.DateTimeField(auto_now_add=True)

    # Use organization-aware manager
    objects = WaMessageManager()

    class Meta:
        indexes = [
            models.Index(fields=['integration', 'wa_id']),
            models.Index(fields=['conversation', 'created_at']),
            models.Index(fields=['direction', 'created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"Msg #{self.id} [{self.direction}] {self.msg_type} to {self.wa_id}"


class PeriodicMessageScheduleManager(models.Manager):
    """Manager for PeriodicMessageSchedule with organization filtering"""
    
    def for_user(self, user):
        """Filter schedules by user's organizations"""
        if user.is_superuser:
            return self.all()
        
        # Get user's organizations
        from organizations.models import Organization
        user_orgs = Organization.objects.filter(users=user)
        return self.filter(organization__in=user_orgs)


class PeriodicMessageSchedule(models.Model):
    """Organization-specific periodic messaging schedule"""
    FREQUENCY_CHOICES = [
        ('minute', 'Every Minute (Testing)'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('disabled', 'Disabled'),
    ]
    
    organization = models.OneToOneField('organizations.Organization', on_delete=models.CASCADE, related_name='message_schedule')
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, default='daily', help_text="How often to send periodic messages")
    is_active = models.BooleanField(default=True, help_text="Enable/disable periodic messaging")
    last_sent = models.DateTimeField(null=True, blank=True, help_text="When the last periodic message was sent")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    objects = PeriodicMessageScheduleManager()
    
    class Meta:
        verbose_name = "Periodic Message Schedule"
        verbose_name_plural = "Periodic Message Schedules"
        ordering = ['-updated_at']
    
    def __str__(self):
        return f"{self.organization.name} - {self.get_frequency_display()}"
    
    def get_next_run_time(self):
        """Calculate next run time based on frequency"""
        from django.utils import timezone
        from datetime import timedelta
        
        if not self.is_active or self.frequency == 'disabled':
            return None
            
        if not self.last_sent:
            return timezone.now()
            
        if self.frequency == 'minute':
            return self.last_sent + timedelta(minutes=1)
        elif self.frequency == 'daily':
            return self.last_sent + timedelta(days=1)
        elif self.frequency == 'weekly':
            return self.last_sent + timedelta(weeks=1)
        elif self.frequency == 'monthly':
            return self.last_sent + timedelta(days=30)
        
        return None
