from django.contrib import admin
from django.contrib import messages
from django.shortcuts import redirect
from django.conf import settings
from django import forms
from django.utils import timezone
from organizations.models import Organization, OrganizationUser
import logging
from .models import WaIntegration, WaMessage, WaConversation, LLMConfiguration, ConversationSummary, PeriodicMessageSchedule
from .crypto import enc, dec
from .services import set_webhook_sandbox, send_text_sandbox, send_template_sandbox
from .utils import normalize_msisdn, digits_only, summarize_conversation


logger = logging.getLogger(__name__)

# ============================================================================
# FORMS
# ============================================================================

class WaIntegrationAdminForm(forms.ModelForm):
    """Custom form for WaIntegration with graceful error handling"""
    
    class Meta:
        model = WaIntegration
        fields = '__all__'
        widgets = {
            'client_context': forms.Textarea(attrs={'rows': 4, 'cols': 80}),
            'project_context': forms.Textarea(attrs={'rows': 4, 'cols': 80}),
            'custom_instructions': forms.Textarea(attrs={'rows': 4, 'cols': 80}),
        }
    
    def clean(self):
        """Custom validation with graceful error handling"""
        cleaned_data = super().clean()
        
        raw_api_key = cleaned_data.get('raw_api_key')
        api_key_encrypted = cleaned_data.get('api_key_encrypted')
        
        if raw_api_key:
            try:
                test_encrypted = enc(raw_api_key)
                test_decrypted = dec(test_encrypted)
                if test_decrypted != raw_api_key:
                    raise forms.ValidationError("❌ API key encryption/decryption test failed.")
            except Exception as e:
                if "Crypto not initialized" in str(e):
                    raise forms.ValidationError("❌ Encryption system not properly configured.")
                else:
                    raise forms.ValidationError(f"❌ Failed to encrypt API key: {str(e)}")
        
        elif api_key_encrypted:
            try:
                test_decrypted = dec(api_key_encrypted)
                if not test_decrypted:
                    raise forms.ValidationError("❌ Existing encrypted API key cannot be decrypted.")
            except Exception as e:
                if "Crypto not initialized" in str(e):
                    raise forms.ValidationError("❌ Encryption system not properly configured.")
                else:
                    raise forms.ValidationError(f"❌ Existing encrypted API key is invalid: {str(e)}")
        
        return cleaned_data

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def validate_single_selection(queryset, action_name):
    """Validate that exactly one item is selected for admin actions"""
    if queryset.count() != 1:
        logger.warning(f"Multiple items selected for {action_name}, need exactly one")
        return False, "Please select exactly one item."
    return True, None

def get_api_key_safely(integration, action_name):
    """Safely get decrypted API key with error handling"""
    if not integration.has_api_key:
        return None, "No API key found. Please set raw_api_key field first."
    
    try:
        api_key = integration.get_api_key()
        if not api_key:
            return None, "Failed to decrypt API key. Please check your encryption key."
        return api_key, None
    except Exception as e:
        logger.error(f"Failed to get API key for {action_name}: {str(e)}")
        return None, f"Failed to get API key: {str(e)}"

def get_webhook_url():
    """Get webhook URL from settings"""
    webhook_url = getattr(settings, 'D360_WEBHOOK_URL', None)
    if not webhook_url:
        return None, "D360_WEBHOOK_URL not set in settings. Please set it first."
    return webhook_url, None

def create_message_record(integration, conversation, direction, wa_id, msg_id, msg_type, text, payload):
    """Create message record with conversation update"""
    try:
        message = WaMessage.objects.create(
            integration=integration,
            conversation=conversation,
            direction=direction,
            wa_id=wa_id,
            msg_id=msg_id,
            msg_type=msg_type,
            text=text,
            payload=payload
        )
        
        # Update conversation timestamp
        conversation.last_msg_at = timezone.now()
        conversation.save(update_fields=['last_msg_at'])
        
        return message, None
    except Exception as e:
        logger.error(f"Failed to create message record: {str(e)}")
        return None, f"Failed to store message: {str(e)}"

# ============================================================================
# WAINTEGRATION ADMIN
# ============================================================================

@admin.register(WaIntegration)
class WaIntegrationAdmin(admin.ModelAdmin):
    form = WaIntegrationAdminForm
    list_display = ['organization', 'mode', 'tester_msisdn', 'masked_api_key', 'api_key_status', 'message_count', 'created_at']
    list_filter = ['mode', 'created_at']
    search_fields = ['organization__name', 'tester_msisdn']
    ordering = ['-created_at']
    actions = ['connect_sandbox', 'send_message', 'update_webhook_url', 'create_conversation']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('organization', 'mode', 'tester_msisdn')
        }),
        ('API Key Management', {
            'fields': ('raw_api_key', 'masked_api_key'),
            'description': 'Enter your raw API key in the field above. It will be automatically encrypted and stored securely.',
            'classes': ('collapse',)
        }),
        ('AI Context Settings', {
            'fields': ('client_context', 'project_context', 'custom_instructions'),
            'description': 'These context fields personalize AI responses for this specific WhatsApp number/integration.',
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def get_queryset(self, request):
        """Use organization-aware manager"""
        return self.model.objects.for_user(request.user)
    
    def get_form(self, request, obj=None, **kwargs):
        """Filter organization field choices for staff users"""
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            # Filter organization choices to only show user's organizations
            user_orgs = Organization.objects.filter(users=request.user)
            form.base_fields['organization'].queryset = user_orgs
        return form
    
    def get_readonly_fields(self, request, obj=None):
        """Make fields readonly based on context"""
        return ['created_at', 'updated_at', 'masked_api_key']
    
    # Display methods
    def masked_api_key(self, obj):
        return obj.get_masked_api_key()
    masked_api_key.short_description = "API Key (Masked)"
    masked_api_key.admin_order_field = 'api_key_encrypted'
    
    def api_key_status(self, obj):
        if not obj.api_key_encrypted:
            return "❌ No Key"
        try:
            test_key = obj.get_api_key()
            return "✅ Valid" if test_key else "❌ Invalid"
        except Exception:
            return "❌ Error"
    api_key_status.short_description = "Status"
    api_key_status.admin_order_field = 'api_key_encrypted'
    
    def message_count(self, obj):
        count = obj.messages.count()
        return f"{count} message{'s' if count != 1 else ''}"
    message_count.short_description = "Messages"
    message_count.admin_order_field = 'messages__count'
    
    # Admin actions
    def create_conversation(self, request, queryset):
        """Create conversation row for tester"""
        success_count = 0
        error_count = 0
        
        for integration in queryset:
            logger.info(f"Creating conversation for integration {integration.id}")
            
            try:
                wa_id = normalize_msisdn(integration.tester_msisdn)
                if not wa_id:
                    self.message_user(request, f"❌ {integration.organization.name}: No valid tester phone number found.", level=messages.WARNING)
                    error_count += 1
                    continue
                
                conv, created = WaConversation.objects.for_user(request.user).get_or_create(
                    integration=integration, 
                    wa_id=wa_id, 
                    status__in=['open', 'continue', 'schedule_later', 'evaluating'], 
                    defaults={"started_by": "admin", "status": "open"}
                )
                
                action = "created" if created else "found existing"
                self.message_user(request, f"✅ {integration.organization.name}: Conversation #{conv.id} {action} for {wa_id}", level=messages.SUCCESS)
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to create conversation for {integration.id}: {str(e)}")
                self.message_user(request, f"❌ {integration.organization.name}: Failed - {e}", level=messages.ERROR)
                error_count += 1
        
        # Summary message
        if success_count > 0:
            self.message_user(request, f"✅ Successfully processed {success_count} integration(s)", level=messages.SUCCESS)
        if error_count > 0:
            self.message_user(request, f"❌ Failed to process {error_count} integration(s)", level=messages.WARNING)
    create_conversation.short_description = "Create conversation row for tester"
    
    def update_webhook_url(self, request, queryset):
        """Update webhook URL when ngrok URL changes"""
        # Get webhook URL once (same for all integrations)
        webhook_url, error_msg = get_webhook_url()
        if not webhook_url:
            self.message_user(request, error_msg, level=messages.ERROR)
            return
        
        success_count = 0
        error_count = 0
        
        for integration in queryset:
            logger.info(f"Updating webhook for integration {integration.id}")
            
            try:
                # Get API key
                api_key, error_msg = get_api_key_safely(integration, "update_webhook_url")
                if not api_key:
                    self.message_user(request, f"❌ {integration.organization.name}: {error_msg}", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # Update webhook
                set_webhook_sandbox(api_key, webhook_url)
                
                self.message_user(
                    request, 
                    f"✅ {integration.organization.name}: Webhook URL updated successfully!",
                    level=messages.SUCCESS
                )
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to update webhook for {integration.id}: {str(e)}")
                self.message_user(request, f"❌ {integration.organization.name}: Failed to update webhook - {str(e)}", level=messages.ERROR)
                error_count += 1
        
        # Summary message
        if success_count > 0:
            self.message_user(request, f"✅ Successfully updated webhook for {success_count} integration(s) to: {webhook_url}", level=messages.SUCCESS)
        if error_count > 0:
            self.message_user(request, f"❌ Failed to update {error_count} integration(s)", level=messages.WARNING)
    update_webhook_url.short_description = "Update webhook URL with current ngrok URL"
    
    def connect_sandbox(self, request, queryset):
        """Connect integration to sandbox"""
        # Get webhook URL once (same for all integrations)
        webhook_url, error_msg = get_webhook_url()
        if not webhook_url:
            self.message_user(request, error_msg, level=messages.ERROR)
            return
        
        success_count = 0
        error_count = 0
        
        for integration in queryset:
            logger.info(f"Connecting sandbox for integration {integration.id}")
            
            try:
                # Validate required data
                if not integration.tester_msisdn:
                    self.message_user(request, f"❌ {integration.organization.name}: No tester phone found. Please set tester_msisdn field first.", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # Get API key
                api_key, error_msg = get_api_key_safely(integration, "connect_sandbox")
                if not api_key:
                    self.message_user(request, f"❌ {integration.organization.name}: {error_msg}", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # Set webhook
                set_webhook_sandbox(api_key, webhook_url)
                
                self.message_user(request, f"✅ {integration.organization.name}: Integration connected and webhook set!", level=messages.SUCCESS)
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to connect sandbox for {integration.id}: {str(e)}")
                if "401 UNAUTHORIZED" in str(e) or "API key validation failed" in str(e):
                    error_msg = (
                        f"❌ {integration.organization.name}: Failed to connect - {str(e)}\n"
                        "🔧 Check: API key correct, not expired, sandbox key, has permissions"
                    )
                    self.message_user(request, error_msg, level=messages.ERROR)
                else:
                    self.message_user(request, f"❌ {integration.organization.name}: Failed to connect - {str(e)}", level=messages.ERROR)
                error_count += 1
        
        # Summary message
        if success_count > 0:
            self.message_user(request, f"✅ Successfully connected {success_count} integration(s) to sandbox", level=messages.SUCCESS)
        if error_count > 0:
            self.message_user(request, f"❌ Failed to connect {error_count} integration(s)", level=messages.WARNING)
    connect_sandbox.short_description = "Connect selected integration to sandbox"
    
    def send_message(self, request, queryset):
        """Send test message"""
        from datetime import datetime
        import time
        
        success_count = 0
        error_count = 0
        
        for integration in queryset:
            logger.info(f"Sending test message for integration {integration.id}")
            
            try:
                # Validate required data
                if not integration.tester_msisdn:
                    self.message_user(request, f"❌ {integration.organization.name}: No tester phone found. Please set tester_msisdn field first.", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # Get API key
                api_key, error_msg = get_api_key_safely(integration, "send_message")
                if not api_key:
                    self.message_user(request, f"❌ {integration.organization.name}: {error_msg}", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # Prepare message
                to_phone = integration.tester_msisdn
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                unique_id = int(time.time() * 1000) % 10000
                message_text = f"Hello from Django Admin! Test message #{unique_id} sent at {timestamp}"
                
                # Send message
                response = send_text_sandbox(api_key, to_phone, message_text)
                
                # Store message
                to_phone = normalize_msisdn(to_phone)
                conv = (WaConversation.objects.for_user(request.user)
                        .filter(integration=integration, wa_id=to_phone, status__in=['open', 'continue', 'schedule_later', 'evaluating'])
                        .order_by('-last_msg_at').first())
                
                if not conv:
                    conv = WaConversation.objects.create(
                        integration=integration, 
                        wa_id=to_phone, 
                        started_by="admin", 
                        status="open"
                    )
                
                # Extract message ID
                msg_id = ""
                try:
                    if response and isinstance(response, dict):
                        msg_list = response.get("messages", [])
                        if msg_list and isinstance(msg_list, list) and len(msg_list) > 0:
                            msg_id = str(msg_list[0].get("id", ""))
                except Exception:
                    import uuid
                    msg_id = f"admin_{uuid.uuid4().hex[:16]}"
                
                message, error_msg = create_message_record(
                    integration, conv, 'out', to_phone, msg_id, "text", message_text, response
                )
                if not message:
                    self.message_user(request, f"❌ {integration.organization.name}: Message sent but {error_msg}", level=messages.WARNING)
                    error_count += 1
                    continue
                
                self.message_user(request, f"✅ {integration.organization.name}: Message sent successfully to {to_phone}!", level=messages.SUCCESS)
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to send message for {integration.id}: {str(e)}")
                self.message_user(request, f"❌ {integration.organization.name}: Failed to send message - {str(e)}", level=messages.ERROR)
                error_count += 1
        
        # Summary message
        if success_count > 0:
            self.message_user(request, f"✅ Successfully sent test messages to {success_count} integration(s)", level=messages.SUCCESS)
        if error_count > 0:
            self.message_user(request, f"❌ Failed to send messages to {error_count} integration(s)", level=messages.WARNING)
    send_message.short_description = "Send test message via selected integration"
    
    
    def save_model(self, request, obj, form, change):
        """Custom save method with logging and graceful error handling"""
        try:
            super().save_model(request, obj, form, change)
            
            # Show success message
            action = "updated" if change else "created"
            has_api_key = form.cleaned_data.get('raw_api_key')
            api_key_msg = " API key has been encrypted and stored securely." if has_api_key else ""
            
            self.message_user(
                request, 
                f"✅ Integration {action} successfully!{api_key_msg}", 
                level=messages.SUCCESS
            )
            
        except Exception as e:
            logger.error(f"Admin save model failed: {str(e)}")
            
            if "Encrypted API key validation failed" in str(e):
                self.message_user(
                    request, 
                    "❌ API key validation failed. Please re-enter the API key.", 
                    level=messages.ERROR
                )
            elif "API key encryption failed" in str(e):
                self.message_user(
                    request, 
                    "❌ Failed to encrypt the API key. Please check your encryption configuration.", 
                    level=messages.ERROR
                )
            else:
                self.message_user(
                    request, 
                    f"❌ Failed to save integration: {str(e)}", 
                    level=messages.ERROR
                )
            raise

# ============================================================================
# WAMESSAGE ADMIN
# ============================================================================

@admin.register(WaMessage)
class WaMessageAdmin(admin.ModelAdmin):
    list_display = ['direction', 'wa_id', 'msg_type', 'conversation', 'integration', 'created_at']
    list_filter = ['direction', 'msg_type', 'created_at', 'integration__mode', 'conversation__status']
    search_fields = ['wa_id', 'text', 'integration__organization__name', 'conversation__wa_id']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    
    def get_queryset(self, request):
        """Use organization-aware manager"""
        return self.model.objects.for_user(request.user)

# ============================================================================
# WACONVERSATION ADMIN
# ============================================================================

@admin.register(WaConversation)
class WaConversationAdmin(admin.ModelAdmin):
    """Admin interface for WhatsApp conversations with template and messaging actions"""
    list_display = ['id', 'integration', 'wa_id', 'status', 'started_by', 'started_at', 'last_msg_at', 'message_count']
    list_filter = ['status', 'integration__mode', 'integration__organization']
    search_fields = ['wa_id', 'integration__organization__name']
    readonly_fields = ['started_at', 'last_msg_at']
    actions = ['start_with_template', 'send_text', 'end_conversation', 'generate_summary', 'ai_reply_to_clients']
    
    def get_queryset(self, request):
        """Use organization-aware manager"""
        return self.model.objects.for_user(request.user)

    def message_count(self, obj): 
        return obj.messages.count()
    message_count.short_description = "Messages"

    def _get_api_key(self, integration):
        """Get decrypted API key with error handling"""
        try:
            api_key = integration.get_api_key()
            if not api_key: 
                raise Exception("Failed to decrypt API key")
            return api_key
        except Exception as e:
            logger.error(f"Failed to get API key: {str(e)}")
            raise

    @admin.action(description="Start with template (Sandbox)")
    def start_with_template(self, request, queryset):
        """Start conversation with template message"""
        success_count = 0
        error_count = 0
        
        for conv in queryset:
            integ = conv.integration
            logger.info(f"Starting template conversation {conv.id}")
            
            try:
                # Get API key
                api_key = self._get_api_key(integ)
                
                # Normalize phone number
                to_phone = normalize_msisdn(conv.wa_id or integ.tester_msisdn)
                if not to_phone:
                    self.message_user(request, f"❌ Conversation #{conv.id}: No valid phone number found", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # Sandbox preflight guard
                own_number = digits_only(integ.tester_msisdn)
                dest_number = digits_only(to_phone)
                if own_number != dest_number:
                    self.message_user(
                        request, 
                        f"❌ Conversation #{conv.id}: Sandbox can only send to your own number ({own_number}), but selected is {dest_number}",
                        level=messages.WARNING
                    )
                    error_count += 1
                    continue
                
                # Send template
                template_name = "disclaimer"
                resp = send_template_sandbox(api_key, to_phone, template_name, components=[])
                
                # Extract message ID
                msg_id = str(resp.get("messages", [{}])[0].get("id", "")) if isinstance(resp, dict) else ""
                if not msg_id:
                    import uuid
                    msg_id = f"template_{uuid.uuid4().hex[:16]}"
                
                # Create message record
                WaMessage.objects.create(
                    integration=integ, 
                    conversation=conv, 
                    direction='out', 
                    wa_id=to_phone,
                    msg_id=msg_id, 
                    msg_type='template', 
                    text=f"[TEMPLATE] {template_name}", 
                    payload=resp
                )
                
                # Reopen conversation if closed
                if not conv.is_open:
                    conv.status = 'open'
                    conv.save(update_fields=['status', 'last_msg_at'])
                
                self.message_user(request, f"✅ Conversation #{conv.id}: Template '{template_name}' sent to {to_phone}", level=messages.SUCCESS)
                success_count += 1
                
            except Exception as e:
                logger.exception(f"start_with_template failed for conversation {conv.id}")
                self.message_user(request, f"❌ Conversation #{conv.id}: Failed to send template - {e}", level=messages.ERROR)
                error_count += 1
        
        # Summary message
        if success_count > 0:
            self.message_user(request, f"✅ Successfully sent templates to {success_count} conversation(s)", level=messages.SUCCESS)
        if error_count > 0:
            self.message_user(request, f"❌ Failed to send templates to {error_count} conversation(s)", level=messages.WARNING)

    @admin.action(description="Send text (append)")
    def send_text(self, request, queryset):
        """Send text message to conversation"""
        # Get text from request (same for all conversations)
        text = request.GET.get("text", "Hello from Admin!")
        
        success_count = 0
        error_count = 0
        
        for conv in queryset:
            integ = conv.integration
            logger.info(f"Sending text to conversation {conv.id}")
            
            try:
                # Get API key
                api_key = self._get_api_key(integ)
                
                # Normalize phone number
                to_phone = normalize_msisdn(conv.wa_id or integ.tester_msisdn)
                if not to_phone:
                    self.message_user(request, f"❌ Conversation #{conv.id}: No valid phone number found", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # Send text message
                resp = send_text_sandbox(api_key, to_phone, text)
                
                # Extract message ID
                msg_id = str(resp.get("messages", [{}])[0].get("id", "")) if isinstance(resp, dict) else ""
                if not msg_id:
                    import uuid
                    msg_id = f"text_{uuid.uuid4().hex[:16]}"
                
                # Create message record
                WaMessage.objects.create(
                    integration=integ, 
                    conversation=conv, 
                    direction='out', 
                    wa_id=to_phone,
                    msg_id=msg_id, 
                    msg_type='text', 
                    text=text, 
                    payload=resp
                )
                
                # Reopen conversation if closed
                if not conv.is_open:
                    conv.status = 'open'
                    conv.save(update_fields=['status', 'last_msg_at'])
                
                self.message_user(request, f"✅ Conversation #{conv.id}: Message sent to {to_phone}", level=messages.SUCCESS)
                success_count += 1
                
            except Exception as e:
                logger.exception(f"send_text failed for conversation {conv.id}")
                self.message_user(request, f"❌ Conversation #{conv.id}: Failed to send text - {e}", level=messages.ERROR)
                error_count += 1
        
        # Summary message
        if success_count > 0:
            self.message_user(request, f"✅ Successfully sent messages to {success_count} conversation(s)", level=messages.SUCCESS)
        if error_count > 0:
            self.message_user(request, f"❌ Failed to send messages to {error_count} conversation(s)", level=messages.WARNING)

    @admin.action(description="End conversation")
    def end_conversation(self, request, queryset):
        """End conversations"""
        success_count = 0
        skipped_count = 0
        error_count = 0
        
        for conv in queryset:
            try:
                if conv.is_open:
                    conv.close()
                    self.message_user(request, f"✅ Conversation #{conv.id}: Closed successfully", level=messages.SUCCESS)
                    success_count += 1
                else:
                    skipped_count += 1
            except Exception as e:
                logger.error(f"Failed to close conversation {conv.id}: {str(e)}")
                self.message_user(request, f"❌ Conversation #{conv.id}: Failed to close - {str(e)}", level=messages.ERROR)
                error_count += 1
        
        # Summary message
        if success_count > 0:
            self.message_user(request, f"✅ Successfully closed {success_count} conversation(s)", level=messages.SUCCESS)
        if skipped_count > 0:
            self.message_user(request, f"ℹ️ Skipped {skipped_count} already closed conversation(s)", level=messages.INFO)
        if error_count > 0:
            self.message_user(request, f"❌ Failed to close {error_count} conversation(s)", level=messages.WARNING)

    @admin.action(description="Generate AI summary")
    def generate_summary(self, request, queryset):
        """Generate AI summaries for selected conversations"""        
        success_count = 0
        error_count = 0
        
        for conv in queryset:
            try:
                # Get LLM configuration for the organization
                llm_config = getattr(conv.integration.organization, 'llm_config', None)
                if not llm_config:
                    logger.warning(f"No LLM configuration found for organization {conv.integration.organization.name}")
                    self.message_user(request, f"❌ Conversation #{conv.id}: No LLM configuration found for {conv.integration.organization.name}", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # Generate summary using utils
                summary_content = summarize_conversation(llm_config, conv)
                self.message_user(request, f"✅ Conversation #{conv.id}: Summary generated successfully", level=messages.SUCCESS)
                success_count += 1
                
                logger.info(f"Generated summary for conversation {conv.id}")
                
            except Exception as e:
                logger.error(f"Failed to generate summary for conversation {conv.id}: {str(e)}")
                self.message_user(request, f"❌ Conversation #{conv.id}: Failed to generate summary - {str(e)}", level=messages.ERROR)
                error_count += 1
        
        # Summary message
        if success_count > 0:
            self.message_user(
                request, 
                f"✅ Successfully generated summaries for {success_count} conversation(s)", 
                level=messages.SUCCESS
            )
        if error_count > 0:
            self.message_user(
                request, 
                f"❌ Failed to generate {error_count} summaries - Check LLM configuration and API keys", 
                level=messages.WARNING
            )

    @admin.action(description="AI reply to engaged clients")
    def ai_reply_to_clients(self, request, queryset):
        """Generate and send AI replies to engaged clients who sent the last message"""
        from .utils import generate_ai_reply
        from .services import send_text_sandbox
        
        success_count = 0
        skipped_count = 0
        error_count = 0
        
        for conv in queryset:
            try:
                # Only reply to engaged conversations
                if conv.status != 'continue':
                    self.message_user(request, f"ℹ️ Conversation #{conv.id}: Skipped - status is '{conv.status}', not 'continue'", level=messages.INFO)
                    skipped_count += 1
                    continue
                
                # Check if client sent the last message
                last_message = conv.messages.order_by('-created_at').first()
                if not last_message or last_message.direction != 'in':
                    self.message_user(request, f"ℹ️ Conversation #{conv.id}: Skipped - last message was not from client", level=messages.INFO)
                    skipped_count += 1
                    continue
                
                # Get LLM configuration
                llm_config = getattr(conv.integration.organization, 'llm_config', None)
                if not llm_config:
                    self.message_user(request, f"❌ Conversation #{conv.id}: No LLM configuration found", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # Generate AI reply
                ai_reply = generate_ai_reply(llm_config, conv)
                if not ai_reply:
                    self.message_user(request, f"❌ Conversation #{conv.id}: Failed to generate AI reply", level=messages.WARNING)
                    error_count += 1
                    continue
                
                # ANTI-LOOP PROTECTION: Re-check last message direction right before sending
                # This prevents race conditions when multiple tasks/actions run simultaneously
                last_msg_check = conv.messages.order_by('-created_at').first()
                if not last_msg_check or last_msg_check.direction != 'in':
                    self.message_user(request, f"ℹ️ Conversation #{conv.id}: Skipped - another task already replied", level=messages.INFO)
                    skipped_count += 1
                    continue
                
                # Send reply via WhatsApp
                api_key = conv.integration.get_api_key()
                if not api_key:
                    self.message_user(request, f"❌ Conversation #{conv.id}: No API key found for integration", level=messages.WARNING)
                    error_count += 1
                    continue
                
                response = send_text_sandbox(api_key, conv.wa_id, ai_reply)
                
                # Save message to database
                msg_id = ""
                try:
                    if response and isinstance(response, dict):
                        msg_list = response.get("messages", [])
                        if msg_list and isinstance(msg_list, list) and len(msg_list) > 0:
                            msg_id = str(msg_list[0].get("id", ""))
                except Exception:
                    import uuid
                    msg_id = f"ai_reply_{uuid.uuid4().hex[:16]}"
                
                WaMessage.objects.create(
                    integration=conv.integration,
                    conversation=conv,
                    direction='out',
                    wa_id=conv.wa_id,
                    msg_id=msg_id,
                    msg_type='text',
                    text=ai_reply,
                    payload=response
                )
                
                # Update conversation timestamp
                conv.last_msg_at = timezone.now()
                conv.save(update_fields=['last_msg_at'])
                
                self.message_user(request, f"✅ Conversation #{conv.id}: AI reply sent to {conv.wa_id}", level=messages.SUCCESS)
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to send AI reply to conversation {conv.id}: {str(e)}")
                self.message_user(request, f"❌ Conversation #{conv.id}: Failed - {str(e)}", level=messages.ERROR)
                error_count += 1
        
        # Summary message
        if success_count > 0:
            self.message_user(request, f"✅ Successfully sent AI replies to {success_count} conversation(s)", level=messages.SUCCESS)
        if skipped_count > 0:
            self.message_user(request, f"ℹ️ Skipped {skipped_count} conversation(s)", level=messages.INFO)
        if error_count > 0:
            self.message_user(request, f"❌ Failed to send replies to {error_count} conversation(s)", level=messages.WARNING)

# ============================================================================
# ORGANIZATION ADMIN
# ============================================================================

class OrganizationAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(users=request.user)

class OrganizationUserAdmin(admin.ModelAdmin):
    list_display = ("get_username", "organization")

    def get_username(self, obj):
        return obj.user.username  
    get_username.short_description = "Username"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(organization__in=Organization.objects.filter(users=request.user))
    
    def get_form(self, request, obj=None, **kwargs):
        """Filter organization field choices for staff users"""
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            # Filter organization choices to only show user's organizations
            user_orgs = Organization.objects.filter(users=request.user)
            form.base_fields['organization'].queryset = user_orgs
        return form

# ============================================================================
# LLM CONFIGURATION ADMIN
# ============================================================================

class LLMConfigurationAdminForm(forms.ModelForm):
    """Custom form for LLMConfiguration with API key validation"""
    
    class Meta:
        model = LLMConfiguration
        fields = '__all__'
    
    def clean_temperature(self):
        temp = self.cleaned_data.get('temperature')
        if temp < 0.0 or temp > 1.0:
            raise forms.ValidationError("Temperature must be between 0.0 and 1.0")
        return temp
    
    def clean_max_tokens(self):
        tokens = self.cleaned_data.get('max_tokens')
        if tokens < 1 or tokens > 4000:
            raise forms.ValidationError("Max tokens must be between 1 and 4000")
        return tokens

@admin.register(LLMConfiguration)
class LLMConfigurationAdmin(admin.ModelAdmin):
    form = LLMConfigurationAdminForm
    list_display = ['organization', 'model', 'temperature', 'max_tokens', 'api_key_status', 'updated_at']
    list_filter = ['model', 'updated_at']
    search_fields = ['organization__name']
    ordering = ['-updated_at']
    
    fieldsets = (
        ('Organization', {
            'fields': ('organization',)
        }),
        ('OpenAI Configuration', {
            'fields': ('raw_api_key', 'model', 'temperature', 'max_tokens'),
            'description': 'Enter your OpenAI API key. It will be encrypted automatically. Context settings have been moved to individual integrations.'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def get_queryset(self, request):
        """Use organization-aware manager"""
        return self.model.objects.for_user(request.user)
    
    def get_form(self, request, obj=None, **kwargs):
        """Filter organization field choices for staff users"""
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            user_orgs = Organization.objects.filter(users=request.user)
            form.base_fields['organization'].queryset = user_orgs
        return form
    
    def get_readonly_fields(self, request, obj=None):
        """Make fields readonly based on context"""
        return ['created_at', 'updated_at']
    
    def api_key_status(self, obj):
        """Show API key status"""
        if not obj.api_key_encrypted:
            return "❌ No Key"
        try:
            test_key = obj.get_api_key()
            return "✅ Valid" if test_key else "❌ Invalid"
        except Exception:
            return "❌ Error"
    api_key_status.short_description = "API Key Status"

# ============================================================================
# CONVERSATION SUMMARY ADMIN
# ============================================================================

@admin.register(ConversationSummary)
class ConversationSummaryAdmin(admin.ModelAdmin):
    """Admin interface for conversation summaries with AI evaluation insights"""
    list_display = ['conversation', 'conversation_status', 'ai_evaluation_status', 'message_count', 'needs_update_status', 'updated_at']
    list_filter = ['created_at', 'updated_at', 'conversation__status']
    search_fields = ['conversation__wa_id', 'conversation__integration__organization__name']
    readonly_fields = ['created_at', 'updated_at', 'needs_update_status', 'ai_evaluation_status']
    actions = ['regenerate_summary', 'force_evaluation']
    ordering = ['-updated_at']
    
    fieldsets = (
        ('Summary Information', {
            'fields': ('conversation', 'content', 'message_count')
        }),
        ('AI Evaluation', {
            'fields': ('ai_evaluation_status',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'needs_update_status'),
            'classes': ('collapse',)
        })
    )
    
    def get_queryset(self, request):
        """Use organization-aware manager"""
        return self.model.objects.for_user(request.user)
    
    def conversation_status(self, obj):
        """Show conversation status with emoji"""
        status_emoji = {
            'open': '🟢',
            'continue': '💬', 
            'schedule_later': '⏰',
            'evaluating': '🤖',
            'closed': '🔴'
        }
        emoji = status_emoji.get(obj.conversation.status, '❓')
        return f"{emoji} {obj.conversation.get_status_display()}"
    conversation_status.short_description = "Conv Status"
    
    def ai_evaluation_status(self, obj):
        """Extract AI evaluation status from summary content"""
        content = obj.content or ""
        
        # Extract status from new format
        if "Status: ConversationStatus.CONTINUE" in content or "Status: continue" in content:
            # Extract confidence if available
            if "Confidence:" in content:
                try:
                    confidence = content.split("Confidence:")[1].split("\n")[0].strip()
                    return f"💬 Continue (Conf: {confidence})"
                except:
                    return "💬 Continue - Client Engaged"
            return "💬 Continue - Client Engaged"
        elif "Status: ConversationStatus.SCHEDULE_LATER" in content or "Status: schedule_later" in content:
            if "Confidence:" in content:
                try:
                    confidence = content.split("Confidence:")[1].split("\n")[0].strip()
                    return f"⏰ Schedule Later (Conf: {confidence})"
                except:
                    return "⏰ Schedule Later - Client Postponed"
            return "⏰ Schedule Later - Client Postponed"
        elif "Status: ConversationStatus.CLOSE" in content or "Status: close" in content or "Status: closed" in content:
            return "🔴 Close - Client Disinterested"
        elif "[EVALUATION" in content:
            return "🤖 Evaluated (Check Details)"
        else:
            return "📝 No AI Evaluation Yet"
    ai_evaluation_status.short_description = "AI Evaluation"
    
    def needs_update_status(self, obj):
        """Show if summary needs updating"""
        return "🔄 Needs Update" if obj.needs_update() else "✅ Up to Date"
    needs_update_status.short_description = "Status"
    
    def regenerate_summary(self, request, queryset):
        """Regenerate summaries for selected conversations"""
        from .tasks import evaluate_conversation_statuses
        
        success_count = 0
        for summary in queryset:
            try:
                result = evaluate_conversation_statuses.delay(summary.conversation.integration.organization.id)
                success_count += 1
                self.message_user(
                    request, 
                    f"✅ Queued evaluation for {summary.conversation.wa_id} (Task ID: {result.id})", 
                    level=messages.SUCCESS
                )
            except Exception as e:
                self.message_user(
                    request, 
                    f"❌ Failed to queue evaluation for {summary.conversation.wa_id}: {str(e)}", 
                    level=messages.ERROR
                )
        
        if success_count > 0:
            self.message_user(
                request, 
                f"✅ Successfully queued evaluation for {success_count} conversation(s)", 
                level=messages.SUCCESS
            )
    regenerate_summary.short_description = "Regenerate AI evaluation"
    
    def force_evaluation(self, request, queryset):
        """Force immediate evaluation for selected conversations"""
        from .tasks import evaluate_conversation_statuses
        
        # Group by organization to avoid duplicate evaluations
        org_ids = set()
        for summary in queryset:
            org_ids.add(summary.conversation.integration.organization.id)
        
        success_count = 0
        for org_id in org_ids:
            try:
                result = evaluate_conversation_statuses.delay(org_id)
                success_count += 1
                self.message_user(
                    request, 
                    f"✅ Queued evaluation for organization {org_id} (Task ID: {result.id})", 
                    level=messages.SUCCESS
                )
            except Exception as e:
                self.message_user(
                    request, 
                    f"❌ Failed to queue evaluation for organization {org_id}: {str(e)}", 
                    level=messages.ERROR
                )
        
        if success_count > 0:
            self.message_user(
                request, 
                f"✅ Successfully queued evaluation for {success_count} organization(s)", 
                level=messages.SUCCESS
            )
    force_evaluation.short_description = "Force evaluation now"


@admin.register(PeriodicMessageSchedule)
class PeriodicMessageScheduleAdmin(admin.ModelAdmin):
    """Admin interface for periodic message schedules"""
    list_display = ['organization', 'frequency', 'is_active', 'last_sent', 'next_run_time', 'evaluation_status', 'created_at']
    list_filter = ['frequency', 'is_active', 'created_at']
    search_fields = ['organization__name']
    readonly_fields = ['last_sent', 'created_at', 'updated_at', 'next_run_time', 'evaluation_status']
    actions = ['send_now', 'enable_schedule', 'disable_schedule', 'set_testing_mode', 'set_daily_mode', 'evaluate_conversations', 'evaluate_all_organizations', 'reply_to_engaged_clients_now']
    
    fieldsets = (
        ('Schedule Settings', {
            'fields': ('organization', 'frequency', 'is_active')
        }),
        ('Timing Information', {
            'fields': ('last_sent', 'next_run_time', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def get_queryset(self, request):
        """Use organization-aware manager"""
        return self.model.objects.for_user(request.user)
    
    def next_run_time(self, obj):
        """Show next scheduled run time"""
        next_run = obj.get_next_run_time()
        if next_run:
            return next_run.strftime("%Y-%m-%d %H:%M")
        return "Not scheduled"
    next_run_time.short_description = "Next Run"
    
    def evaluation_status(self, obj):
        """Show conversation evaluation status for this organization"""
        from .models import WaConversation
        open_conversations = WaConversation.objects.filter(
            integration__organization=obj.organization,
            status__in=['open', 'continue', 'schedule_later', 'evaluating']
        )
        
        if not open_conversations.exists():
            return "📭 No Open Conversations"
        
        # Count by status
        status_counts = {}
        for conv in open_conversations:
            status_counts[conv.status] = status_counts.get(conv.status, 0) + 1
        
        # Format status display
        status_parts = []
        for status, count in status_counts.items():
            if status == 'open':
                status_parts.append(f"🟢 Open: {count}")
            elif status == 'continue':
                status_parts.append(f"💬 Continue: {count}")
            elif status == 'schedule_later':
                status_parts.append(f"⏰ Schedule Later: {count}")
            elif status == 'evaluating':
                status_parts.append(f"🤖 Evaluating: {count}")
        
        return " | ".join(status_parts)
    evaluation_status.short_description = "Conversation Status"
    
    def send_now(self, request, queryset):
        """Send periodic messages now for selected organizations"""
        from .tasks import send_periodic_messages
        
        success_count = 0
        for schedule in queryset:
            if schedule.is_active:
                try:
                    # Pass the organization_id as argument
                    result = send_periodic_messages.delay(schedule.organization.id)
                    success_count += 1
                    self.message_user(
                        request, 
                        f"✅ Queued periodic messages for {schedule.organization.name} (Task ID: {result.id})", 
                        level=messages.SUCCESS
                    )
                except Exception as e:
                    self.message_user(
                        request, 
                        f"❌ Failed to queue messages for {schedule.organization.name}: {str(e)}", 
                        level=messages.ERROR
                    )
        
        if success_count > 0:
            self.message_user(
                request, 
                f"✅ Successfully queued periodic messages for {success_count} organization(s)", 
                level=messages.SUCCESS
            )
    send_now.short_description = "Send periodic messages now"
    
    def enable_schedule(self, request, queryset):
        """Enable periodic messaging for selected organizations"""
        updated = queryset.update(is_active=True)
        self.message_user(
            request, 
            f"✅ Enabled periodic messaging for {updated} organization(s)", 
            level=messages.SUCCESS
        )
    enable_schedule.short_description = "Enable periodic messaging"
    
    def disable_schedule(self, request, queryset):
        """Disable periodic messaging for selected organizations"""
        updated = queryset.update(is_active=False)
        self.message_user(
            request, 
            f"✅ Disabled periodic messaging for {updated} organization(s)", 
            level=messages.SUCCESS
        )
    disable_schedule.short_description = "Disable periodic messaging"
    
    def set_testing_mode(self, request, queryset):
        """Set frequency to minute for testing"""
        updated = queryset.update(frequency='minute', is_active=True)
        self.message_user(
            request, 
            f"✅ Set {updated} organization(s) to testing mode (every minute)", 
            level=messages.SUCCESS
        )
    set_testing_mode.short_description = "Set to testing mode (every minute)"
    
    def set_daily_mode(self, request, queryset):
        """Set frequency to daily for production"""
        updated = queryset.update(frequency='daily', is_active=True)
        self.message_user(
            request, 
            f"✅ Set {updated} organization(s) to daily mode", 
            level=messages.SUCCESS
        )
    set_daily_mode.short_description = "Set to daily mode"
    
    def evaluate_conversations(self, request, queryset):
        """Evaluate conversation statuses for selected organizations"""
        from .tasks import evaluate_conversation_statuses
        
        success_count = 0
        for schedule in queryset:
            try:
                result = evaluate_conversation_statuses.delay(schedule.organization.id)
                success_count += 1
                self.message_user(
                    request, 
                    f"✅ Queued conversation evaluation for {schedule.organization.name} (Task ID: {result.id})", 
                    level=messages.SUCCESS
                )
            except Exception as e:
                self.message_user(
                    request, 
                    f"❌ Failed to queue evaluation for {schedule.organization.name}: {str(e)}", 
                    level=messages.ERROR
                )
        
        if success_count > 0:
            self.message_user(
                request, 
                f"✅ Successfully queued conversation evaluation for {success_count} organization(s)", 
                level=messages.SUCCESS
            )
    evaluate_conversations.short_description = "Evaluate conversation statuses"
    
    def evaluate_all_organizations(self, request, queryset):
        """Evaluate conversations for ALL organizations (not just selected ones)"""
        from .tasks import evaluate_conversation_statuses
        from .models import WaIntegration
        
        # Get all organizations that have integrations
        organizations = WaIntegration.objects.values_list('organization_id', flat=True).distinct()
        
        success_count = 0
        for org_id in organizations:
            try:
                result = evaluate_conversation_statuses.delay(org_id)
                success_count += 1
                self.message_user(
                    request, 
                    f"✅ Queued evaluation for organization {org_id} (Task ID: {result.id})", 
                    level=messages.SUCCESS
                )
            except Exception as e:
                self.message_user(
                    request, 
                    f"❌ Failed to queue evaluation for organization {org_id}: {str(e)}", 
                    level=messages.ERROR
                )
        
        if success_count > 0:
            self.message_user(
                request, 
                f"✅ Successfully queued conversation evaluation for {success_count} organization(s)", 
                level=messages.SUCCESS
            )
    evaluate_all_organizations.short_description = "Evaluate ALL organizations"
    
    def reply_to_engaged_clients_now(self, request, queryset):
        """Send AI replies to engaged clients for selected organizations"""
        from .tasks import reply_to_engaged_clients
        
        success_count = 0
        for schedule in queryset:
            try:
                result = reply_to_engaged_clients.delay(schedule.organization.id)
                success_count += 1
                self.message_user(
                    request, 
                    f"✅ Queued AI auto-reply for {schedule.organization.name} (Task ID: {result.id})", 
                    level=messages.SUCCESS
                )
            except Exception as e:
                self.message_user(
                    request, 
                    f"❌ Failed to queue AI replies for {schedule.organization.name}: {str(e)}", 
                    level=messages.ERROR
                )
        
        if success_count > 0:
            self.message_user(
                request, 
                f"✅ Successfully queued AI replies for {success_count} organization(s)", 
                level=messages.SUCCESS
            )
    reply_to_engaged_clients_now.short_description = "Send AI replies to engaged clients now"

# Register organization models
admin.site.unregister(Organization)
admin.site.register(Organization, OrganizationAdmin)

admin.site.unregister(OrganizationUser)
admin.site.register(OrganizationUser, OrganizationUserAdmin)

