"""
Twilio SMS integratie module.
Verstuurt SMS berichten via Twilio API.
"""
import logging
from typing import Optional
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

logger = logging.getLogger(__name__)


class TwilioClient:
    """Client voor Twilio SMS verzending."""

    def __init__(self):
        import os
        from dotenv import load_dotenv

        load_dotenv()

        self.account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        self.auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        self.from_number = os.getenv('TWILIO_PHONE_NUMBER')
        self.to_number = os.getenv('RECIPIENT_PHONE_NUMBER')

        if not self.account_sid or not self.auth_token:
            logger.warning("Twilio credentials not configured")
            self.client = None
        else:
            try:
                self.client = Client(self.account_sid, self.auth_token)
                logger.info("Twilio client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Twilio client: {e}")
                self.client = None

    def send_sms(self, message: str, recipient: str = None) -> dict:
        """
        Verstuurt SMS bericht.

        Args:
            message: SMS tekst
            recipient: Ontvanger telefoonnummer (optioneel, gebruikt default uit config)

        Returns:
            Dictionary met success status en details
        """
        if not self.client:
            logger.error("Twilio client not configured")
            return {
                'success': False,
                'error': 'Twilio client not configured',
                'message': message
            }

        try:
            # Gebruik recipient uit config of parameter
            to_number = recipient or self.to_number

            if not to_number:
                logger.error("No recipient phone number configured")
                return {
                    'success': False,
                    'error': 'No recipient phone number configured',
                    'message': message
                }

            # Verzend SMS via Twilio
            message_obj = self.client.messages.create(
                body=message,
                from_=self.from_number,
                to=to_number
            )

            logger.info(f"SMS sent successfully. SID: {message_obj.sid}, Recipient: {to_number}")

            return {
                'success': True,
                'message_id': message_obj.sid,
                'recipient': to_number,
                'message': message,
                'status': message_obj.status,
                'created_datetime': message_obj.date_created.isoformat()
            }

        except TwilioRestException as e:
            logger.error(f"Twilio API error: {e}")

            return {
                'success': False,
                'error': str(e),
                'code': e.code,
                'message': message
            }

        except Exception as e:
            logger.error(f"Unexpected error sending SMS: {e}")
            return {
                'success': False,
                'error': str(e),
                'message': message
            }

    def send_alert_sms(self, message: str, recipient: str = None) -> dict:
        """Verstuurt alert SMS. Dry-run wordt door main.py `--dry-run` afgevangen."""
        logger.info(f"Sending alert SMS to {recipient or self.to_number}")
        return self.send_sms(message, recipient)

    def send_digest_sms(self, message: str, recipient: str = None) -> dict:
        """Verstuurt digest SMS. Dry-run wordt door main.py `--dry-run` afgevangen."""
        logger.info(f"Sending digest SMS to {recipient or self.to_number}")
        return self.send_sms(message, recipient)

    def check_balance(self) -> Optional[dict]:
        """
        Check Twilio account balance.

        Returns:
            Dictionary met balance info of None bij error
        """
        if not self.client:
            logger.error("Twilio client not configured")
            return None

        try:
            account = self.client.api.accounts(self.account_sid).fetch()

            return {
                'account_sid': account.sid,
                'status': account.status,
                'type': account.type
            }

        except Exception as e:
            logger.error(f"Failed to check account: {e}")
            return None


def format_sms_for_logging(sms_result: dict) -> str:
    """
    Formatteer SMS result voor logging.

    Args:
        sms_result: Resultaat van send_sms methode

    Returns:
    Geformatteerde string
    """
    if sms_result['success']:
        if sms_result.get('debug_mode'):
            return f"DEBUG: {sms_result['message'][:100]}..."
        else:
            return f"SUCCESS: SID={sms_result.get('message_id')}, To={sms_result.get('recipient')}, " \
                   f"Msg={sms_result['message'][:100]}..."
    else:
        return f"FAILED: {sms_result.get('error')}, Msg={sms_result['message'][:100]}..."