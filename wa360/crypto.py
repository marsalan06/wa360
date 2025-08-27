"""
Cryptography Helper Module
Handles encryption and decryption of sensitive data using Fernet
"""
import logging
from cryptography.fernet import Fernet
from django.conf import settings

logger = logging.getLogger(__name__)

# Initialize Fernet instance
try:
    logger.info("=== CRYPTO MODULE INITIALIZATION ===")
    logger.info(f"D360_ENCRYPTION_KEY present: {bool(getattr(settings, 'D360_ENCRYPTION_KEY', None))}")
    
    if not getattr(settings, 'D360_ENCRYPTION_KEY', None):
        raise Exception("D360_ENCRYPTION_KEY not set in settings")
    
    _fernet = Fernet(settings.D360_ENCRYPTION_KEY.encode())
    logger.info("✓ Crypto module initialized successfully")
    
except Exception as e:
    logger.error(f"Failed to initialize crypto: {str(e)}")
    _fernet = None

def enc(s: str) -> str:
    """Encrypt a string"""
    logger.info("=== ENCRYPTION STARTED ===")
    
    if not _fernet:
        error_msg = "Crypto not initialized"
        logger.error(error_msg)
        raise Exception(error_msg)
    
    try:
        logger.info("Calling Fernet.encrypt()...")
        encrypted = _fernet.encrypt(s.encode())
        logger.info("✓ Encryption successful")
        
        encrypted_str = encrypted.decode()
        logger.info("=== ENCRYPTION COMPLETED SUCCESSFULLY ===")
        return encrypted_str
        
    except Exception as e:
        logger.error(f"=== ENCRYPTION FAILED: {str(e)} ===")
        raise

def dec(s: str) -> str:
    """Decrypt a string"""
    logger.info("=== DECRYPTION STARTED ===")
    
    if not _fernet:
        error_msg = "Crypto not initialized"
        logger.error(error_msg)
        raise Exception(error_msg)
    
    try:
        logger.info("Calling Fernet.decrypt()...")
        decrypted = _fernet.decrypt(s.encode())
        logger.info("✓ Decryption successful")
        
        decrypted_str = decrypted.decode()
        logger.info("=== DECRYPTION COMPLETED SUCCESSFULLY ===")
        return decrypted_str
        
    except Exception as e:
        logger.error(f"=== DECRYPTION FAILED: {str(e)} ===")
        raise
