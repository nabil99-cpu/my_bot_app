import os
import time
import logging
import requests
from flask import Flask, request, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiException

from catalog import get_network, get_package, validate_package_belongs_to_network, generate_network_card

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("admin_bot")

# ============================================================================
# ENV
# ============================================================================

def get_env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ValueError(f"Environment variable '{name}' is required but not set")
    return value

ADMIN_BOT_TOKEN = get_env("ADMIN_BOT_TOKEN", required=True)
CLIENT_BOT_TOKEN = get_env("CLIENT_BOT_TOKEN", required=True)
ADMIN_CHAT_ID = get_env("ADMIN_CHAT_ID", required=True)
WEBHOOK_SECRET_TOKEN = get_env("WEBHOOK_SECRET_TOKEN", required=True)
RENDER_EXTERNAL_URL = get_env("RENDER_EXTERNAL_URL")
PORT = int(get_env("PORT", "10001"))
REQUEST_TIMEOUT = int(get_env("REQUEST_TIMEOUT", "15"))

WEBHOOK_BASE_URL = RENDER_EXTERNAL_URL.rstrip("/") if RENDER_EXTERNAL_URL else None

admin_bot = telebot.TeleBot(ADMIN_BOT_TOKEN, threaded=True, parse_mode="HTML")
app = Flask(__name__)
session = requests.Session()

# ============================================================================
# HELPERS
# ============================================================================

def send_to_client(client_id: int, message: str):
    url = f"https://api.telegram.org/bot{CLIENT_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": client_id,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        response = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return True
    except Exception:
        logger.exception("Failed sending message to client_id=%s", client_id)
        return False

def get_webhook_url():
    if not WEBHOOK_BASE_URL:
        raise ValueError("RENDER_EXTERNAL_URL not available")
    return f"{WEBHOOK_BASE_URL}/{ADMIN_BOT_TOKEN}"

def ensure_webhook():
    if not WEBHOOK_BASE_URL:
        logger.warning("RENDER_EXTERNAL_URL not ready")
        return False

    full_url = get_webhook_url()

    try:
        info = admin_bot.get_webhook_info()
        if info.url == full_url:
            logger.info("Admin webhook already configured")
            return True

        admin_bot.remove_webhook()
        time.sleep(1)
        admin_bot.set_webhook(url=full_url, secret_token=WEBHOOK_SECRET_TOKEN)
        logger.info("Admin webhook set: %s", full_url)
        return True
    except Exception:
        logger.exception("Failed to ensure admin webhook")
        return False

def append_status_to_caption(caption: str, status_line: str) -> str:
    if "الحالة:" in caption:
        return caption
    return f"{caption}\n\n{status_line}"

# ============================================================================
# ADMIN ACTIONS
# ============================================================================

@admin_bot.callback_query_handler(
    func=lambda call: call.data.startswith("approve:") or call.data.startswith("reject:")
)
def handle_admin_action(call):
    try:
        action, client_id, network_id, package_id = call.data.split(":", 3)
        client_id = int(client_id)
        original_caption = call.message.caption or ""

        if "الحالة:" in original_caption:
            admin_bot.answer_callback_query(call.id, "تمت معالجة هذا الطلب مسبقًا")
            return

        network = get_network(network_id)
        package = get_package(package_id)

        if not network or not package:
            admin_bot.answer_callback_query(call.id, "❌ الشبكة أو الباقة غير موجودة", show_alert=True)
            return

        if not validate_package_belongs_to_network(network_id, package_id):
            admin_bot.answer_callback_query(call.id, "❌ الباقة لا تنتمي لهذه الشبكة", show_alert=True)
            return

        if action == "approve":
            card_code = generate_network_card(network_id, package)

            client_message = (
                f"✅ <b>تم تأكيد الدفع بنجاح!</b>\n\n"
                f"📡 <b>الشبكة:</b> {network.display_name}\n"
                f"📦 <b>الباقة:</b> {package.name}\n"
                f"🔐 <b>كود الشبكة الخاص بك:</b> <code>{card_code}</code>\n\n"
                f"نتمنى لك تصفحاً ممتعاً."
            )

            if not send_to_client(client_id, client_message):
                admin_bot.answer_callback_query(call.id, "فشل إرسال الكود للعميل", show_alert=True)
                return

            new_caption = append_status_to_caption(
                original_caption,
                f"✅ <b>الحالة:</b> تم القبول وإرسال الكود (<code>{card_code}</code>)"
            )

            admin_bot.edit_message_caption(
                caption=new_caption,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML"
            )

        elif action == "reject":
            client_message = (
                "❌ عذراً، تم رفض إشعار الدفع الخاص بك.\n"
                "يرجى التأكد من وضوح الصورة أو التواصل مع الدعم الفني."
            )

            if not send_to_client(client_id, client_message):
                admin_bot.answer_callback_query(call.id, "فشل إرسال رسالة الرفض للعميل", show_alert=True)
                return

            new_caption = append_status_to_caption(
                original_caption,
                "❌ <b>الحالة:</b> تم الرفض"
            )

            admin_bot.edit_message_caption(
                caption=new_caption,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML"
            )

        admin_bot.answer_callback_query(call.id, "تم تنفيذ العملية بنجاح")

    except Exception:
        logger.exception("Error handling admin action")
        admin_bot.answer_callback_query(call.id, "❌ حدث خطأ داخلي أثناء المعالجة.", show_alert=True)

# ============================================================================
# INTERNAL API
# ============================================================================

@app.route("/new_order", methods=["POST"])
def receive_new_order():
    try:
        data = request.get_json(force=True)

        client_id = data.get("client_id")
        name = data.get("name", "Unknown")
        username = data.get("username", "No Username")

        network_id = data.get("network_id")
        network_name = data.get("network_name")

        package_id = data.get("package_id")
        package_name = data.get("package")
        price = data.get("price")
        data_gb = data.get("data_gb")
        duration_days = data.get("duration_days")

        image_url = data.get("image_url")

        if not all([client_id, network_id, package_id, package_name, image_url]):
            return jsonify({"status": "error", "message": "Missing required fields"}), 400

        caption = (
            f"🔔 <b>طلب شراء جديد!</b>\n\n"
            f"👤 <b>العميل:</b> {name} (@{username})\n"
            f"🆔 <b>الآيدي:</b> <code>{client_id}</code>\n"
            f"📡 <b>الشبكة:</b> {network_name}\n"
            f"📦 <b>الباقة:</b> {package_name}\n"
            f"💰 <b>المبلغ:</b> {price} ريال\n"
            f"💾 <b>البيانات:</b> {data_gb} جيجا\n"
            f"📅 <b>المدة:</b> {duration_days} يوم\n"
        )

        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                "✅ موافق (إرسال كرت)",
                callback_data=f"approve:{client_id}:{network_id}:{package_id}"
            ),
            InlineKeyboardButton(
                "❌ رفض الطلب",
                callback_data=f"reject:{client_id}:{network_id}:{package_id}"
            )
        )

        admin_bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=image_url,
            caption=caption,
            parse_mode="HTML",
            reply_markup=markup
        )

        logger.info(
            "Forwarded new order client_id=%s network_id=%s package_id=%s",
            client_id, network_id, package_id
        )
        return jsonify({"status": "success"}), 200

    except Exception:
        logger.exception("Error processing /new_order")
        return jsonify({"status": "error", "message": "internal error"}), 500

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route("/", methods=["GET"])
def health_check():
    return "Admin Bot is running!", 200

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok", "service": "admin_bot"}), 200

@app.route("/webhook_info", methods=["GET"])
def webhook_info():
    try:
        info = admin_bot.get_webhook_info()
        return jsonify({
            "url": info.url,
            "pending_update_count": info.pending_update_count,
            "last_error_date": info.last_error_date,
            "last_error_message": info.last_error_message,
            "max_connections": info.max_connections
        }), 200
    except Exception:
        logger.exception("Failed to fetch admin webhook info")
        return jsonify({"error": "failed"}), 500

@app.route("/set_webhook", methods=["GET"])
def set_webhook_route():
    ok = ensure_webhook()
    return jsonify({"ok": ok}), 200 if ok else 500

@app.route(f"/{ADMIN_BOT_TOKEN}", methods=["POST"])
def handle_admin_webhook():
    try:
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if received_secret != WEBHOOK_SECRET_TOKEN:
            logger.warning("Invalid admin webhook secret")
            return "Unauthorized", 403

        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        admin_bot.process_new_updates([update])
        return "OK", 200

    except ApiException:
        logger.exception("Telegram API error in admin webhook")
        return "ERROR", 500
    except Exception:
        logger.exception("Unexpected error in admin webhook")
        return "ERROR", 500

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting Admin Bot...")
    ensure_webhook()
    app.run(host="0.0.0.0", port=PORT, debug=False)
