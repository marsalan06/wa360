"""
Utility functions for WhatsApp 360dialog integration
"""
import re
import logging

logger = logging.getLogger(__name__)

def normalize_msisdn(msisdn: str) -> str:
    """Normalize MSISDN to standard format with + prefix"""
    digits = re.sub(r"[^\d+]", "", msisdn or "")
    if digits and not digits.startswith("+"):
        digits = "+" + digits.lstrip("+")
    return digits

def digits_only(msisdn: str) -> str:
    """Return international number with digits only (no '+'). Sandbox expects this."""
    return re.sub(r"\D", "", msisdn or "")
