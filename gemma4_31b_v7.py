import telebot
from telebot import apihelper
import requests
import time
from datetime import datetime
import json
import threading
import os
import sqlite3

# اتصال به سرور بله به جای تلگرام
apihelper.API_URL = "https://tapi.bale.ai/bot{0}/{1}"

# مشخصات پروژه و اطلاعات اتصال مستقیم شما
BOT_TOKEN = "*"
API_KEY = "*"
BASE_ROUTE = "gemma_31b"

if not BASE_ROUTE.endswith("/chat/completions"):
    ENDPOINT_URL = f"{BASE_ROUTE.rstrip('/')}/chat/completions"
else:
    ENDPOINT_URL = BASE_ROUTE

bot = telebot.TeleBot(BOT_TOKEN)

# دستورالعمل سیستم: بخش اول (شخصیت، لحن و جریان گام‌به‌گام گفتگو)

SYSTEM_INSTRUCTION = """

harchi mikhay be AI begi ke pishfarz bashe inja benevis

"""

# متغیرها و تنظیمات اصلی دیتابیس ربات
chat_histories = {}
MAX_HISTORY_PAIRS = 5  # افزایش کانتکست زنده برای مدیریت سناریوی دقیق لید کوالیفای
DB_FILE = "chat_history.db"

def get_time():
    return datetime.now().strftime("%H:%M:%S")

def init_db():
    """ایجاد ساختار پایگاه‌داده لوکال سرور با پشتیبانی از نام کاربری و نام نمایشی"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            chat_id INTEGER,
            username TEXT,
            display_name TEXT,
            role TEXT,
            message TEXT
        )
    """)
    # 🟢 خط جدید: ساخت ایندکس روی آیدی کاربر برای جستجوی میلی‌ثانیه‌ای در آینده
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON messages_log (chat_id)")
    conn.commit()
    conn.close()
    print(f"[{get_time()}] 🗄️ Database initialized with username support.")

def log_message_to_db(chat_id, username, display_name, role, message_text):
    """ذخیره دائمی و امن پیام‌ها به همراه هوش مانیتورینگ کاربری در دیتابیس لوکال"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO messages_log (timestamp, chat_id, username, display_name, role, message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (current_timestamp, chat_id, username, display_name, role, message_text))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[{get_time()}] ⚠️ خطای پایگاه‌داده: {e}")

def load_chat_history(chat_id):
    """بازیابی تاریخچه مکالمات کاربر از دیتابیس پس از ری‌استارت ربات"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        limit = MAX_HISTORY_PAIRS * 2
        # واکشی آخرین پیام‌های کاربر و ربات به ترتیب زمان
        cursor.execute("""
            SELECT role, message FROM (
                SELECT id, role, message FROM messages_log 
                WHERE chat_id=? AND role IN ('user', 'assistant')
                ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
        """, (chat_id, limit))
        rows = cursor.fetchall()
        conn.close()
        
        history = []
        for row in rows:
            history.append({"role": row[0], "content": row[1]})
        return history
    except Exception as e:
        print(f"[{get_time()}] ⚠️ خطا در بازیابی تاریخچه از دیتابیس: {e}")
        return []

def send_bale_safe_reply(chat_id, text, reply_to_message_id):
    """ارسال پیام با متد POST مستقیم به صورت JSON برای جلوگیری از خطای URI Too Large"""
    url = f"https://tapi.bale.ai/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_to_message_id": reply_to_message_id
    }
    headers = {"Content-Type": "application/json"}
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        return res.status_code == 200
    except Exception as e:
        print(f"[{get_time()}] ⚠️ خطای مستقیم در ارسال به بله: {e}")
        return False

def split_and_send_messages(chat_id, text, reply_to_message_id):
    """تکه‌تکه کردن متون طولانی برای عبور از سقف کاراکتر پیام‌رسان بله"""
    max_length = 3900
    if len(text) <= max_length:
        return send_bale_safe_reply(chat_id, text, reply_to_message_id)
    
    print(f"[{get_time()}] ✂️ متن خروجی طولانی است. در حال تکه‌تکه کردن...")
    for i in range(0, len(text), max_length):
        chunk = text[i:i+max_length]
        send_bale_safe_reply(chat_id, chunk, reply_to_message_id)
    return True

def keep_typing_loop(chat_id, stop_event):
    """زنده نگه داشتن حالت Typing در بله تا زمان دریافت پاسخ از کلاود آروان"""
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
        except:
            pass
        time.sleep(4)

# ==================================================================
# بخش دستورات ادمین (نظارت هوشمند و خروجی تفکیک‌شده و هویت‌دار به صورت JSON)
# ==================================================================

ADMIN_IDS = [459826104]  

@bot.message_handler(commands=['users'])
def list_bot_users(message):
    """لیست کردن تمام کاربرانی که با ربات چت کرده‌اند به همراه نام کاربری واقعی کاربر (ویژه ادمین)"""
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                chat_id, 
                MAX(CASE WHEN role = 'user' THEN username END), 
                MAX(CASE WHEN role = 'user' THEN display_name END), 
                MAX(timestamp), 
                COUNT(*) 
            FROM messages_log 
            GROUP BY chat_id 
            ORDER BY MAX(timestamp) DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            bot.reply_to(message, "📭 هنوز هیچ مکالمه‌ای در دیتابیس ثبت نشده است.")
            return

        report = "�� **لیست هویت‌دار کاربران ربات و تعداد پیام‌ها:**\n\n"
        for row in rows:
            chat_id, username, display_name, last_time, msg_count = row
            
            final_display = display_name if display_name else "کاربر ناشناس"
            user_info = f"@{username}" if username and username != "بدون_یوزرنیم" else "بدون آیدی"
            
            report += f"👤 نام: *{final_display}* ({user_info})\n"
            report += f"🆔 آیدی عددی: `{chat_id}`\n"
            report += f"⏳ آخرین فعالیت: {last_time}\n"
            report += f"💬 تعداد پیام: {msg_count}\n"
            # 🔴 تغییر اول: برداشتن بک‌تیک و اتصال با آندرلاین برای کلیک‌دار شدن
            report += f"📥 دریافت تاریخچه: /history_{chat_id}\n"
            report += f"{'-'*30}\n"

        bot.reply_to(message, report, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ خطا در واکشی اطلاعات: {str(e)}")


# 🔴 تغییر دوم: تغییر هندلر به نحوی که هم دستور با فاصله و هم دستور با آندرلاین را پشتیبانی کند
@bot.message_handler(func=lambda message: message.text and message.text.startswith('/history'))
def send_user_history_json(message):
    """استخراج چت یک کاربر خاص و ارسال آن به صورت فایل JSON هویت‌دار واقعی (ویژه ادمین)"""
    if message.from_user.id not in ADMIN_IDS:
        return

    filename = None
    try:
        # جایگزین کردن آندرلاین با فاصله برای جدا کردن راحت آیدی
        command_parts = message.text.replace('/history_', '/history ').split()
        if len(command_parts) < 2:
            bot.reply_to(message, "⚠️ لطفا آیدی کاربر را وارد کنید.\nمثال: /history_459826104")
            return

        target_chat_id = int(command_parts[1])

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, username, display_name, role, message 
            FROM messages_log 
            WHERE chat_id=? 
            ORDER BY id ASC
        """, (target_chat_id,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            bot.reply_to(message, f"❓ هیچ چتی برای آیدی {target_chat_id} پیدا نشد.")
            return

        user_username = "بدون_یوزرنیم"
        user_display_name = "کاربر ناشناس"
        for row in rows:
            if row[3] == "user":
                user_username = row[1]
                user_display_name = row[2]
                break

        export_structure = {
            "chat_id": target_chat_id,
            "username": f"@{user_username}" if user_username and user_username != "بدون_یوزرنیم" else "None",
            "display_name": user_display_name,
            "total_messages_logged": len(rows),
            "conversations": []
        }

        for row in rows:
            export_structure["conversations"].append({
                "timestamp": row[0],
                "role": row[3],
                "message": row[4]
            })

        filename = f"User_{target_chat_id}_History.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(export_structure, f, ensure_ascii=False, indent=4)

        with open(filename, "rb") as f:
            bot.send_document(
                message.chat.id, 
                f, 
                caption=f"📦 فایل JSON اختصاصی مکالمات:\n👤 کاربر: {user_display_name}\n🆔 آیدی: `{target_chat_id}`"
            )

    except ValueError:
        bot.reply_to(message, "❌ آیدی وارد شده باید به صورت عددی باشد.")
    except Exception as e:
        bot.reply_to(message, f"💥 خطای سیستمی: {str(e)}")
    finally:
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except:
                pass

# ==================================================================
# بخش پردازش پیام‌های کاربران عمومی (موتور ارزیابی و غربالگری لید)
# ==================================================================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    """پاکسازی کانتکست قبلی و آماده‌سازی مدل برای شروع گام ۰ سناریو"""
    chat_id = message.chat.id
    chat_histories[chat_id] = []
    print(f"[{get_time()}] �� تاریخچه چت کاربر {chat_id} برای ارزیابی جدید پاکسازی شد.")
    
    # برای اینکه ربات گام 0 سناریو را فوراً اجرا کند، پیام استارت را به بدنه اصلی هدایت می‌کنیم
    handle_customer_message(message)

@bot.message_handler(func=lambda message: True)
def handle_customer_message(message):
    chat_id = message.chat.id
    user_text = message.text
    
    # استخراج دیتای هویتی زنده از پروفایل بله کاربر
    username = message.from_user.username if message.from_user.username else "بدون_یوزرنیم"
    first_name = message.from_user.first_name if message.from_user.first_name else ""
    last_name = message.from_user.last_name if message.from_user.last_name else ""
    display_name = f"{first_name} {last_name}".strip()
    if not display_name:
        display_name = "کاربر ناشناس"

    print("\n" + "🔍" * 20 + " شروع پردازش لید مهاجرتی " + "🔍" * 20)
    print(f"[{get_time()}] 📩 پیام از {display_name} (@{username}): {user_text}")
    
    # ۱. ثبت فوری پیام دریافتی کاربر به همراه جزییات یوزرنیم در دیتابیس
# 🟢 تغییر جدید: اگر ربات ری‌استارت شده و حافظه‌اش خالیست، چت‌های قبلی را از دیتابیس لود کن
    if chat_id not in chat_histories:
        chat_histories[chat_id] = load_chat_history(chat_id)
        print(f"[{get_time()}] ♻️ حافظه بازیابی شد: {len(chat_histories[chat_id])} پیام قبلی برای {display_name} لود شد.")

    # ۱. ثبت فوری پیام دریافتی کاربر به همراه جزییات یوزرنیم در دیتابیس
    # ۱. ثبت فوری پیام دریافتی کاربر به همراه جزییات یوزرنیم در دیتابیس
    log_message_to_db(chat_id, username, display_name, "user", user_text)
    
    max_retries = 3  # افزایش دفعات تلاش به ۳ بار برای بالا بردن شانس موفقیت ربات
    
    for attempt in range(max_retries):
        stop_typing_event = threading.Event()
        typing_thread = threading.Thread(target=keep_typing_loop, args=(chat_id, stop_typing_event))
        typing_thread.daemon = True
        typing_thread.start()
            
        try:
            # چیدمان پکیج آرایه پیام‌ها برای ارسال به مدل با حفظ پرامپت سیستم
            messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
            messages.extend(chat_histories[chat_id])
            messages.append({"role": "user", "content": user_text})
            
            headers = {
                "Authorization": f"apikey {API_KEY.strip()}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "Gemma-4-31B-IT",
                "messages": messages,
                "temperature": 0.5,        # تعادل بهینه برای حفظ دقیق لحن محاوره‌ای و منطق سناریو
                "max_tokens": 10000
            }
            
            print(f"[{get_time()}] 🚀 در حال ارسال وضعیت لید به کلاود آروان (تلاش {attempt + 1} از {max_retries})...")
            start_time = time.time()
            
            response = requests.post(ENDPOINT_URL, headers=headers, json=payload, timeout=50)
            
            stop_typing_event.set()
            typing_thread.join()
            
            elapsed = time.time() - start_time
            print(f"[{get_time()}] ⏱️ زمان پاسخ‌گویی سرور آروان: {elapsed:.2f} ثانیه")
            print(f"[{get_time()}] 🚦 کد وضعیت HTTP کلاود: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                bot_reply = result['choices'][0]['message']['content']
                print(f"[{get_time()}] ✅ پاسخ غربالگری از مدل دریافت شد.") 
                
                # ۲. ثبت پاسخ موفق هوش مصنوعی با مشخصات بات در پایگاه‌داده
                log_message_to_db(chat_id, "Gemma_Bot", "دستیار هوشمند", "assistant", bot_reply)
                
                # بروزرسانی کانتکست زنده حافظه موقت برای گام بعدی چت
                chat_histories[chat_id].append({"role": "user", "content": user_text})
                chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
                
                # اعمال محدودیت سقف کانتکست جهت بهینه‌سازی سرعت لود مدل
                if len(chat_histories[chat_id]) > MAX_HISTORY_PAIRS * 2:
                    chat_histories[chat_id] = chat_histories[chat_id][-(MAX_HISTORY_PAIRS * 2):]
                
                bale_start = time.time()
                success = split_and_send_messages(chat_id, bot_reply, message.message_id)
                
                if success:
                    print(f"[{get_time()}] 📤 پاسخ مشاوره با موفقیت در {time.time() - bale_start:.2f} ثانیه به بله فرستاده شد.")
                else:
                    print(f"[{get_time()}] ❌ ارسال پاسخ به بله با شکست مواجه شد.")
                
                break  # موفقیت‌آمیز بود! از حلقه تلاش مجدد خارج شو.
                
            else:
                print(f"[{get_time()}] ❌ خطا در سرور آروان: {response.text}")
                if attempt < max_retries - 1:
                    if attempt == 0: # فقط دفعه اول این پیام رو بده که رو اعصاب کاربر نره
                        send_bale_safe_reply(chat_id, "لطفاً چند لحظه کوتاه صبر کن...", message.message_id)
                    time.sleep(3)  # ۳ ثانیه صبر برای رفع اختلال شبکه قبل از تلاش مجدد
                else:
                    send_bale_safe_reply(chat_id, "رفیق متاسفانه الان سیستم خیلی شلوغه و نتونستم بررسی رو کامل کنم. لطفاً پیام آخرت رو دوباره برام بفرست.", message.message_id)
                
        except requests.exceptions.Timeout:
            stop_typing_event.set()
            try: typing_thread.join() 
            except: pass
            print(f"[{get_time()}] 🚨 خطای زمان پایان (Timeout) از آروان.")
            if attempt < max_retries - 1:
                if attempt == 0:
                    send_bale_safe_reply(chat_id, "لطفاً چند لحظه کوتاه صبر کن...", message.message_id)
                time.sleep(3)
            else:
                send_bale_safe_reply(chat_id, "رفیق متاسفانه الان سیستم خیلی شلوغه و نتونستم بررسی رو کامل کنم. لطفاً پیام آخرت رو دوباره برام بفرست.", message.message_id)
                
        except Exception as e:
            stop_typing_event.set()
            try: typing_thread.join()
            except: pass
            print(f"[{get_time()}] 💥 خطای عمومی سیستم: {str(e)}")
            if attempt < max_retries - 1:
                if attempt == 0:
                    send_bale_safe_reply(chat_id, "لطفاً چند لحظه کوتاه صبر کن...", message.message_id)
                time.sleep(3)
            else:
                send_bale_safe_reply(chat_id, "رفیق متاسفانه الان سیستم خیلی شلوغه و نتونستم بررسی رو کامل کنم. لطفاً پیام آخرت رو دوباره برام بفرست.", message.message_id)
        
    print("🔍" * 45 + "\n")

# ==================================================================
# نقطه اجرای نهایی و پولینگ دائمی پروژه
# ==================================================================
if __name__ == "__main__":
    init_db()  # بررسی، اصلاح ستون‌ها و راه‌اندازی پایگاه‌داده لوکال سرور لینوکس
    print(f"[{get_time()}] 🚀 ربات ارزیابی و لید کوالیفای هوشمند Gemma با موفقیت استارت شد...")
    while True:
        try:
            bot.infinity_polling()
        except Exception as e:
            print(f"[{get_time()}] 🔄 خطا در پولینگ بله: {e}. تلاش مجدد تا ۵ ثانیه دیگر...")
            time.sleep(5)
