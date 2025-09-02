from django.contrib import admin
from django.contrib import messages
from django.shortcuts import redirect
from django.conf import settings
from django import forms
from django.utils import timezone
from organizations.models import Organization, OrganizationUser
import logging
from .models import WaIntegration, WaMessage, WaConversation
from .crypto import enc, dec
from .services import set_webhook_sandbox, send_text_sandbox, send_template_sandbox
from .utils import normalize_msisdn, digits_only

logger = logging.getLogger(__name__)

# ============================================================================
# FORMS
# ============================================================================

class WaIntegrationAdminForm(forms.ModelForm):
    """Custom form for WaIntegration with graceful error handling"""
    
    class Meta:
        model = WaIntegration
        fields = '__all__'
    
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
        is_valid, error_msg = validate_single_selection(queryset, "create_conversation")
        if not is_valid:
            self.message_user(request, error_msg, level=messages.WARNING)
            return
        
        integration = queryset.first()
        logger.info(f"Creating conversation for integration {integration.id}")
        
        try:
            wa_id = normalize_msisdn(integration.tester_msisdn)
            if not wa_id:
                self.message_user(request, "No valid tester phone number found.", level=messages.ERROR)
                return
            
            conv, created = WaConversation.objects.for_user(request.user).get_or_create(
                integration=integration, 
                wa_id=wa_id, 
                status='open', 
                defaults={"started_by": "admin"}
            )
            
            action = "created" if created else "found existing"
            self.message_user(request, f"Conversation #{conv.id} {action} for {wa_id}", level=messages.SUCCESS)
            
        except Exception as e:
            logger.error(f"Failed to create conversation: {str(e)}")
            self.message_user(request, f"Failed: {e}", level=messages.ERROR)
    create_conversation.short_description = "Create conversation row for tester"
    
    def update_webhook_url(self, request, queryset):
        """Update webhook URL when ngrok URL changes"""
        is_valid, error_msg = validate_single_selection(queryset, "update_webhook_url")
        if not is_valid:
            self.message_user(request, error_msg, level=messages.WARNING)
            return
        
        integration = queryset.first()
        logger.info(f"Updating webhook for integration {integration.id}")
        
        try:
            # Get API key
            api_key, error_msg = get_api_key_safely(integration, "update_webhook_url")
            if not api_key:
                self.message_user(request, error_msg, level=messages.ERROR)
                return
            
            # Get webhook URL
            webhook_url, error_msg = get_webhook_url()
            if not webhook_url:
                self.message_user(request, error_msg, level=messages.ERROR)
                return
            
            # Update webhook
            set_webhook_sandbox(api_key, webhook_url)
            
            self.message_user(
                request, 
                f"✅ Webhook URL updated successfully for {integration.organization.name}!\n"
                f"New URL: {webhook_url}",
                level=messages.SUCCESS
            )
            
        except Exception as e:
            logger.error(f"Failed to update webhook: {str(e)}")
            self.message_user(request, f"Failed to update webhook URL: {str(e)}", level=messages.ERROR)
    update_webhook_url.short_description = "Update webhook URL with current ngrok URL"
    
    def connect_sandbox(self, request, queryset):
        """Connect integration to sandbox"""
        is_valid, error_msg = validate_single_selection(queryset, "connect_sandbox")
        if not is_valid:
            self.message_user(request, error_msg, level=messages.WARNING)
            return
        
        integration = queryset.first()
        logger.info(f"Connecting sandbox for integration {integration.id}")
        
        try:
            # Validate required data
            if not integration.tester_msisdn:
                self.message_user(request, "No tester phone found. Please set tester_msisdn field first.", level=messages.ERROR)
                return
            
            # Get API key
            api_key, error_msg = get_api_key_safely(integration, "connect_sandbox")
            if not api_key:
                self.message_user(request, error_msg, level=messages.ERROR)
                return
            
            # Get webhook URL
            webhook_url, error_msg = get_webhook_url()
            if not webhook_url:
                self.message_user(request, error_msg, level=messages.ERROR)
                return
            
            # Set webhook
            set_webhook_sandbox(api_key, webhook_url)
            
            self.message_user(request, f"Integration connected and webhook set for {integration.organization.name}!")
            
        except Exception as e:
            logger.error(f"Failed to connect sandbox: {str(e)}")
            if "401 UNAUTHORIZED" in str(e) or "API key validation failed" in str(e):
                error_msg = (
                    f"❌ Failed to connect integration: {str(e)}\n\n"
                    "🔧 Troubleshooting Steps:\n"
                    "1. Check if your 360dialog API key is correct\n"
                    "2. Verify the API key hasn't expired\n"
                    "3. Ensure you're using the sandbox API key\n"
                    "4. Check API key permissions\n"
                    "5. Try regenerating a new API key"
                )
                self.message_user(request, error_msg, level=messages.ERROR)
            else:
                self.message_user(request, f"Failed to connect integration: {str(e)}", level=messages.ERROR)
    connect_sandbox.short_description = "Connect selected integration to sandbox"
    
    def send_message(self, request, queryset):
        """Send test message"""
        is_valid, error_msg = validate_single_selection(queryset, "send_message")
        if not is_valid:
            self.message_user(request, error_msg, level=messages.WARNING)
            return
        
        integration = queryset.first()
        logger.info(f"Sending test message for integration {integration.id}")
        
        try:
            # Validate required data
            if not integration.tester_msisdn:
                self.message_user(request, "No tester phone found. Please set tester_msisdn field first.", level=messages.ERROR)
                return
            
            # Get API key
            api_key, error_msg = get_api_key_safely(integration, "send_message")
            if not api_key:
                self.message_user(request, error_msg, level=messages.ERROR)
                return
            
            # Prepare message
            to_phone = integration.tester_msisdn
            from datetime import datetime
            import time
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            unique_id = int(time.time() * 1000) % 10000
            message_text = f"Hello from Django Admin! Test message #{unique_id} sent at {timestamp}"
            
            # Send message
            response = send_text_sandbox(api_key, to_phone, message_text)
            
            # Store message
            to_phone = normalize_msisdn(to_phone)
            conv = (WaConversation.objects.for_user(request.user)
                    .filter(integration=integration, wa_id=to_phone, status='open')
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
                    messages = response.get("messages", [])
                    if messages and isinstance(messages, list) and len(messages) > 0:
                        msg_id = str(messages[0].get("id", ""))
            except Exception:
                import uuid
                msg_id = f"admin_{uuid.uuid4().hex[:16]}"
            
            message, error_msg = create_message_record(
                integration, conv, 'out', to_phone, msg_id, "text", message_text, response
            )
            if not message:
                self.message_user(request, f"Message sent but {error_msg}", level=messages.ERROR)
                return
            
            self.message_user(request, f"Message sent successfully to {to_phone}!")
            
        except Exception as e:
            logger.error(f"Failed to send message: {str(e)}")
            self.message_user(request, f"Failed to send message: {str(e)}", level=messages.ERROR)
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
    actions = ['start_with_template', 'send_text', 'end_conversation']
    
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
        is_valid, error_msg = validate_single_selection(queryset, "start_with_template")
        if not is_valid:
            self.message_user(request, error_msg, level=messages.WARNING)
            return
        
        conv = queryset.first()
        integ = conv.integration
        logger.info(f"Starting template conversation {conv.id}")
        
        try:
            # Get API key
            api_key = self._get_api_key(integ)
            
            # Normalize phone number
            to_phone = normalize_msisdn(conv.wa_id or integ.tester_msisdn)
            if not to_phone:
                raise Exception("No valid phone number found")
            
            # Sandbox preflight guard
            own_number = digits_only(integ.tester_msisdn)
            dest_number = digits_only(to_phone)
            if own_number != dest_number:
                error_msg = f"Sandbox can only send to your own number ({own_number}). Selected conversation is {dest_number}."
                self.message_user(request, error_msg, level=messages.ERROR)
                return
            
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
            
            self.message_user(request, f"Template '{template_name}' sent to {to_phone}.", level=messages.SUCCESS)
            
        except Exception as e:
            logger.exception("start_with_template failed")
            self.message_user(request, f"Failed to send template: {e}", level=messages.ERROR)

    @admin.action(description="Send text (append)")
    def send_text(self, request, queryset):
        """Send text message to conversation"""
        is_valid, error_msg = validate_single_selection(queryset, "send_text")
        if not is_valid:
            self.message_user(request, error_msg, level=messages.WARNING)
            return
        
        conv = queryset.first()
        integ = conv.integration
        logger.info(f"Sending text to conversation {conv.id}")
        
        try:
            # Get API key
            api_key = self._get_api_key(integ)
            
            # Normalize phone number
            to_phone = normalize_msisdn(conv.wa_id or integ.tester_msisdn)
            if not to_phone:
                raise Exception("No valid phone number found")
            
            # Get text from request
            text = request.GET.get("text", "Hello from Admin!")
            
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
            
            self.message_user(request, f"Message sent to {to_phone}", level=messages.SUCCESS)
            
        except Exception as e:
            logger.exception("send_text failed")
            self.message_user(request, f"Failed to send text: {e}", level=messages.ERROR)

    @admin.action(description="End conversation")
    def end_conversation(self, request, queryset):
        """End conversations"""
        n = 0
        for conv in queryset:
            try:
                if conv.is_open:
                    conv.close()
                    n += 1
            except Exception as e:
                logger.error(f"Failed to close conversation {conv.id}: {str(e)}")
                continue
        
        self.message_user(request, f"Closed {n} conversation(s).", level=messages.SUCCESS)

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

# Register organization models
admin.site.unregister(Organization)
admin.site.register(Organization, OrganizationAdmin)

admin.site.unregister(OrganizationUser)
admin.site.register(OrganizationUser, OrganizationUserAdmin)

