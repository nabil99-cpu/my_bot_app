import os
import sqlite3
import logging
import time
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from enum import Enum
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiException
from flask import Flask, request, jsonify

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("client_bot")

# ============================================================================
# ENV
# ============================================================================

def get_env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise ValueError(f"Environment variable '{name}' is required but not set")
    return value

TOKEN = get_env("TELEGRAM_BOT_TOKEN", required=True)
ADMIN_BOT_URL = get_env("ADMIN_BOT_URL", required=True).rstrip("/")
WEBHOOK_SECRET_TOKEN = get_env("WEBHOOK_SECRET_TOKEN", required=True)
RENDER_EXTERNAL_URL = get_env("RENDER_EXTERNAL_URL")
PORT = int(get_env("PORT", "10000"))
DB_PATH = get_env("DB_PATH", "client_bot.db")
REQUEST_TIMEOUT = int(get_env("REQUEST_TIMEOUT", "15"))
MAX_WORKERS = int(get_env("MAX_WORKERS", "8"))

WEBHOOK_BASE_URL = RENDER_EXTERNAL_URL.rstrip("/") if RENDER_EXTERNAL_URL else None

bot = telebot.TeleBot(TOKEN, threaded=True, parse_mode="HTML")
app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# ============================================================================
# MODELS
# ============================================================================

class UserState(Enum):
    WAITING_FOR_PAYMENT = "waiting_for_payment"
    ORDER_SUBMITTED = "order_submitted"


@dataclass(frozen=True)
class Package:
    id: str
    name: str
    price: int
    data_gb: str
    duration_days: int


PACKAGES: Dict[str, Package] = {
    "pkg1": Package("pkg1", "5 جيجا - 7 أيام", 1500, "5", 7),
    "pkg2": Package("pkg2", "15 جيجا - 30 يوم", 4000, "15", 30),
    "pkg3": Package("pkg3", "غير محدود - 30 يوم", 8000, "∞", 30),
}

PAYMENT_INFO = """💳 <b>طرق الدفع:</b>
1. كريمي: <code>1234 5678 9012 3456</code>
2. كاش: <code>771234567</code> - أحمد
3. صرافة القطيبي

📸 <b>بعد التحويل، قم بإرسال صورة الإشعار هنا مباشرة:</b>"""

# ============================================================================
# DB
# ============================================================================

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    @contextmanager
    def get_connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_states (
                    user_id INTEGER PRIMARY KEY,
                    selected_package TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    package_id TEXT NOT NULL,
                    image_url TEXT,
                    status TEXT DEFAULT 'pending',
                    admin_response TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        logger.info("Database initialized")

    def update_user_state(self, user_id: int, package_key: str, state: UserState):
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO user_states (user_id, selected_package, state, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    selected_package=excluded.selected_package,
                    state=excluded.state,
                    updated_at=CURRENT_TIMESTAMP
            """, (user_id, package_key, state.value))

    def get_user_state(self, user_id: int) -> Optional[Tuple[str, str]]:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT selected_package, state FROM user_states WHERE user_id = ?",
                (user_id,)
            ).fetchone()
            return (row["selected_package"], row["state"]) if row else None

    def clear_user_state(self, user_id: int):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))

    def save_order(self, user_id: int, package_id: str, image_url: str, status: str, admin_response: str = ""):
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO orders (user_id, package_id, image_url, status, admin_response)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, package_id, image_url, status, admin_response))


db = DatabaseManager(DB_PATH)

# ============================================================================
# ADMIN API CLIENT
# ============================================================================

class AdminAPIClient:
    def __init__(self, base_url: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def submit_order(self, user_id: int, package: Package, user_info: Dict[str, Any], image_url: str):
        payload = {
            "client_id": user_id,
            "name": user_info.get("first_name") or "User",
            "username": user_info.get("username") or "No Username",
            "package": package.name,
            "price": package.price,
            "image_url": image_url,
            "data_gb": package.data_gb,
            "duration_days": package.duration_days,
        }

        try:
            response = self.session.post(
                f"{self.base_url}/new_order",
                json=payload,
                timeout=self.timeout
            )
            text_preview = response.text[:500] if response.text else ""
            if 200 <= response.status_code < 300:
                return True, text_preview

            logger.error("Admin API error status=%s body=%s", response.status_code, text_preview)
            return False, text_preview

        except Exception as e:
            logger.exception("Admin API submit failed")
            return False, str(e)


admin_api = AdminAPIClient(ADMIN_BOT_URL, REQUEST_TIMEOUT)

# ============================================================================
# WEBHOOK HELPERS
# ============================================================================

def get_webhook_url():
    if not WEBHOOK_BASE_URL:
        raise ValueError("RENDER_EXTERNAL_URL not available")
    return f"{WEBHOOK_BASE_URL}/{TOKEN}"

def ensure_webhook():
    if not WEBHOOK_BASE_URL:
        logger.warning("RENDER_EXTERNAL_URL not ready")
        return False

    full_url = get_webhook_url()
    try:
        info = bot.get_webhook_info()
        if info.url == full_url:
            logger.info("Client webhook already configured")
            return True

        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=full_url, secret_token=WEBHOOK_SECRET_TOKEN)
        logger.info("Client webhook set: %s", full_url)
        return True
    except Exception:
        logger.exception("Failed to ensure client webhook")
        return False

# ============================================================================
# BOT HELPERS
# ============================================================================

def build_packages_markup():
    markup = InlineKeyboardMarkup()
    for key, pkg in PACKAGES.items():
        markup.add(InlineKeyboardButton(f"{pkg.name} - {pkg.price} ريال", callback_data=key))
    return markup

def process_order_async(user_id, pkg_key, pkg, message, processing_msg, image_url, user_info):
    try:
        ok, admin_response = admin_api.submit_order(user_id, pkg, user_info, image_url)

        if ok:
            db.save_order(user_id, pkg_key, image_url, "submitted", admin_response)
            db.update_user_state(user_id, pkg_key, UserState.ORDER_SUBMITTED)

            bot.edit_message_text(
                "✅ تم استلام الإشعار وإرساله للإدارة بنجاح.\n\n"
                "سيتم التأكيد وتفعيل الباقة خلال دقائق، ستصلك رسالة قريباً.",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id
            )
            db.clear_user_state(user_id)
        else:
            db.save_order(user_id, pkg_key, image_url, "failed", admin_response)
            bot.edit_message_text(
                "❌ حدث خطأ أثناء إرسال طلبك للإدارة.\nالرجاء المحاولة لاحقاً أو مراسلة الدعم.",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id
            )

    except Exception:
        logger.exception("Error in process_order_async")
        try:
            bot.edit_message_text(
                "❌ حدث خطأ غير متوقع أثناء معالجة الطلب.",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id
            )
        except Exception:
            logger.exception("Failed to edit processing message")

# ============================================================================
# BOT HANDLERS
# ============================================================================

@bot.message_handler(commands=["start"])
def handle_start(message):
    try:
        bot.send_message(
            message.chat.id,
            "أهلاً بك في شبكتنا 🌐\nالرجاء اختيار الباقة المناسبة لك:",
            reply_markup=build_packages_markup()
        )
    except Exception:
        logger.exception("Error in /start")
        bot.reply_to(message, "❌ حدث خطأ أثناء عرض الباقات.")

@bot.callback_query_handler(func=lambda call: call.data in PACKAGES)
def handle_package_selection(call):
    try:
        pkg_key = call.data
        pkg = PACKAGES[pkg_key]
        user_id = call.from_user.id

        db.update_user_state(user_id, pkg_key, UserState.WAITING_FOR_PAYMENT)

        text = (
            f"📦 طلبت: <b>{pkg.name}</b>\n"
            f"💰 المبلغ: <b>{pkg.price} ريال</b>\n"
            f"💾 البيانات: <b>{pkg.data_gb} جيجا</b>\n"
            f"📅 المدة: <b>{pkg.duration_days} يوم</b>\n\n"
            f"{PAYMENT_INFO}"
        )

        bot.edit_message_text(
            text=text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        bot.answer_callback_query(call.id, "تم اختيار الباقة")
    except Exception:
        logger.exception("Error in package selection")
        bot.answer_callback_query(call.id, "❌ حدث خطأ", show_alert=True)

@bot.message_handler(content_types=["photo"])
def handle_payment_proof(message):
    user_id = message.from_user.id

    try:
        state_data = db.get_user_state(user_id)
        if not state_data or state_data[1] != UserState.WAITING_FOR_PAYMENT.value:
            bot.reply_to(message, "⚠️ الرجاء إرسال /start واختيار الباقة أولاً قبل إرسال الإشعار.")
            return

        pkg_key = state_data[0]
        pkg = PACKAGES.get(pkg_key)
        if not pkg:
            db.clear_user_state(user_id)
            bot.reply_to(message, "❌ الباقة غير موجودة. أرسل /start من جديد.")
            return

        processing_msg = bot.reply_to(message, "⏳ جاري معالجة الإشعار وإرساله للإدارة...")

        file_info = bot.get_file(message.photo[-1].file_id)
        image_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"

        user_info = {
            "first_name": message.from_user.first_name or "User",
            "username": message.from_user.username
        }

        executor.submit(
            process_order_async,
            user_id, pkg_key, pkg, message, processing_msg, image_url, user_info
        )

    except ApiException:
        logger.exception("Telegram API error in handle_payment_proof")
        bot.reply_to(message, "❌ خطأ في الاتصال مع Telegram. حاول لاحقاً.")
    except Exception:
        logger.exception("Unexpected error in handle_payment_proof")
        bot.reply_to(message, "❌ حدث خطأ غير متوقع. الرجاء المحاولة لاحقاً.")

@bot.message_handler(func=lambda message: True)
def handle_default_message(message):
    bot.reply_to(message, "أرسل /start للبدء باختيار الباقة.")

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route("/", methods=["GET"])
def health_check():
    return "Client Bot is running!", 200

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok", "service": "client_bot"}), 200

@app.route("/webhook_info", methods=["GET"])
def webhook_info():
    try:
        info = bot.get_webhook_info()
        return jsonify({
            "url": info.url,
            "pending_update_count": info.pending_update_count,
            "last_error_date": info.last_error_date,
            "last_error_message": info.last_error_message,
            "max_connections": info.max_connections
        }), 200
    except Exception:
        logger.exception("Failed to get webhook info")
        return jsonify({"error": "failed"}), 500

@app.route("/set_webhook", methods=["GET"])
def set_webhook_route():
    ok = ensure_webhook()
    return jsonify({"ok": ok}), 200 if ok else 500

@app.route(f"/{TOKEN}", methods=["POST"])
def handle_telegram_webhook():
    try:
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if received_secret != WEBHOOK_SECRET_TOKEN:
            logger.warning("Invalid client webhook secret")
            return "Unauthorized", 403

        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([update])
        return "OK", 200

    except Exception:
        logger.exception("Client webhook error")
        return "ERROR", 500

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting Client Bot...")
    ensure_webhook()
    app.run(host="0.0.0.0", port=PORT, debug=False)
