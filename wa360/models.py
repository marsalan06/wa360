"""
WhatsApp 360dialog Integration Models
"""
import logging
from django.db import models
from organizations.models import Organization
from .crypto import enc, dec

logger = logging.getLogger(__name__)

class WaIntegration(models.Model):
    """WhatsApp Integration Model"""
    MODE_CHOICES = [('sandbox', 'Sandbox'), ('prod', 'Production')]
    
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="wa_integrations")
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="sandbox")
    raw_api_key = models.CharField(max_length=200, blank=True, help_text="Raw API key (will be encrypted automatically)")
    api_key_encrypted = models.TextField(blank=True, help_text="Encrypted API key (auto-generated)")
    tester_msisdn = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['organization', 'mode']
        indexes = [models.Index(fields=['organization', 'mode'])]
    
    def __str__(self):
        return f"{self.organization.name} - {self.mode} WhatsApp Integration"
    
    def save(self, *args, **kwargs):
        """Override save to automatically encrypt API key"""
        try:
            logger.info(f"=== SAVE METHOD STARTED for integration {self.id or 'NEW'} ===")
            
            # If raw_api_key is provided, encrypt it
            if self.raw_api_key:
                logger.info("Step 1: Raw API key found, proceeding to encrypt...")
                try:
                    encrypted_key = enc(self.raw_api_key)
                    logger.info("✓ Encryption successful")
                    
                    self.api_key_encrypted = encrypted_key
                    logger.info("✓ Encrypted key assigned to api_key_encrypted field")
                    
                    # Clear the raw key for security
                    self.raw_api_key = ""
                    logger.info("✓ Raw API key cleared for security")
                    
                except Exception as encrypt_error:
                    logger.error(f"Encryption failed: {str(encrypt_error)}")
                    raise Exception(f"API key encryption failed: {str(encrypt_error)}")
            else:
                logger.info("No raw API key provided")
            
            # Validate that encrypted key can be decrypted (if present)
            if self.api_key_encrypted:
                logger.info("Step 1.5: Validating encrypted key can be decrypted...")
                try:
                    test_decrypt = dec(self.api_key_encrypted)
                    logger.info("✓ Validation successful: encrypted key can be decrypted")
                except Exception as decrypt_error:
                    logger.error(f"Validation failed: encrypted key cannot be decrypted: {str(decrypt_error)}")
                    raise Exception(f"Encrypted API key validation failed: {str(decrypt_error)}")
            
            logger.info("Step 2: Calling parent save method...")
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

class WaConversation(models.Model):
    """WhatsApp Conversation Model - Groups messages between a contact and integration"""
    STATUS_CHOICES = [('open', 'Open'), ('closed', 'Closed')]
    
    integration = models.ForeignKey('WaIntegration', on_delete=models.CASCADE, related_name='conversations')
    wa_id = models.CharField(max_length=32, db_index=True, help_text="WhatsApp ID of the contact")
    started_by = models.CharField(max_length=32, blank=True, default="admin", 
                                help_text="Who started the conversation: admin|contact|system")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='open')
    started_at = models.DateTimeField(auto_now_add=True)
    last_msg_at = models.DateTimeField(auto_now=True)

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
        """Check if conversation is currently open"""
        return self.status == 'open'

    def close(self):
        """Close the conversation and update timestamp"""
        logger.info(f"Closing conversation {self.id} for {self.wa_id}")
        self.status = 'closed'
        self.save(update_fields=['status', 'last_msg_at'])
        logger.info(f"✓ Conversation {self.id} closed successfully")

class WaMessage(models.Model):
    """WhatsApp Message Model - Individual messages within conversations"""
    DIRECTION_CHOICES = [('in', 'Incoming'), ('out', 'Outgoing')]
    MSG_TYPE_CHOICES = [('text', 'Text'), ('image', 'Image'), ('audio', 'Audio'), 
                        ('video', 'Video'), ('template', 'Template')]
    
    integration = models.ForeignKey(WaIntegration, on_delete=models.CASCADE, related_name="messages")
    conversation = models.ForeignKey('WaConversation', on_delete=models.SET_NULL, null=True, 
                                   blank=True, related_name='messages', 
                                   help_text="Conversation this message belongs to")
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES)
    wa_id = models.CharField(max_length=32, help_text="WhatsApp ID of sender/recipient")
    msg_id = models.CharField(max_length=100, blank=True, default="", 
                            help_text="Unique message ID from WhatsApp")
    msg_type = models.CharField(max_length=24, choices=MSG_TYPE_CHOICES, default="text")
    text = models.TextField(blank=True, default="", help_text="Message text content")
    payload = models.JSONField(default=dict, blank=True, help_text="Full message payload from WhatsApp")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['integration', 'created_at']),
            models.Index(fields=['conversation', 'created_at'])
        ]
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['integration', 'msg_id'], name='uniq_integration_msgid')
        ]
    
    def __str__(self):
        return f"{self.direction.upper()} {self.msg_type} to/from {self.wa_id}"
