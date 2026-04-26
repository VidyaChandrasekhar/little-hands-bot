import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "littlehands2024")
OWNER_PHONE = os.environ.get("OWNER_PHONE", "447827219728")

conversations = {}
human_takeover = set()

SYSTEM_PROMPT = """You are a warm, friendly WhatsApp customer service assistant for "Little Hands Stories & Activities Box" — a UK-based children's activity box business.

BUSINESS INFORMATION:
- Product: One-off purchase activity boxes for children aged 0-8
- Contents: Age-appropriate storybooks, craft activities, and educational worksheets
- Price range: £15-£25 per box
- No subscription — one-off purchases only

AGE BOXES AVAILABLE:
- Baby Box (0-2 years): £15
- Toddler Box (2-4 years): £18
- Explorer Box (4-6 years): £22
- Adventure Box (6-8 years): £25

ORDERING INSTRUCTIONS:
When a customer wants to order, collect these three things one at a time:
1. Which box they want
2. Their full name
3. Their delivery address

Once you have all three, end your message with this EXACT format on a new line (the customer will never see this line, it is automatically removed before sending):
##ORDER:BoxName|CustomerName|Address##

Example ending: ##ORDER:Explorer Box|Jane Smith|45 Oak Street Hull HU2 8AB##

After collecting all details, tell the customer: "Wonderful! I've passed your order to Vidya who will send you a payment link within 2 hours. Thank you for choosing Little Hands! 🌟"

DELIVERY:
- 3-5 working days
- Free delivery on all orders
- Gift wrapping available for £2 extra
- UK only

RETURNS:
- 14 days if unopened

TONE: Warm, friendly, use light emojis, keep replies to 2-4 sentences, always end with a question."""


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
    print(f"Send message to {to}: {response.status_code}")
    return response.json()


def notify_owner(message):
    send_whatsapp_message(OWNER_PHONE, message)


def get_ai_reply(user_phone, user_message):
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
    print(f"Anthropic response status: {response.status_code}")

    if "content" in resp_json and len(resp_json["content"]) > 0:
        reply = resp_json["content"][0]["text"]
    else:
        print(f"Unexpected response: {resp_json}")
        reply = "Hi! How can I help you with Little Hands Stories & Activities Box? 📦"

    conversations[user_phone].append({
        "role": "assistant",
        "content": reply
    })

    return reply


def extract_and_notify_order(reply, user_phone):
    """Extract order details, notify owner, and clean reply"""
    if "##ORDER:" in reply and "##" in reply.split("##ORDER:")[1]:
        try:
            order_raw = reply.split("##ORDER:")[1].split("##")[0]
            parts = order_raw.split("|")
            box = parts[0].strip() if len(parts) > 0 else "Unknown"
            name = parts[1].strip() if len(parts) > 1 else "Unknown"
            address = parts[2].strip() if len(parts) > 2 else "Unknown"

            owner_msg = f"""🛍️ NEW ORDER!

Box: {box}
Customer: {name}
Phone: +{user_phone}
Address: {address}

Send payment link to the customer on WhatsApp.
PayPal: littlehands@gmail.com"""

            notify_owner(owner_msg)
            print(f"Order notification sent to owner for {name}")

            # Remove the hidden order tag from customer message
            reply = reply.replace(f"##ORDER:{order_raw}##", "").strip()

        except Exception as e:
            print(f"Order extraction error: {e}")
            reply = reply.split("##ORDER:")[0].strip()

    return reply


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def receive_message():
    try:
        data = request.json
        print(f"Webhook received")

        if not data:
            return jsonify({"status": "no data"}), 200

        entry = data.get("entry", [])
        if not entry:
            return jsonify({"status": "no entry"}), 200

        changes = entry[0].get("changes", [])
        if not changes:
            return jsonify({"status": "no changes"}), 200

        value = changes[0].get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return jsonify({"status": "no message"}), 200

        message = messages[0]
        user_phone = message.get("from")
        if not user_phone:
            return jsonify({"status": "no phone"}), 200

        if message.get("type") != "text":
            send_whatsapp_message(user_phone,
                "Hi! I can only read text messages. How can I help you with Little Hands Stories? 📦")
            return jsonify({"status": "ok"}), 200

        user_text = message.get("text", {}).get("body", "").strip()
        if not user_text:
            return jsonify({"status": "empty"}), 200

        print(f"Message from {user_phone}: {user_text}")

        # ===== OWNER COMMANDS =====
        if user_phone == OWNER_PHONE:
            # Global takeover
            if user_text.lower() == "#takeover":
                human_takeover.add("ALL")
                send_whatsapp_message(OWNER_PHONE,
                    "✅ Bot paused for ALL conversations. Type #bot to resume.")
                return jsonify({"status": "ok"}), 200

            # Global resume
            if user_text.lower() == "#bot":
                human_takeover.discard("ALL")
                human_takeover.clear()
                send_whatsapp_message(OWNER_PHONE,
                    "🤖 Bot resumed for all conversations.")
                return jsonify({"status": "ok"}), 200

            # Takeover specific customer: #takeover:447123456789
            if user_text.lower().startswith("#takeover:"):
                target = user_text.split(":", 1)[1].strip()
                human_takeover.add(target)
                send_whatsapp_message(OWNER_PHONE,
                    f"✅ Bot paused for {target}. Type #bot:{target} to resume.")
                return jsonify({"status": "ok"}), 200

            # Resume specific customer: #bot:447123456789
            if user_text.lower().startswith("#bot:"):
                target = user_text.split(":", 1)[1].strip()
                human_takeover.discard(target)
                send_whatsapp_message(OWNER_PHONE,
                    f"🤖 Bot resumed for {target}.")
                return jsonify({"status": "ok"}), 200

        # ===== CHECK TAKEOVER =====
        if "ALL" in human_takeover or user_phone in human_takeover:
            print(f"Human handling {user_phone} — skipping AI")
            return jsonify({"status": "human_handling"}), 200

        # ===== CUSTOMER WANTS HUMAN =====
        human_phrases = ["speak to someone", "speak to a person", "talk to someone",
                        "real person", "human please", "call me", "ring me"]
        if any(phrase in user_text.lower() for phrase in human_phrases):
            human_takeover.add(user_phone)
            send_whatsapp_message(user_phone,
                "Of course! I'll get Vidya to contact you shortly 😊 "
                "She'll be in touch within 2 hours during business hours.")
            notify_owner(
                f"⚠️ HUMAN REQUESTED\n"
                f"Customer +{user_phone} wants to speak to a person.\n"
                f"Bot paused for this conversation.\n"
                f"Reply to them, then send: #bot:{user_phone} to resume bot.")
            return jsonify({"status": "ok"}), 200

        # ===== GET AI REPLY =====
        ai_reply = get_ai_reply(user_phone, user_text)
        print(f"AI reply generated")

        # Extract order if present and notify owner
        ai_reply = extract_and_notify_order(ai_reply, user_phone)

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
