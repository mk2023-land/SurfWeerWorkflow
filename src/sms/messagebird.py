"""
MessageBird SMS integratie module.
Verstuurt SMS berichten via MessageBird API.
"""
import logging
from typing import Optional
import messagebird

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import MESSAGEBIRD_CONFIG, DEBUG

logger = logging.getLogger(__name__)


class MessageBirdClient:
    """Client voor MessageBird SMS verzending."""

    def __init__(self):
        self.api_key = MESSAGEBIRD_CONFIG['api_key']
        self.originator = MESSAGEBIRD_CONFIG['originator']
        self.recipient = MESSAGEBIRD_CONFIG['recipient']

        if not self.api_key:
            logger.warning("No MessageBird API key configured")
            self.client = None
        else:
            self.client = messagebird.Client(self.api_key)

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
            logger.error("MessageBird client not configured")
            return {
                'success': False,
                'error': 'MessageBird client not configured',
                'message': message
            }

        try:
            # Gebruik recipient uit config of parameter
            to_number = recipient or self.recipient

            if not to_number:
                logger.error("No recipient phone number configured")
                return {
                    'success': False,
                    'error': 'No recipient phone number configured',
                    'message': message
                }

            # Verzend SMS
            msg = self.client.message_create(
                self.originator,
                to_number,
                message
            )

            logger.info(f"SMS sent successfully. ID: {msg.id}, Recipient: {to_number}")

            return {
                'success': True,
                'message_id': msg.id,
                'recipient': to_number,
                'message': message,
                'status': msg.status,
                'created_datetime': msg.createdDatetime
            }

        except messagebird.client.ErrorException as e:
            logger.error(f"MessageBird API error: {e}")

            # Extract error details
            error_details = []
            for error in e.errors:
                error_details.append({
                    'code': error.code,
                    'description': error.description,
                    'parameter': error.parameter
                })

            return {
                'success': False,
                'error': str(e),
                'error_details': error_details,
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
        """
        Verstuurt alert SMS.

        Args:
            message: Alert SMS tekst
            recipient: Ontvanger telefoonnummer (optioneel)

        Returns:
            Dictionary met success status en details
        """
        logger.info(f"Sending alert SMS to {recipient or self.recipient}")

        # In debug mode: log alleen, verzend niet
        if DEBUG:
            logger.info(f"DEBUG MODE: Would send SMS: {message}")
            return {
                'success': True,
                'debug_mode': True,
                'message': message
            }

        return self.send_sms(message, recipient)

    def send_digest_sms(self, message: str, recipient: str = None) -> dict:
        """
        Verstuurt digest SMS.

        Args:
            message: Digest SMS tekst
            recipient: Ontvanger telefoonnummer (optioneel)

        Returns:
            Dictionary met success status en details
        """
        logger.info(f"Sending digest SMS to {recipient or self.recipient}")

        # In debug mode: log alleen, verzend niet
        if DEBUG:
            logger.info(f"DEBUG MODE: Would send SMS: {message}")
            return {
                'success': True,
                'debug_mode': True,
                'message': message
            }

        return self.send_sms(message, recipient)

    def check_balance(self) -> Optional[dict]:
        """
        Check MessageBird account balance.

        Returns:
            Dictionary met balance info of None bij error
        """
        if not self.client:
            logger.error("MessageBird client not configured")
            return None

        try:
            balance = self.client.balance()

            return {
                'amount': balance.amount,
                'currency': balance.currency,
                'type': balance.type
            }

        except Exception as e:
            logger.error(f"Failed to check balance: {e}")
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
            return f"SUCCESS: ID={sms_result.get('message_id')}, To={sms_result.get('recipient')}, " \
                   f"Msg={sms_result['message'][:100]}..."
    else:
        return f"FAILED: {sms_result.get('error')}, Msg={sms_result['message'][:100]}..."