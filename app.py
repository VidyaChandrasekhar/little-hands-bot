import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Credentials from environment variables
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "littlehands2024")

# YOUR WhatsApp number to receive order notifications
# Replace with your actual number in international format
OWNER_PHONE = os.environ.get("OWNER_PHONE", "447827219728")

# Store conversation history per user
conversations = {}

# Track which conversations are in human takeover mode
human_takeover = set()

# Track order collection state per user
order_states = {}

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
- When a customer wants to order, collect: which box they want, delivery name, delivery address
- After collecting details say: "Perfect! I've passed your order to Vidya who will send you a payment link within 2 hours 😊"
- Payment is via bank transfer or PayPal
- DO NOT process payment yourself

DELIVERY:
- 3–5 working days standard delivery
- Free delivery on all orders
- Gift wrapping available for £2 extra
- UK delivery only currently

RETURNS:
- Returns accepted within 14 days if box is unopened

ORDER DETECTION:
- If a customer says anything like "I want to order", "I'd like to buy", "can I get", "how do I order" — reply normally but also include the phrase "COLLECT_ORDER" somewhere in your response so the system can detect it
- After collecting name and address, include "ORDER_COMPLETE:[box name]:[customer name]:[address]" at the very end of your message (this will be hidden from customer)

TONE GUIDELINES:
- Always warm, friendly and enthusiastic about children's learning
- Use occasional light emojis
- Keep replies SHORT — 2-4 sentences max
- Always end with a gentle question
- Never make up information not listed above"""


def send_whatsapp_message(to, message):
    """Send a WhatsApp message"""
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
    print(f"Send message response: {response.json()}")
    return response.json()


def notify_owner(order_details):
    """Send order notification to the owner"""
    notification = f"""🛍️ NEW ORDER ALERT!

{order_details}

Reply to the customer directly on WhatsApp to send payment link.
Payment: Bank transfer or PayPal to littlehands@gmail.com"""

    send_whatsapp_message(OWNER_PHONE, notification)


def get_ai_reply(user_phone, user_message):
    """Get AI reply from Anthropic"""
    if user_phone not in conversations:
        conversations[user_phone] = []

    conversations[user_phone].append({
        "role": "user",
        "content": user_message
    })

    history = conversations[user_phone][-10:]

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 400,
            "system": SYSTEM_PROMPT,
            "messages": history
        }
    )

    resp_json = response.json()
    print(f"Anthropic response: {resp_json}")

    if "content" in resp_json and len(resp_json["content"]) > 0:
        reply = resp_json["content"][0]["text"]
    else:
        reply = "Hi! Thanks for your message. How can I help you with Little Hands Stories & Activities Box? 📦"

    conversations[user_phone].append({
        "role": "assistant",
        "content": reply
    })

    return reply


def process_order_completion(reply, user_phone):
    """Check if order is complete and notify owner"""
    if "ORDER_COMPLETE:" in reply:
        try:
            order_data = reply.split("ORDER_COMPLETE:")[1].strip()
            parts = order_data.split(":")
            box_name = parts[0] if len(parts) > 0 else "Unknown box"
            customer_name = parts[1] if len(parts) > 1 else "Unknown"
            address = parts[2] if len(parts) > 2 else "Unknown"

            order_details = f"""Box: {box_name}
Customer: {customer_name}
Phone: +{user_phone}
Address: {address}"""

            notify_owner(order_details)

            # Remove the hidden order data from the reply shown to customer
            reply = reply.split("ORDER_COMPLETE:")[0].strip()
        except Exception as e:
            print(f"Error processing order: {e}")

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
                "How can I help you with Little Hands Stories? 📦")
            return jsonify({"status": "ok"}), 200

        user_text = message.get("text", {}).get("body", "").strip()
        if not user_text:
            return jsonify({"status": "empty"}), 200

        print(f"Message from {user_phone}: {user_text}")

        # ===== HUMAN TAKEOVER COMMANDS =====
        # Owner types #takeover to take control
        if user_text.lower() == "#takeover" and user_phone == OWNER_PHONE:
            human_takeover.add(user_phone)
            send_whatsapp_message(OWNER_PHONE,
                "✅ You are now in control. The bot is paused for all conversations. Type #bot to hand back to AI.")
            return jsonify({"status": "ok"}), 200

        # Owner types #bot to hand back to AI
        if user_text.lower() == "#bot" and user_phone == OWNER_PHONE:
            human_takeover.discard(user_phone)
            send_whatsapp_message(OWNER_PHONE,
                "🤖 Bot is back in control. All conversations will be handled by AI again.")
            return jsonify({"status": "ok"}), 200

        # Check takeover per conversation — owner can type #takeover:[phone] to pause specific chat
        if user_text.lower().startswith("#takeover:"):
            target_phone = user_text.split(":")[1].strip()
            human_takeover.add(target_phone)
            send_whatsapp_message(OWNER_PHONE,
                f"✅ Bot paused for {target_phone}. You can now reply to them directly.")
            return jsonify({"status": "ok"}), 200

        if user_text.lower().startswith("#bot:"):
            target_phone = user_text.split(":")[1].strip()
            human_takeover.discard(target_phone)
            send_whatsapp_message(OWNER_PHONE,
                f"🤖 Bot resumed for {target_phone}.")
            return jsonify({"status": "ok"}), 200

        # If this conversation is in human takeover mode, skip AI
        if user_phone in human_takeover:
            print(f"Skipping AI for {user_phone} — human takeover active")
            return jsonify({"status": "human_handling"}), 200

        # Customer types "speak to someone" or "human" — notify owner and pause bot
        if any(phrase in user_text.lower() for phrase in
               ["speak to someone", "speak to a person", "human", "real person", "call me"]):
            human_takeover.add(user_phone)
            send_whatsapp_message(user_phone,
                "Of course! I'll get Vidya to contact you shortly 😊 "
                "She'll be in touch within 2 hours during business hours.")
            notify_owner(f"⚠️ HUMAN REQUESTED\nCustomer +{user_phone} wants to speak to a person.\nBot has been paused for this conversation.\nReply to them directly, then send #bot:{user_phone} to resume the bot.")
            return jsonify({"status": "ok"}), 200

        # ===== AI REPLY =====
        ai_reply = get_ai_reply(user_phone, user_text)
        print(f"AI reply: {ai_reply}")

        # Check for completed order
        ai_reply = process_order_completion(ai_reply, user_phone)

        # Send reply to customer
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
