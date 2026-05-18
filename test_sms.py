"""
Test script voor Twilio SMS.
Verstuurt een test bericht naar je eigen nummer.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.sms.twilio import TwilioClient

def main():
    """Test Twilio SMS verzending."""
    print("🧪 Testing Twilio SMS...")

    # Maak client
    client = TwilioClient()

    # Test bericht
    test_message = "Test bericht van SurfWeerAlert! 🏄‍♂️"

    print(f"📱 Sending test message to +31631369911...")
    print(f"Message: {test_message}")

    # Verstuur test SMS
    result = client.send_sms(test_message)

    if result['success']:
        print(f"✅ SUCCESS! Message sent!")
        print(f"   Message ID: {result.get('message_id')}")
        print(f"   Status: {result.get('status')}")
    else:
        print(f"❌ FAILED: {result.get('error')}")
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())