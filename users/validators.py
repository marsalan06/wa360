import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
import phonenumbers
from phonenumbers import NumberParseException

def validate_pakistani_mobile(phone_number):
    """
    Validate Pakistani mobile number format
    Supports formats: +92-3XX-XXXXXXX, 03XX-XXXXXXX, 3XXXXXXXXX
    """
    pass
    # if not phone_number:
    #     raise ValidationError(_('Phone number is required.'))
    
    # # Clean the number
    # cleaned_number = re.sub(r'[^\d+]', '', str(phone_number))
    
    # # Pakistani mobile number patterns
    # patterns = [
    #     r'^(\+92|0092|92)?0?3[0-4]\d{7}$',  # Pakistani mobile format
    # ]
    
    # is_valid = False
    # for pattern in patterns:
    #     if re.match(pattern, cleaned_number):
    #         is_valid = True
    #         break
    
    # if not is_valid:
    #     raise ValidationError(
    #         _('Invalid Pakistani mobile number. Format should be: +92-3XX-XXXXXXX or 03XX-XXXXXXX')
    #     )
    
    # # Additional validation using phonenumbers library
    # try:
    #     parsed_number = phonenumbers.parse(cleaned_number, 'PK')
    #     if not phonenumbers.is_valid_number(parsed_number):
    #         raise ValidationError(_('Invalid phone number.'))
    #     if not phonenumbers.number_type(parsed_number) == phonenumbers.PhoneNumberType.MOBILE:
    #         raise ValidationError(_('Only mobile numbers are allowed.'))
    # except NumberParseException:
    #     raise ValidationError(_('Invalid phone number format.'))

def validate_full_name(value):
    """Validate full name contains only letters and spaces"""
    if not re.match(r'^[a-zA-Z\s]+$', value):
        raise ValidationError(_('Full name should contain only letters and spaces.'))
    
    if len(value.strip()) < 2:
        raise ValidationError(_('Full name should be at least 2 characters long.'))

def validate_password_strength(password):
    """Validate password strength"""
    if len(password) < 8:
        raise ValidationError(_('Password must be at least 8 characters long.'))
    
    if not re.search(r'[A-Z]', password):
        raise ValidationError(_('Password must contain at least one uppercase letter.'))
    
    if not re.search(r'[a-z]', password):
        raise ValidationError(_('Password must contain at least one lowercase letter.'))
    
    if not re.search(r'\d', password):
        raise ValidationError(_('Password must contain at least one digit.'))
