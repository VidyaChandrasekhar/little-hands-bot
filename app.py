import os
import json
import time
import threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "littlehands2024")
OWNER_PHONE = os.environ.get("OWNER_PHONE", "447827219728")

# How many seconds to wait before bot auto-replies (default 90 seconds)
HUMAN_DECISION_WINDOW = int(os.environ.get("DECISION_WINDOW", "90"))

conversations = {}        # conversation history per customer
human_takeover = set()    # phones where human is fully in control
pending_decisions = {}    # phones waiting for owner decision


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

Once you have all three, end your message with this EXACT format on a new line (customer never sees this):
##ORDER:BoxName|CustomerName|Address##

Example: ##ORDER:Explorer Box|Jane Smith|45 Oak Street Hull HU2 8AB##

After collecting details say: "Wonderful! I've passed your order to Vidya who will send you a payment link within 2 hours. Thank you for choosing Little Hands! 🌟"

DELIVERY:
- 3-5 working days, free delivery, gift wrapping £2 extra, UK only

RETURNS: 14 days if unopened

IMPORTANT: If asked whether you are a bot or AI, always honestly confirm that you are an AI assistant.

TONE: Warm, friendly, light emojis, 2-4 sentences max, always end with a question."""


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
    print(f"Sent to {to}: {response.status_code}")
    return response.json()


def notify_owner_new_message(user_phone, user_text):
    """Notify owner of new message with decision options"""
    notification = (
        f"💬 NEW MESSAGE\n"
        f"From: +{user_phone}\n"
        f"Message: \"{user_text}\"\n\n"
        f"Reply with:\n"
        f"✋ *#me* — I'll handle this myself\n"
        f"🤖 *#bot* — Let bot reply now\n"
        f"⏳ Or do nothing — bot replies in {HUMAN_DECISION_WINDOW}s automatically"
    )
    send_whatsapp_message(OWNER_PHONE, notification)


def notify_owner_order(order_details):
    """Send order notification to owner"""
    send_whatsapp_message(OWNER_PHONE, f"🛍️ NEW ORDER!\n\n{order_details}\n\nSend payment link to customer on WhatsApp.\nPayPal: littlehands@gmail.com")


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

    if "content" in resp_json and len(resp_json["content"]) > 0:
        reply = resp_json["content"][0]["text"]
    else:
        print(f"Unexpected Anthropic response: {resp_json}")
        reply = "Hi! How can I help you with Little Hands Stories & Activities Box? 📦"

    conversations[user_phone].append({
        "role": "assistant",
        "content": reply
    })

    return reply


def extract_and_notify_order(reply, user_phone):
    """Extract order tag, notify owner, clean reply"""
    if "##ORDER:" in reply and "##" in reply.split("##ORDER:")[1]:
        try:
            order_raw = reply.split("##ORDER:")[1].split("##")[0]
            parts = order_raw.split("|")
            box = parts[0].strip() if len(parts) > 0 else "Unknown"
            name = parts[1].strip() if len(parts) > 1 else "Unknown"
            address = parts[2].strip() if len(parts) > 2 else "Unknown"

            order_details = f"Box: {box}\nCustomer: {name}\nPhone: +{user_phone}\nAddress: {address}"
            notify_owner_order(order_details)
            reply = reply.replace(f"##ORDER:{order_raw}##", "").strip()
        except Exception as e:
            print(f"Order extraction error: {e}")
            reply = reply.split("##ORDER:")[0].strip()

    return reply


def delayed_bot_reply(user_phone, user_text):
    """Wait for owner decision, then bot replies if no decision made"""
    time.sleep(HUMAN_DECISION_WINDOW)

    # Check if owner made a decision during the wait
    decision = pending_decisions.pop(user_phone, None)

    if decision == "human":
        # Owner said #me — do nothing, human is handling
        print(f"Human handling {user_phone} — bot staying silent")
        return

    if user_phone in human_takeover:
        # Owner put in full takeover mode during wait
        print(f"Full takeover active for {user_phone} — bot staying silent")
        return

    # No decision made — bot replies automatically
    print(f"No owner decision for {user_phone} — bot replying automatically")
    ai_reply = get_ai_reply(user_phone, user_text)
    ai_reply = extract_and_notify_order(ai_reply, user_phone)
    send_whatsapp_message(user_phone, ai_reply)

    # Let owner know bot replied
    send_whatsapp_message(OWNER_PHONE,
        f"🤖 Bot auto-replied to +{user_phone}\n"
        f"Type *#takeover:{user_phone}* to take over this conversation.")


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
            if user_phone != OWNER_PHONE:
                send_whatsapp_message(user_phone,
                    "Hi! I can only read text messages. How can I help you with Little Hands Stories? 📦")
            return jsonify({"status": "ok"}), 200

        user_text = message.get("text", {}).get("body", "").strip()
        if not user_text:
            return jsonify({"status": "empty"}), 200

        print(f"Message from {user_phone}: {user_text}")

        # ===== OWNER COMMANDS =====
        if user_phone == OWNER_PHONE:

            # Owner decides to handle a pending conversation
            if user_text.lower() == "#me":
                # Find most recent pending conversation
                if pending_decisions:
                    latest = list(pending_decisions.keys())[-1]
                    pending_decisions[latest] = "human"
                    human_takeover.add(latest)
                    send_whatsapp_message(OWNER_PHONE,
                        f"✋ Got it! You're handling +{latest}.\n"
                        f"Bot is paused for this conversation.\n"
                        f"Send *#bot:{latest}* when you're done.")
                else:
                    send_whatsapp_message(OWNER_PHONE,
                        "No pending conversations to take over right now.")
                return jsonify({"status": "ok"}), 200

            # Owner takes over specific conversation
            if user_text.lower().startswith("#me:"):
                target = user_text.split(":", 1)[1].strip()
                pending_decisions[target] = "human"
                human_takeover.add(target)
                send_whatsapp_message(OWNER_PHONE,
                    f"✋ You're now handling +{target}.\n"
                    f"Send *#bot:{target}* when you're done.")
                return jsonify({"status": "ok"}), 200

            # Owner wants bot to reply immediately to pending
            if user_text.lower() == "#bot":
                if pending_decisions:
                    latest = list(pending_decisions.keys())[-1]
                    pending_decisions.pop(latest, None)
                    # Get the stored message and reply immediately
                    send_whatsapp_message(OWNER_PHONE, f"🤖 Bot replying to +{latest} now...")
                else:
                    # Resume all takeovers
                    human_takeover.clear()
                    send_whatsapp_message(OWNER_PHONE, "🤖 Bot resumed for all conversations.")
                return jsonify({"status": "ok"}), 200

            # Resume specific customer
            if user_text.lower().startswith("#bot:"):
                target = user_text.split(":", 1)[1].strip()
                human_takeover.discard(target)
                pending_decisions.pop(target, None)
                send_whatsapp_message(OWNER_PHONE,
                    f"🤖 Bot resumed for +{target}.")
                return jsonify({"status": "ok"}), 200

            # Global takeover
            if user_text.lower() == "#takeover":
                human_takeover.add("ALL")
                send_whatsapp_message(OWNER_PHONE,
                    "✅ Bot paused for ALL conversations. Send *#bot* to resume.")
                return jsonify({"status": "ok"}), 200

            # Status check
            if user_text.lower() == "#status":
                takeover_list = ", ".join(human_takeover) if human_takeover else "None"
                pending_list = ", ".join(pending_decisions.keys()) if pending_decisions else "None"
                send_whatsapp_message(OWNER_PHONE,
                    f"📊 Bot Status\n\n"
                    f"Human takeover: {takeover_list}\n"
                    f"Pending decisions: {pending_list}\n"
                    f"Decision window: {HUMAN_DECISION_WINDOW}s\n\n"
                    f"Commands:\n"
                    f"#me — take over latest\n"
                    f"#me:number — take over specific\n"
                    f"#bot — resume all\n"
                    f"#bot:number — resume specific\n"
                    f"#takeover — pause all\n"
                    f"#status — this message")
                return jsonify({"status": "ok"}), 200

            # If owner is just chatting (not a command) — ignore
            if user_text.startswith("#"):
                send_whatsapp_message(OWNER_PHONE,
                    "Unknown command. Send *#status* to see all commands.")
                return jsonify({"status": "ok"}), 200

            # Owner typed a normal message — they're probably replying to a customer
            # Don't process through bot
            return jsonify({"status": "owner_message"}), 200

        # ===== CUSTOMER MESSAGE =====

        # Check if fully paused
        if "ALL" in human_takeover or user_phone in human_takeover:
            print(f"Human handling {user_phone} — skipping")
            return jsonify({"status": "human_handling"}), 200

        # Customer wants human
        human_phrases = ["speak to someone", "speak to a person", "talk to someone",
                        "real person", "human please", "call me", "ring me", "speak to vidya"]
        if any(phrase in user_text.lower() for phrase in human_phrases):
            human_takeover.add(user_phone)
            send_whatsapp_message(user_phone,
                "Of course! I'll get Vidya to contact you shortly 😊 "
                "She'll be in touch within 2 hours during business hours.")
            send_whatsapp_message(OWNER_PHONE,
                f"⚠️ HUMAN REQUESTED\n"
                f"Customer +{user_phone} wants to speak to you directly.\n"
                f"Bot paused. Reply to them, then send *#bot:{user_phone}* to resume bot.")
            return jsonify({"status": "ok"}), 200

        # ===== HUMAN IN THE LOOP =====
        # Notify owner and wait for decision before bot replies

        # If already waiting for a decision on this phone, update the message
        if user_phone in pending_decisions:
            pending_decisions.pop(user_phone, None)

        # Store that we're waiting for a decision
        pending_decisions[user_phone] = "pending"

        # Notify owner with decision options
        notify_owner_new_message(user_phone, user_text)

        # Start delayed bot reply in background thread
        thread = threading.Thread(
            target=delayed_bot_reply,
            args=(user_phone, user_text),
            daemon=True
        )
        thread.start()

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
