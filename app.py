import os
import re
import requests
from flask import Flask, request, jsonify, g

app = Flask(__name__)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "littlehands2024")
OWNER_PHONE = os.environ.get("OWNER_PHONE", "447827219728")

# ======================================================================
# BUSINESS CONFIG - to set up a new customer, edit only this block
# (details taken from https://www.shrisaaranganarts.com/)
# ======================================================================
BUSINESS_NAME = "Shri Saarangan Arts"
OWNER_NAME = "the Saarangan Arts team"
NOTIFY_EVERY_REPLY = False   # True = owner gets an alert for every bot reply

BUSINESS_INFO = """- Shri Saarangan Arts Ltd is a UK-based maker of bespoke, handmade Pooja Mandhirs (Hindu prayer shrines / prayer room pieces) and traditional Indian home decor. It serves the UK Indian community. Tagline: "handcrafted for you".
- Everything is designed, made and delivered in the UK ("Made in Britain").
- Products and services:
  * Custom-made Pooja Mandhirs and accessories
  * Paintings, including original oil-on-canvas works and commissioned artworks
  * Picture frames
  * Other Indian-themed art and decor
  * Golu padi / steps
  * Collaborative design: the team works with the customer to design decor that suits their space
- UK-wide delivery.
- Studio / office: 141 School Avenue, Basildon, SS15 6GH, Essex.
- Email: saarangan.arts@gmail.com
- Phone: +44 7479 393967
- Instagram: @shrisaaranganarts   Facebook: saarangan.arts
- Website: www.shrisaaranganarts.com (shop at www.shrisaaranganarts.com/shop)
- Customers can also use the contact form on the website.

NOT KNOWN (never guess these; say you will check with the team): prices, quotes, deposits, delivery charges, delivery times, lead times, opening hours, returns or refunds, payment methods.
Pricing is bespoke: it depends on the design, size and materials, and is given as a quote."""

ENQUIRY_FLOW = """When a customer wants something made, or wants a quote, collect these one at a time:
1. What they would like (for example a pooja mandhir, a painting, a frame, golu padi)
2. Rough size, style or any ideas, and a budget if they want to share one. They can send a photo for inspiration.
3. Their name
4. Their town or postcode (for delivery)

Once you have all four, end your message with this EXACT format on a new line (the customer never sees it):
##LEAD:What they want, size/style/budget|Customer name|Town or postcode##

Example: ##LEAD:Wooden pooja mandhir about 4ft, traditional style, budget around 800|Priya Nair|Basildon SS15##

After that, say the team will contact them on this WhatsApp number to discuss the design and give a quote. Do not promise a time."""

TONE = "Warm, welcoming and proud of the craftsmanship. 2-4 short sentences. Light emoji use is fine. Where it fits, end with a question."
# ======================================================================

SYSTEM_PROMPT = f"""You are the friendly WhatsApp assistant for "{BUSINESS_NAME}", a UK business.

BUSINESS INFORMATION (only use these facts):
{BUSINESS_INFO}

RULES:
- Never invent prices, discounts, dates, lead times or policies. If you do not know something, say you will check with {OWNER_NAME} and offer to take the customer's details.
- If asked whether you are a bot or AI, always honestly say you are an AI assistant.
- If the customer is upset, has a complaint, or asks for a person, say you will pass it to {OWNER_NAME}.
- Customers can send photos. If they mention a photo, thank them and say the team will look at it.

{ENQUIRY_FLOW}

TONE: {TONE}"""

HUMAN_PHRASES = ["speak to someone", "speak to a person", "talk to someone",
                 "real person", "human please", "call me", "ring me",
                 "speak to the owner", "speak to a human"]

MEDIA_TYPES = ("image", "video", "audio", "document", "sticker")

conversations = {}
human_takeover = set()


def current_phone_number_id():
    """Reply from the WhatsApp number the message arrived on."""
    try:
        return g.phone_number_id or PHONE_NUMBER_ID
    except (AttributeError, RuntimeError):
        return PHONE_NUMBER_ID


def graph_post(payload):
    url = f"https://graph.facebook.com/v18.0/{current_phone_number_id()}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json=payload, timeout=15)
    print(f"Sent to {payload.get('to')}: {response.status_code}")
    if response.status_code != 200:
        print(f"Send error: {response.text}")
    return response


def send_whatsapp_message(to, message):
    return graph_post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    })


def notify_owner(message):
    send_whatsapp_message(OWNER_PHONE, message)


def forward_media_to_owner(user_phone, mtype, media):
    """Tell the owner a customer sent media, then forward the file itself."""
    caption = media.get("caption", "")
    notify_owner(
        f"{mtype.upper()} from +{user_phone}"
        + (f"\nCaption: {caption}" if caption else "")
        + f"\nSend #takeover:{user_phone} to take over."
    )
    payload_media = {"id": media.get("id")}
    if caption and mtype in ("image", "video", "document"):
        payload_media["caption"] = caption
    graph_post({
        "messaging_product": "whatsapp",
        "to": OWNER_PHONE,
        "type": mtype,
        mtype: payload_media,
    })


def get_ai_reply(user_phone, user_message):
    history = conversations.setdefault(user_phone, [])
    history.append({"role": "user", "content": user_message})
    messages = history[-10:]

    # the conversation must start with a user message
    while messages and messages[0]["role"] != "user":
        messages = messages[1:]

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "system": SYSTEM_PROMPT,
                "messages": messages,
            },
            timeout=30,
        )
        reply = response.json()["content"][0]["text"]
    except Exception as e:
        print(f"Anthropic error: {e}")
        history.pop()
        return f"Hi! Thanks for contacting {BUSINESS_NAME}. How can I help?"

    history.append({"role": "assistant", "content": reply})
    return reply


def extract_and_notify_lead(reply, user_phone):
    match = re.search(r"##LEAD:(.+?)\|(.+?)\|(.+?)##", reply, re.DOTALL)
    if not match:
        # strip any half-finished tag so the customer never sees it
        return reply.split("##LEAD:")[0].strip() if "##LEAD:" in reply else reply

    interest, name, place = (x.strip() for x in match.groups())
    notify_owner(
        f"NEW ENQUIRY - {BUSINESS_NAME}\n\n"
        f"Customer: {name}\n"
        f"Phone: +{user_phone}\n"
        f"Location: {place}\n"
        f"Wants: {interest}\n\n"
        f"Reply to the customer directly on WhatsApp to discuss and quote.\n"
        f"Send #takeover:{user_phone} to pause the bot for this chat."
    )
    return reply.replace(match.group(0), "").strip()


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


def handle_owner_command(text):
    t = text.lower()
    if t.startswith("#takeover:"):
        target = text.split(":", 1)[1].strip()
        human_takeover.add(target)
        notify_owner(f"You are now handling +{target}. Send #bot:{target} when done.")
    elif t == "#takeover":
        human_takeover.add("ALL")
        notify_owner("Bot paused for ALL chats. Send #bot to resume.")
    elif t.startswith("#bot:"):
        target = text.split(":", 1)[1].strip()
        human_takeover.discard(target)
        notify_owner(f"Bot resumed for +{target}")
    elif t == "#bot":
        human_takeover.clear()
        notify_owner("Bot resumed for all chats.")
    elif t == "#status":
        paused = ", ".join(human_takeover) if human_takeover else "None"
        notify_owner(
            f"Bot status. Paused for: {paused}\n\n"
            "#takeover - pause everyone\n#takeover:number - pause one chat\n"
            "#bot - resume everyone\n#bot:number - resume one chat"
        )
    else:
        notify_owner("Unknown command. Send #status to see all commands.")


@app.route("/webhook", methods=["POST"])
def receive_message():
    try:
        data = request.json or {}
        entry = data.get("entry", [])
        if not entry:
            return jsonify({"status": "no entry"}), 200
        changes = entry[0].get("changes", [])
        if not changes:
            return jsonify({"status": "no changes"}), 200
        value = changes[0].get("value", {})
        g.phone_number_id = value.get("metadata", {}).get("phone_number_id")
        messages = value.get("messages", [])
        if not messages:
            return jsonify({"status": "no message"}), 200

        message = messages[0]
        user_phone = message.get("from")
        if not user_phone:
            return jsonify({"status": "no phone"}), 200

        mtype = message.get("type")

        # photos, voice notes, videos, files
        if mtype != "text":
            if True:
                if mtype in MEDIA_TYPES:
                    print(f"{mtype} from {user_phone}")
                    forward_media_to_owner(user_phone, mtype, message.get(mtype, {}))
                    label = "photo" if mtype in ("image", "sticker") else (
                        "file" if mtype == "document" else f"{mtype} message")
                    ack = (f"Thanks for sending your {label}! 😊 I've passed it to "
                           f"{OWNER_NAME}, who'll take a look. Is there anything else "
                           f"I can help with?")
                    hist = conversations.setdefault(user_phone, [])
                    hist.append({"role": "user", "content": f"[Customer sent a {label}]"})
                    hist.append({"role": "assistant", "content": ack})
                    if "ALL" not in human_takeover and user_phone not in human_takeover:
                        send_whatsapp_message(user_phone, ack)
                else:
                    send_whatsapp_message(
                        user_phone,
                        f"Hi! I can read text, photos and voice notes. "
                        f"How can I help you with {BUSINESS_NAME}?")
            return jsonify({"status": "ok"}), 200

        user_text = message.get("text", {}).get("body", "").strip()
        if not user_text:
            return jsonify({"status": "empty"}), 200
        print(f"Message from {user_phone}: {user_text}")

        # owner commands (any other message from the owner is answered like a
        # customer's, so you can test the bot from your own phone)
        if user_phone == OWNER_PHONE and user_text.startswith("#"):
            handle_owner_command(user_text)
            return jsonify({"status": "ok"}), 200

        # human has taken over
        if "ALL" in human_takeover or user_phone in human_takeover:
            print(f"Human handling {user_phone} - skipping bot")
            return jsonify({"status": "human_handling"}), 200

        # customer asks for a person
        if any(p in user_text.lower() for p in HUMAN_PHRASES):
            human_takeover.add(user_phone)
            send_whatsapp_message(
                user_phone,
                f"Of course! I'll ask {OWNER_NAME} to get back to you personally.")
            notify_owner(
                f"HUMAN REQUESTED by +{user_phone}. Bot paused for this chat.\n"
                f"Send #bot:{user_phone} when done to resume the bot.")
            return jsonify({"status": "ok"}), 200

        ai_reply = get_ai_reply(user_phone, user_text)
        ai_reply = extract_and_notify_lead(ai_reply, user_phone)
        send_whatsapp_message(user_phone, ai_reply)

        if NOTIFY_EVERY_REPLY:
            notify_owner(f"+{user_phone}: \"{user_text}\"\nBot replied automatically. "
                         f"Send #takeover:{user_phone} to take over.")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error"}), 200


@app.route("/", methods=["GET"])
def home():
    return f"{BUSINESS_NAME} bot is running."


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
