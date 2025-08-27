from django.contrib import admin
from django.contrib import messages
from django.shortcuts import redirect
from django.conf import settings
from django import forms
import logging
from .models import WaIntegration, WaMessage
from .crypto import enc, dec
from .services import set_webhook_sandbox, send_text_sandbox

logger = logging.getLogger(__name__)

class WaIntegrationAdminForm(forms.ModelForm):
    """Custom form for WaIntegration with graceful error handling"""
    
    class Meta:
        model = WaIntegration
        fields = '__all__'
    
    def clean(self):
        """Custom validation with graceful error handling"""
        cleaned_data = super().clean()
        
        # Check if we're trying to save with a raw API key
        raw_api_key = cleaned_data.get('raw_api_key')
        api_key_encrypted = cleaned_data.get('api_key_encrypted')
        
        if raw_api_key:
            # We have a new raw API key, validate it can be encrypted
            try:
                test_encrypted = enc(raw_api_key)
                # Test that it can be decrypted
                test_decrypted = dec(test_encrypted)
                if test_decrypted != raw_api_key:
                    raise forms.ValidationError(
                        "❌ API key encryption/decryption test failed. The key cannot be properly encrypted."
                    )
            except Exception as e:
                if "Crypto not initialized" in str(e):
                    raise forms.ValidationError(
                        "❌ Encryption system not properly configured. Please check your D360_ENCRYPTION_KEY setting."
                    )
                else:
                    raise forms.ValidationError(
                        f"❌ Failed to encrypt API key: {str(e)}"
                    )
        
        elif api_key_encrypted:
            # We have an existing encrypted key, validate it can be decrypted
            try:
                test_decrypted = dec(api_key_encrypted)
                if not test_decrypted:
                    raise forms.ValidationError(
                        "❌ Existing encrypted API key cannot be decrypted. This usually means the encryption key has changed or the data is corrupted."
                    )
            except Exception as e:
                if "Crypto not initialized" in str(e):
                    raise forms.ValidationError(
                        "❌ Encryption system not properly configured. Please check your D360_ENCRYPTION_KEY setting."
                    )
                else:
                    raise forms.ValidationError(
                        f"❌ Existing encrypted API key is invalid: {str(e)}"
                    )
        
        return cleaned_data

@admin.register(WaIntegration)
class WaIntegrationAdmin(admin.ModelAdmin):
    form = WaIntegrationAdminForm
    list_display = ['organization', 'mode', 'tester_msisdn', 'masked_api_key', 'api_key_status', 'message_count', 'created_at']
    list_filter = ['mode', 'created_at']
    search_fields = ['organization__name', 'tester_msisdn']
    fieldsets = (
        ('Basic Information', {
            'fields': ('organization', 'mode', 'tester_msisdn')
        }),
        ('API Key Management', {
            'fields': ('raw_api_key', 'masked_api_key'),
            'description': 'Enter your raw API key in the field above. It will be automatically encrypted and stored securely. The masked version shows the current status.',
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def get_readonly_fields(self, request, obj=None):
        """Make fields readonly based on context"""
        if obj:  # Editing existing object
            return ['created_at', 'updated_at', 'masked_api_key']
        else:  # Creating new object
            return ['created_at', 'updated_at', 'masked_api_key']
    
    ordering = ['-created_at']
    
    actions = ['connect_sandbox', 'send_message', 'update_webhook_url']
    
    def update_webhook_url(self, request, queryset):
        """Admin action to update webhook URL when ngrok URL changes"""
        logger.info("=== UPDATE WEBHOOK URL ACTION STARTED ===")
        
        if queryset.count() != 1:
            logger.warning("Multiple integrations selected, need exactly one")
            self.message_user(request, "Please select exactly one integration")
            return
        
        integration = queryset.first()
        logger.info(f"Processing integration ID: {integration.id} for org: {integration.organization.name}")
        
        try:
            # Check if we have the required data
            logger.info("Step 1: Checking required data...")
            
            if not integration.has_api_key:
                logger.error("No API key found in integration")
                self.message_user(request, "No API key found. Please set raw_api_key field first.", level=messages.ERROR)
                return
            logger.info("✓ API key field has data")
            
            # Get decrypted API key
            logger.info("Step 2: Getting decrypted API key...")
            try:
                api_key = integration.get_api_key()
                if not api_key:
                    logger.error("Failed to decrypt API key")
                    self.message_user(request, "Failed to decrypt API key. Please check your encryption key.", level=messages.ERROR)
                    return
                logger.info("✓ API key retrieved successfully")
            except Exception as decrypt_error:
                logger.error(f"Failed to get API key: {str(decrypt_error)}")
                self.message_user(request, f"Failed to get API key: {str(decrypt_error)}", level=messages.ERROR)
                return
            
            # Get current webhook URL from settings
            logger.info("Step 3: Getting current webhook URL...")
            webhook_url = getattr(settings, 'D360_WEBHOOK_URL', None)
            if not webhook_url:
                logger.error("D360_WEBHOOK_URL not set in settings")
                self.message_user(request, "D360_WEBHOOK_URL not set in settings. Please set it first.", level=messages.ERROR)
                return
            logger.info(f"✓ Current webhook URL: {webhook_url}")
            
            # Update webhook with 360dialog
            logger.info("Step 4: Updating webhook via 360dialog API...")
            try:
                set_webhook_sandbox(api_key, webhook_url)
                logger.info("✓ Webhook updated successfully via 360dialog API")
            except Exception as webhook_error:
                logger.error(f"Failed to update webhook: {str(webhook_error)}")
                self.message_user(request, f"Failed to update webhook: {str(webhook_error)}", level=messages.ERROR)
                return
            
            logger.info("=== UPDATE WEBHOOK URL ACTION COMPLETED SUCCESSFULLY ===")
            self.message_user(
                request, 
                f"✅ Webhook URL updated successfully for {integration.organization.name}!\n\n"
                f"New URL: {webhook_url}\n\n"
                "💡 Remember to update D360_WEBHOOK_URL in your environment when ngrok URL changes.",
                level=messages.SUCCESS
            )
            
        except Exception as e:
            logger.error(f"=== UPDATE WEBHOOK URL ACTION FAILED: {str(e)} ===")
            self.message_user(request, f"Failed to update webhook URL: {str(e)}", level=messages.ERROR)
    
    update_webhook_url.short_description = "Update webhook URL with current ngrok URL"
    
    def masked_api_key(self, obj):
        """Display masked version of encrypted API key"""
        return obj.get_masked_api_key()
    masked_api_key.short_description = "API Key (Masked)"
    masked_api_key.admin_order_field = 'api_key_encrypted'
    
    def api_key_status(self, obj):
        """Show API key validation status with color coding"""
        if not obj.api_key_encrypted:
            return "❌ No Key"
        
        try:
            # Test if we can decrypt the key
            test_key = obj.get_api_key()
            if test_key:
                return "✅ Valid"
            else:
                return "❌ Invalid"
        except Exception:
            return "❌ Error"
    
    api_key_status.short_description = "Status"
    api_key_status.admin_order_field = 'api_key_encrypted'
    
    def message_count(self, obj):
        """Show count of messages for this integration"""
        count = obj.messages.count()
        return f"{count} message{'s' if count != 1 else ''}"
    
    message_count.short_description = "Messages"
    message_count.admin_order_field = 'messages__count'
    
    def save_model(self, request, obj, form, change):
        """Custom save method with logging and graceful error handling"""
        logger.info("=== ADMIN SAVE MODEL STARTED ===")
        
        try:
            # Call the model's save method
            super().save_model(request, obj, form, change)
            logger.info("=== ADMIN SAVE MODEL COMPLETED ===")
            
            # Show success message
            if change:
                if form.cleaned_data.get('raw_api_key'):
                    self.message_user(
                        request, 
                        "✅ Integration updated successfully! API key has been encrypted and stored securely.", 
                        level=messages.SUCCESS
                    )
                else:
                    self.message_user(
                        request, 
                        "✅ Integration updated successfully!", 
                        level=messages.SUCCESS
                    )
            else:
                if form.cleaned_data.get('raw_api_key'):
                    self.message_user(
                        request, 
                        "✅ Integration created successfully! API key has been encrypted and stored securely.", 
                        level=messages.SUCCESS
                    )
                else:
                    self.message_user(
                        request, 
                        "✅ Integration created successfully!", 
                        level=messages.SUCCESS
                    )
            
        except Exception as e:
            logger.error(f"=== ADMIN SAVE MODEL FAILED: {str(e)} ===")
            
            # Show user-friendly error message
            if "Encrypted API key validation failed" in str(e):
                self.message_user(
                    request, 
                    "❌ API key validation failed. The encrypted key cannot be decrypted. This usually means the encryption key has changed or the data is corrupted. Please re-enter the API key.", 
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
            
            # Re-raise the exception to prevent the save
            raise
    
    def connect_sandbox(self, request, queryset):
        """Admin action to connect sandbox"""
        logger.info("=== CONNECT SANDBOX ACTION STARTED ===")
        
        if queryset.count() != 1:
            logger.warning("Multiple integrations selected, need exactly one")
            self.message_user(request, "Please select exactly one integration")
            return
        
        integration = queryset.first()
        logger.info(f"Processing integration ID: {integration.id} for org: {integration.organization.name}")
        
        try:
            # Check if we have the required data
            logger.info("Step 1: Checking required data...")
            
            if not integration.has_api_key:
                logger.error("No API key found in integration")
                self.message_user(request, "No API key found. Please set raw_api_key field first.", level=messages.ERROR)
                return
            logger.info("✓ API key field has data")
            
            if not integration.tester_msisdn:
                logger.error("No tester phone found in integration")
                self.message_user(request, "No tester phone found. Please set tester_msisdn field first.", level=messages.ERROR)
                return
            logger.info(f"✓ Tester phone: {integration.tester_msisdn}")
            
            # Get decrypted API key using the model method
            logger.info("Step 2: Getting decrypted API key...")
            try:
                api_key = integration.get_api_key()
                if not api_key:
                    logger.error("Failed to decrypt API key")
                    self.message_user(request, "Failed to decrypt API key. Please check your encryption key.", level=messages.ERROR)
                    return
                logger.info("✓ API key retrieved successfully")
            except Exception as decrypt_error:
                logger.error(f"Failed to get API key: {str(decrypt_error)}")
                self.message_user(request, f"Failed to get API key: {str(decrypt_error)}", level=messages.ERROR)
                return
            
            # Set webhook for this integration
            logger.info("Step 3: Setting webhook...")
            webhook_url = getattr(settings, 'D360_WEBHOOK_URL', None)
            if not webhook_url:
                logger.error("D360_WEBHOOK_URL not set in settings")
                self.message_user(request, "D360_WEBHOOK_URL not set in settings. Please set it first.", level=messages.ERROR)
                return
            logger.info(f"✓ Webhook URL: {webhook_url}")
            
            # Set webhook for this specific integration
            logger.info("Step 4: Calling 360dialog API to set webhook...")
            try:
                set_webhook_sandbox(api_key, webhook_url)
                logger.info("✓ Webhook set successfully via 360dialog API")
            except Exception as webhook_error:
                logger.error(f"Failed to set webhook via API: {str(webhook_error)}")
                self.message_user(request, f"Failed to set webhook: {str(webhook_error)}", level=messages.ERROR)
                return
            
            logger.info("=== CONNECT SANDBOX ACTION COMPLETED SUCCESSFULLY ===")
            self.message_user(request, f"Integration connected and webhook set for {integration.organization.name}!")
            
        except Exception as e:
            logger.error(f"=== CONNECT SANDBOX ACTION FAILED: {str(e)} ===")
            
            # Provide helpful troubleshooting information
            if "401 UNAUTHORIZED" in str(e) or "API key validation failed" in str(e):
                error_msg = (
                    f"❌ Failed to connect integration: {str(e)}\n\n"
                    "🔧 Troubleshooting Steps:\n"
                    "1. Check if your 360dialog API key is correct\n"
                    "2. Verify the API key hasn't expired\n"
                    "3. Ensure you're using the sandbox API key (not production)\n"
                    "4. Check if the API key has webhook configuration permissions\n"
                    "5. Try regenerating a new API key in 360dialog dashboard"
                )
                self.message_user(request, error_msg, level=messages.ERROR)
            else:
                self.message_user(request, f"Failed to connect integration: {str(e)}", level=messages.ERROR)
    
    connect_sandbox.short_description = "Connect selected integration to sandbox"
    
    def send_message(self, request, queryset):
        """Admin action to send message"""
        logger.info("=== SEND MESSAGE ACTION STARTED ===")
        
        if queryset.count() != 1:
            logger.warning("Multiple integrations selected, need exactly one")
            self.message_user(request, "Please select exactly one integration")
            return
        
        integration = queryset.first()
        logger.info(f"Processing integration ID: {integration.id} for org: {integration.organization.name}")
        
        try:
            # Check if we have the required data
            logger.info("Step 1: Checking required data...")
            
            if not integration.has_api_key:
                logger.error("No API key found in integration")
                self.message_user(request, "No API key found. Please set raw_api_key field first.", level=messages.ERROR)
                return
            logger.info("✓ API key field has data")
            
            if not integration.tester_msisdn:
                logger.error("No tester phone found in integration")
                self.message_user(request, "No tester phone found. Please set tester_msisdn field first.", level=messages.ERROR)
                return
            logger.info(f"✓ Tester phone: {integration.tester_msisdn}")
            
            # Get decrypted API key using the model method
            logger.info("Step 2: Getting decrypted API key...")
            try:
                api_key = integration.get_api_key()
                if not api_key:
                    logger.error("Failed to decrypt API key")
                    self.message_user(request, "Failed to decrypt API key. Please check your encryption key.", level=messages.ERROR)
                    return
                logger.info(f"✓ API key retrieved successfully, length: {len(api_key)}")
            except Exception as decrypt_error:
                logger.error(f"Failed to get API key: {str(decrypt_error)}")
                self.message_user(request, f"Failed to get API key: {str(decrypt_error)}", level=messages.ERROR)
                return
            
            # Prepare message
            logger.info("Step 3: Preparing message...")
            to_phone = integration.tester_msisdn
            
            # Generate unique message text with timestamp and counter
            from datetime import datetime
            import time
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            unique_id = int(time.time() * 1000) % 10000  # Last 4 digits of timestamp
            message_text = f"Hello from Django Admin! Test message #{unique_id} sent at {timestamp}"
            
            logger.info(f"✓ Message prepared: To: {to_phone}, Text: {message_text[:50]}...")
            
            # Send message
            logger.info("Step 4: Sending message via 360dialog API...")
            try:
                response = send_text_sandbox(api_key, to_phone, message_text)
                logger.info(f"✓ Message sent successfully, API response: {response}")
            except Exception as send_error:
                logger.error(f"Failed to send message via API: {str(send_error)}")
                self.message_user(request, f"Failed to send message: {str(send_error)}", level=messages.ERROR)
                return
            
            # Store message
            logger.info("Step 5: Storing message in database...")
            try:
                # Extract message ID from response, with fallback
                msg_id = ""
                try:
                    if response and isinstance(response, dict):
                        messages = response.get("messages", [])
                        if messages and isinstance(messages, list) and len(messages) > 0:
                            msg_id = str(messages[0].get("id", ""))
                        else:
                            # Fallback: generate a unique identifier
                            import uuid
                            msg_id = f"admin_{uuid.uuid4().hex[:16]}"
                except Exception as id_error:
                    logger.warning(f"Failed to extract msg_id from response: {str(id_error)}")
                    import uuid
                    msg_id = f"admin_{uuid.uuid4().hex[:16]}"
                
                message = WaMessage.objects.create(
                    integration=integration,
                    direction='out',
                    wa_id=to_phone,
                    msg_id=msg_id,
                    msg_type="text",
                    text=message_text,
                    payload=response
                )
                
                logger.info(f"✓ Message stored in database with ID: {message.id}")
                
            except Exception as db_error:
                logger.error(f"Failed to store message in database: {str(db_error)}")
                self.message_user(request, f"Message sent but failed to store in database: {str(db_error)}", level=messages.ERROR)
                return
            
            logger.info("=== SEND MESSAGE ACTION COMPLETED SUCCESSFULLY ===")
            self.message_user(request, f"Message sent successfully to {to_phone}!")
            
        except Exception as e:
            logger.error(f"=== SEND MESSAGE ACTION FAILED: {str(e)} ===")
            self.message_user(request, f"Failed to send message: {str(e)}", level=messages.ERROR)
    
    send_message.short_description = "Send test message via selected integration"

    class Media:
        css = {
            'all': ('admin/css/custom.css',)
        }
    
    def changelist_view(self, request, extra_context=None):
        """Add custom CSS for status indicators"""
        extra_context = extra_context or {}
        extra_context['custom_css'] = """
        <style>
        .field-api_key_status .status-valid { color: #28a745; font-weight: bold; }
        .field-api_key_status .status-invalid { color: #dc3545; font-weight: bold; }
        .field-api_key_status .status-error { color: #ffc107; font-weight: bold; }
        .field-api_key_status .status-no-key { color: #6c757d; font-weight: bold; }
        </style>
        """
        return super().changelist_view(request, extra_context)

@admin.register(WaMessage)
class WaMessageAdmin(admin.ModelAdmin):
    list_display = ['direction', 'wa_id', 'msg_type', 'integration', 'created_at']
    list_filter = ['direction', 'msg_type', 'created_at', 'integration__mode']
    search_fields = ['wa_id', 'text', 'integration__organization__name']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
