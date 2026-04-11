import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Your credentials (will be set as environment variables on Render)
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "littlehands2024")

# Store conversation history per user
conversations = {}

SYSTEM_PROMPT = """You are a warm, friendly WhatsApp customer service assistant for "Little Hands Stories & Activities Box" — a UK-based children's activity box business.

BUSINESS INFORMATION:
- Product: One-off purchase activity boxes for children aged 0–8
- Contents: Age-appropriate storybooks, craft activities, and educational worksheets
- Price range: £15–£25 per box
- No subscription — one-off purchases only

AGE BOXES AVAILABLE:
- Baby Box (0–2 years): £15 — soft storybook, sensory activity sheet, simple craft
- Toddler Box (2–4 years): £18 — picture storybook, 2 craft activities, colouring worksheet
- Explorer Box (4–6 years): £22 — storybook, 3 craft activities, 2 educational worksheets
- Adventure Box (6–8 years): £25 — chapter book extract, 3 crafts, 3 educational worksheets

ORDERING:
- Customers can order via WhatsApp by telling you which box they want and providing their delivery address
- Payment is taken via bank transfer or PayPal to littlehands@gmail.com
- Confirm orders by saying you will process and send a confirmation

DELIVERY:
- 3–5 working days standard delivery
- Free delivery on all orders
- Gift wrapping available for £2 extra
- UK delivery only currently

RETURNS:
- Returns accepted within 14 days if box is unopened
- Contact via WhatsApp to arrange return

TONE GUIDELINES:
- Always warm, friendly, and enthusiastic about children's learning
- Use occasional light emojis (not too many)
- Keep replies SHORT and conversational — this is WhatsApp, not email
- Maximum 3–4 sentences per reply
- Always end with a gentle question to keep conversation going
- If someone wants to order, collect: which box, delivery name, delivery address
- Never make up information not listed above"""


def send_whatsapp_message(to, message):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    response = requests.post(url, headers=headers, json=data)
    return response.json()


def get_ai_reply(user_phone, user_message):
    # Get or create conversation history for this user
    if user_phone not in conversations:
        conversations[user_phone] = []

    # Add user message to history
    conversations[user_phone].append({
        "role": "user",
        "content": user_message
    })

    # Keep only last 10 messages to avoid token limits
    history = conversations[user_phone][-10:]

    # Call Anthropic API
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 300,
            "system": SYSTEM_PROMPT,
            "messages": history
        }
    )

    reply = response.json()["content"][0]["text"]

    # Add assistant reply to history
    conversations[user_phone].append({
        "role": "assistant",
        "content": reply
    })

    return reply


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Meta webhook verification"""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def receive_message():
    """Receive and process incoming WhatsApp messages"""
    try:
        data = request.json
        print(f"Incoming webhook: {json.dumps(data)}")

        # Safety checks
        if not data:
            return jsonify({"status": "no data"}), 200
        
        entry = data.get("entry", [])
        if not entry:
            return jsonify({"status": "no entry"}), 200

        changes = entry[0].get("changes", [])
        if not changes:
            return jsonify({"status": "no changes"}), 200

        value = changes[0].get("value", {})
        if not value:
            return jsonify({"status": "no value"}), 200

        messages = value.get("messages", [])
        if not messages:
            return jsonify({"status": "no message"}), 200

        message = messages[0]
        user_phone = message.get("from")
        if not user_phone:
            return jsonify({"status": "no phone"}), 200

        # Only handle text messages
        if message.get("type") != "text":
            send_whatsapp_message(user_phone,
                "Hi! I can only read text messages at the moment. "
                "How can I help you with Little Hands Stories & Activities Box? 📦")
            return jsonify({"status": "ok"}), 200

        user_text = message.get("text", {}).get("body", "")
        if not user_text:
            return jsonify({"status": "empty message"}), 200

        print(f"Message from {user_phone}: {user_text}")

        # Get AI reply
        ai_reply = get_ai_reply(user_phone, user_text)
        print(f"AI reply: {ai_reply}")

        # Send reply back via WhatsApp
        send_whatsapp_message(user_phone, ai_reply)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error"}), 200


@app.route("/", methods=["GET"])
def home():
    return "Little Hands Stories Bot is running! 🌟"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
