import os
import random
import sqlite3
import logging
import asyncio
import json
import time
from io import BytesIO
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, RetryAfter, TimedOut, Conflict
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [6101127840, 8189353497]
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/")
ADD_GROUP_URL = os.getenv("ADD_GROUP_URL", "https://t.me/KGBCASINOBOT?startgroup=true")

DB_DIR = os.getenv("DB_DIR", ".")
os.makedirs(DB_DIR, exist_ok=True)
DB_NAME = os.path.join(DB_DIR, "casino.db")

START_BALANCE = 10000
DAILY_REWARD = 500
WEEKLY_REWARD = 2000
VIP_DAILY_BONUS = 1000
VIP_DURATION_DAYS = 7
BANK_INTEREST_RATE = 0.03
XP_PER_GAME = 10
XP_PER_WIN = 25

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

if not TOKEN:
    raise ValueError("BOT_TOKEN bulunamadı.")

# =========================
# DATABASE
# =========================
print("--- VERİTABANI YOLU KONTROL ---")
print("DB_DIR:", DB_DIR)
print("DB_NAME:", DB_NAME)
print("----------------------------")

conn = sqlite3.connect(DB_NAME, check_same_thread=False)
cursor = conn.cursor()


def now():
    return datetime.utcnow()


def now_iso():
    return now().isoformat()


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def init_db():
    cursor.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        sikke INTEGER DEFAULT 1000,
        bank INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        total_won INTEGER DEFAULT 0,
        total_lost INTEGER DEFAULT 0,
        games_played INTEGER DEFAULT 0,
        games_won INTEGER DEFAULT 0,
        last_daily TEXT DEFAULT NULL,
        last_weekly TEXT DEFAULT NULL,
        vip_until TEXT DEFAULT NULL,
        daily_streak INTEGER DEFAULT 0,
        last_streak_claim TEXT DEFAULT NULL,
        last_interest TEXT DEFAULT NULL,
        created_at TEXT DEFAULT NULL
    )""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS game_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        game_name TEXT,
        bet INTEGER,
        result TEXT,
        amount_change INTEGER,
        created_at TEXT
    )""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        item_name TEXT,
        quantity INTEGER DEFAULT 1
    )""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS achievements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        achievement_name TEXT,
        unlocked_at TEXT
    )""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS missions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        mission_name TEXT,
        progress INTEGER DEFAULT 0,
        target INTEGER DEFAULT 1,
        reward INTEGER DEFAULT 0,
        claimed INTEGER DEFAULT 0
    )""")

    cursor.execute("""CREATE TABLE IF NOT EXISTS group_leaderboard (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER,
        user_id INTEGER,
        total_won INTEGER DEFAULT 0,
        total_played INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT NULL,
        UNIQUE(group_id, user_id)
    )""")

    conn.commit()


# =========================
# HELPERS
# =========================
def format_number(n):
    return f"{n:,}".replace(",", ".")


def format_timedelta(td):
    total = int(td.total_seconds())
    days, rem = total // 86400, total % 86400
    hours, minutes = rem // 3600, (rem % 3600) // 60
    return f"{days}g {hours}s {minutes}dk" if days > 0 else f"{hours}s {minutes}dk"


def is_valid_amount(amount):
    return amount > 0


def get_display_name(user):
    return user.first_name or user.username or str(user.id)


def is_admin(user_id):
    return user_id in ADMINS


def fixed_url(url):
    if not url:
        return "https://t.me/"
    url = url.strip()
    if url.startswith("@"):
        return f"https://t.me/{url[1:]}"
    if url.startswith("http"):
        return url
    if url.startswith("t.me/"):
        return "https://" + url
    return f"https://t.me/{url}"


def is_group_chat(update: Update) -> bool:
    return update.effective_chat.type in ("group", "supergroup")


def short_result_prefix(update: Update):
    return "👥 Grup Modu\n" if is_group_chat(update) else ""


# =========================
# DB FUNCTIONS
# =========================
def daily_remaining(last_daily):
    if not last_daily:
        return None
    parsed = parse_time(last_daily)
    if not parsed:
        return None
    remain = timedelta(days=1) - (now() - parsed)
    return remain if remain.total_seconds() > 0 else None


def weekly_remaining(last_weekly):
    if not last_weekly:
        return None
    parsed = parse_time(last_weekly)
    if not parsed:
        return None
    remain = timedelta(days=7) - (now() - parsed)
    return remain if remain.total_seconds() > 0 else None


def interest_remaining(last_interest):
    if not last_interest:
        return None
    parsed = parse_time(last_interest)
    if not parsed:
        return None
    remain = timedelta(days=1) - (now() - parsed)
    return remain if remain.total_seconds() > 0 else None


def create_default_missions(user_id):
    cursor.execute("SELECT COUNT(*) FROM missions WHERE user_id=?", (user_id,))
    if cursor.fetchone()[0] > 0:
        return
    for m in [("İlk Oyunun", 0, 1, 250, 0), ("5 Oyun Oyna", 0, 5, 500, 0), ("3 Oyun Kazan", 0, 3, 750, 0)]:
        cursor.execute(
            "INSERT INTO missions (user_id, mission_name, progress, target, reward, claimed) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, *m)
        )
    conn.commit()


def get_user(user_id, username):
    cursor.execute("SELECT sikke FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        cursor.execute("""INSERT INTO users (
            user_id, username, sikke, bank, xp, level, total_won, total_lost, games_played, games_won,
            last_daily, last_weekly, vip_until, daily_streak, last_streak_claim, last_interest, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (user_id, username, START_BALANCE, 0, 0, 1, 0, 0, 0, 0, None, None, None, 0, None, None, now_iso()))
        conn.commit()
        create_default_missions(user_id)
        return START_BALANCE

    cursor.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
    conn.commit()
    return row[0]


def get_user_row(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()


def get_balance(user_id):
    cursor.execute("SELECT sikke FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0


def get_bank(user_id):
    cursor.execute("SELECT bank FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0


def update_balance(user_id, amount):
    current = get_balance(user_id)
    new_value = current + amount
    if new_value < 0:
        return False
    cursor.execute("UPDATE users SET sikke=? WHERE user_id=?", (new_value, user_id))
    conn.commit()
    return True


def set_balance(user_id, amount):
    if amount < 0:
        return False
    cursor.execute("UPDATE users SET sikke=? WHERE user_id=?", (amount, user_id))
    conn.commit()
    return True


def update_bank(user_id, amount):
    current = get_bank(user_id)
    new_value = current + amount
    if new_value < 0:
        return False
    cursor.execute("UPDATE users SET bank=? WHERE user_id=?", (new_value, user_id))
    conn.commit()
    return True


def set_bank_value(user_id, amount):
    if amount < 0:
        return False
    cursor.execute("UPDATE users SET bank=? WHERE user_id=?", (amount, user_id))
    conn.commit()
    return True


def add_stats(user_id, won=0, lost=0, played=0, games_won=0):
    cursor.execute(
        "UPDATE users SET total_won = total_won + ?, total_lost = total_lost + ?, games_played = games_played + ?, games_won = games_won + ? WHERE user_id=?",
        (won, lost, played, games_won, user_id)
    )
    conn.commit()


def add_xp(user_id, amount):
    cursor.execute("SELECT xp, level FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return None
    xp, level = row
    xp += amount
    leveled_up = False
    while xp >= level * 100:
        xp -= level * 100
        level += 1
        leveled_up = True
    cursor.execute("UPDATE users SET xp=?, level=? WHERE user_id=?", (xp, level, user_id))
    conn.commit()
    return leveled_up, level, xp


def log_game(user_id, game_name, bet, result, amount_change):
    cursor.execute(
        "INSERT INTO game_logs (user_id, game_name, bet, result, amount_change, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, game_name, bet, result, amount_change, now_iso())
    )
    conn.commit()


def set_daily(user_id):
    cursor.execute("UPDATE users SET last_daily=? WHERE user_id=?", (now_iso(), user_id))
    conn.commit()


def set_weekly(user_id):
    cursor.execute("UPDATE users SET last_weekly=? WHERE user_id=?", (now_iso(), user_id))
    conn.commit()


def get_vip_until(user_id):
    cursor.execute("SELECT vip_until FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else None


def is_vip(user_id):
    vip_until = get_vip_until(user_id)
    if not vip_until:
        return False
    parsed = parse_time(vip_until)
    return bool(parsed and parsed > now())


def vip_remaining(user_id):
    vip_until = get_vip_until(user_id)
    if not vip_until:
        return None
    parsed = parse_time(vip_until)
    if not parsed:
        return None
    remain = parsed - now()
    return remain if remain.total_seconds() > 0 else None


def activate_vip(user_id, days=VIP_DURATION_DAYS):
    current = get_vip_until(user_id)
    parsed = parse_time(current)
    new_time = (parsed + timedelta(days=days)) if (parsed and parsed > now()) else (now() + timedelta(days=days))
    cursor.execute("UPDATE users SET vip_until=? WHERE user_id=?", (new_time.isoformat(), user_id))
    conn.commit()
    return new_time


def get_streak_info(user_id):
    cursor.execute("SELECT daily_streak, last_streak_claim FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return (row[0] or 0, row[1]) if row else (0, None)


def update_streak_on_daily(user_id):
    streak, last_claim = get_streak_info(user_id)
    today = now().date()
    parsed = parse_time(last_claim) if last_claim else None
    last_dt = parsed.date() if parsed else None
    if last_dt is None:
        streak = 1
    else:
        diff = (today - last_dt).days
        if diff == 1:
            streak += 1
        elif diff == 0:
            pass
        else:
            streak = 1
    cursor.execute("UPDATE users SET daily_streak=?, last_streak_claim=? WHERE user_id=?", (streak, now_iso(), user_id))
    conn.commit()
    return streak


def get_last_interest(user_id):
    cursor.execute("SELECT last_interest FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else None


def set_last_interest(user_id):
    cursor.execute("UPDATE users SET last_interest=? WHERE user_id=?", (now_iso(), user_id))
    conn.commit()


def add_item(user_id, item_name, qty=1):
    cursor.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_name=?", (user_id, item_name))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE inventory SET quantity = quantity + ? WHERE user_id=? AND item_name=?", (qty, user_id, item_name))
    else:
        cursor.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, ?)", (user_id, item_name, qty))
    conn.commit()


def remove_item(user_id, item_name, qty=1):
    cursor.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_name=?", (user_id, item_name))
    row = cursor.fetchone()
    if not row:
        return False
    if row[0] < qty:
        return False
    new_qty = row[0] - qty
    if new_qty == 0:
        cursor.execute("DELETE FROM inventory WHERE user_id=? AND item_name=?", (user_id, item_name))
    else:
        cursor.execute("UPDATE inventory SET quantity=? WHERE user_id=? AND item_name=?", (new_qty, user_id, item_name))
    conn.commit()
    return True


def has_item(user_id, item_name):
    cursor.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_name=?", (user_id, item_name))
    row = cursor.fetchone()
    return row is not None and row[0] > 0


def get_inventory(user_id):
    cursor.execute("SELECT item_name, quantity FROM inventory WHERE user_id=? ORDER BY item_name ASC", (user_id,))
    return cursor.fetchall()


def has_achievement(user_id, name):
    cursor.execute("SELECT id FROM achievements WHERE user_id=? AND achievement_name=?", (user_id, name))
    return cursor.fetchone() is not None


def unlock_achievement(user_id, name):
    if has_achievement(user_id, name):
        return False
    cursor.execute("INSERT INTO achievements (user_id, achievement_name, unlocked_at) VALUES (?, ?, ?)", (user_id, name, now_iso()))
    conn.commit()
    return True


def get_achievements(user_id):
    cursor.execute("SELECT achievement_name, unlocked_at FROM achievements WHERE user_id=? ORDER BY id DESC", (user_id,))
    return cursor.fetchall()


def update_missions_played(user_id):
    cursor.execute("UPDATE missions SET progress = progress + 1 WHERE user_id=? AND mission_name IN ('İlk Oyunun', '5 Oyun Oyna')", (user_id,))
    conn.commit()


def update_missions_won(user_id):
    cursor.execute("UPDATE missions SET progress = progress + 1 WHERE user_id=? AND mission_name='3 Oyun Kazan'", (user_id,))
    conn.commit()


def get_missions(user_id):
    cursor.execute("SELECT id, mission_name, progress, target, reward, claimed FROM missions WHERE user_id=? ORDER BY id ASC", (user_id,))
    return cursor.fetchall()


def claim_mission(user_id, mission_id):
    cursor.execute("SELECT progress, target, reward, claimed FROM missions WHERE id=? AND user_id=?", (mission_id, user_id))
    row = cursor.fetchone()
    if not row:
        return False, "Görev bulunamadı.", 0
    progress, target, reward, claimed = row
    if claimed:
        return False, "Bu ödül zaten alınmış.", 0
    if progress < target:
        return False, "Görev henüz tamamlanmadı.", 0
    cursor.execute("UPDATE missions SET claimed=1 WHERE id=?", (mission_id,))
    conn.commit()
    update_balance(user_id, reward)
    return True, "Görev ödülü alındı.", reward


def top_users(limit=10):
    cursor.execute("SELECT username, sikke, bank, level FROM users ORDER BY (sikke + bank) DESC LIMIT ?", (limit,))
    return cursor.fetchall()


def global_stats():
    cursor.execute("SELECT COUNT(*) FROM users")
    users = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(sikke + bank) FROM users")
    money = cursor.fetchone()[0] or 0
    cursor.execute("SELECT COUNT(*) FROM game_logs")
    logs = cursor.fetchone()[0]
    return users, money, logs


def reset_user(user_id):
    cursor.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    cursor.execute("DELETE FROM inventory WHERE user_id=?", (user_id,))
    cursor.execute("DELETE FROM achievements WHERE user_id=?", (user_id,))
    cursor.execute("DELETE FROM missions WHERE user_id=?", (user_id,))
    cursor.execute("DELETE FROM game_logs WHERE user_id=?", (user_id,))
    conn.commit()

# =========================
# GROUP LEADERBOARD
# =========================
def update_group_leaderboard(group_id, user_id, won=0, played=0):
    cursor.execute("SELECT id FROM group_leaderboard WHERE group_id=? AND user_id=?", (group_id, user_id))
    if cursor.fetchone():
        cursor.execute(
            "UPDATE group_leaderboard SET total_won = total_won + ?, total_played = total_played + ?, updated_at=? WHERE group_id=? AND user_id=?",
            (won, played, now_iso(), group_id, user_id)
        )
    else:
        cursor.execute(
            "INSERT INTO group_leaderboard (group_id, user_id, total_won, total_played, updated_at) VALUES (?, ?, ?, ?, ?)",
            (group_id, user_id, won, played, now_iso())
        )
    conn.commit()


def get_group_top(group_id, limit=10):
    cursor.execute("""
        SELECT gl.user_id, u.username, gl.total_won, gl.total_played, u.level
        FROM group_leaderboard gl
        JOIN users u ON gl.user_id = u.user_id
        WHERE gl.group_id=?
        ORDER BY gl.total_won DESC, gl.total_played DESC
        LIMIT ?
    """, (group_id, limit))
    return cursor.fetchall()


def get_global_rank(user_id):
    cursor.execute("SELECT user_id FROM users ORDER BY (sikke + bank) DESC")
    rows = [r[0] for r in cursor.fetchall()]
    return rows.index(user_id) + 1 if user_id in rows else 0

# =========================
# PROFILE CARD
# =========================
def load_font(size):
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def get_tier_name(level):
    if level >= 50:
        return "EFSANEVI", (255, 215, 0)
    elif level >= 30:
        return "ELMAS", (0, 255, 255)
    elif level >= 20:
        return "ALTIN", (255, 200, 0)
    elif level >= 10:
        return "GUMUS", (180, 180, 180)
    return "BRONZ", (205, 127, 50)


def generate_profile_card(user_id):
    row = get_user_row(user_id)
    if not row:
        return None

    username = row[1] or str(user_id)
    sikke = row[2]
    bank = row[3]
    xp = row[4]
    level = row[5]
    total_won = row[6]
    total_lost = row[7]
    games_played = row[8]
    games_won = row[9]
    streak = row[13]

    total = sikke + bank
    winrate = round((games_won / games_played) * 100, 1) if games_played else 0
    rank = get_global_rank(user_id)
    tier_name, tier_color = get_tier_name(level)

    width, height = 1000, 560
    img = Image.new("RGB", (width, height), (15, 18, 28))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((20, 20, 980, 540), radius=28, fill=(24, 28, 40), outline=tier_color, width=3)
    draw.rounded_rectangle((40, 40, 960, 140), radius=24, fill=(35, 40, 58))
    draw.rounded_rectangle((40, 160, 470, 510), radius=24, fill=(28, 33, 48))
    draw.rounded_rectangle((490, 160, 960, 510), radius=24, fill=(28, 33, 48))

    f_title = load_font(40)
    f_big = load_font(28)
    f_mid = load_font(22)

    draw.text((65, 58), "PROFIL KARTI", font=f_title, fill=(255, 255, 255))
    draw.text((700, 58), tier_name, font=f_big, fill=tier_color)

    uname = str(username)[:22]
    draw.text((65, 110), f"@{uname}", font=f_mid, fill=(170, 180, 210))

    draw.text((65, 185), "GENEL", font=f_big, fill=(255, 255, 255))
    draw.text((65, 235), f"Seviye: {level}", font=f_mid, fill=(255, 255, 255))
    draw.text((65, 275), f"XP: {xp}", font=f_mid, fill=(255, 255, 255))
    draw.text((65, 315), f"Streak: {streak}", font=f_mid, fill=(255, 120, 120))
    draw.text((65, 355), f"Global Sira: #{rank}", font=f_mid, fill=(255, 215, 0))
    draw.text((65, 395), f"VIP: {'AKTIF' if is_vip(user_id) else 'YOK'}", font=f_mid, fill=(120, 255, 200))

    draw.text((515, 185), "ISTATISTIK", font=f_big, fill=(255, 255, 255))
    draw.text((515, 235), f"Cuzdan: {format_number(sikke)}", font=f_mid, fill=(255, 255, 255))
    draw.text((515, 275), f"Banka: {format_number(bank)}", font=f_mid, fill=(255, 255, 255))
    draw.text((515, 315), f"Toplam Servet: {format_number(total)}", font=f_mid, fill=(0, 255, 140))
    draw.text((515, 355), f"Toplam Kazanc: {format_number(total_won)}", font=f_mid, fill=(0, 255, 140))
    draw.text((515, 395), f"Toplam Kayip: {format_number(total_lost)}", font=f_mid, fill=(255, 110, 110))
    draw.text((515, 435), f"Oyun: {games_played} | Galibiyet: {games_won}", font=f_mid, fill=(255, 255, 255))
    draw.text((515, 475), f"Winrate: %{winrate}", font=f_mid, fill=(255, 215, 0))

    bar_x1, bar_y1, bar_x2, bar_y2 = 65, 500, 935, 520
    draw.rounded_rectangle((bar_x1, bar_y1, bar_x2, bar_y2), radius=10, fill=(50, 55, 75))
    xp_need = max(level * 100, 1)
    fill_ratio = min(xp / xp_need, 1.0)
    fill_width = int((bar_x2 - bar_x1) * fill_ratio)
    draw.rounded_rectangle((bar_x1, bar_y1, bar_x1 + fill_width, bar_y2), radius=10, fill=tier_color)

    bio = BytesIO()
    bio.name = "profile_card.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio

# =========================
# UI / MENUS / MISSING FIXES
# =========================
MARKET_ITEMS = {
    "vip_ticket": {"name": "VIP Bilet", "price": 25000},
    "lucky_box": {"name": "Şans Kutusu", "price": 5000},
}

def start_text(name):
    return (
        "╔══════════════════════╗\n"
        "      🎰 <b>CASINO BOTA HOŞ GELDİN</b>\n"
        "╚══════════════════════╝\n\n"
        f"👋 Merhaba <b>{name}</b>\n\n"
        "Bu botta oyun oynayabilir, ödül toplayabilir,\n"
        "profilini geliştirebilir ve marketi kullanabilirsin.\n\n"
        "Aşağıdaki menüden devam et."
    )

def home_panel(row, user_id):
    if not row:
        return "❌ Profil verisi bulunamadı."
    cash = row[2]
    bankv = row[3]
    total = cash + bankv
    vip_tag = "💎 <b>VIP Aktif</b>\n" if is_vip(user_id) else ""
    return (
        "╔══════════════════════╗\n"
        "        🏠 <b>ANA PANEL</b>\n"
        "╚══════════════════════╝\n\n"
        f"{vip_tag}"
        f"👛 Cüzdan: <b>{format_number(cash)} 🪙</b>\n"
        f"🏦 Banka: <b>{format_number(bankv)} 🪙</b>\n"
        f"📦 Toplam: <b>{format_number(total)} 🪙</b>\n\n"
        "Aşağıdan bir seçenek seç."
    )

def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("💰 Bakiye", callback_data="menu_balance"),
            InlineKeyboardButton("📊 Profil", callback_data="menu_profile"),
        ],
        [
            InlineKeyboardButton("🎮 Oyunlar", callback_data="menu_games"),
            InlineKeyboardButton("🏆 Sıralama", callback_data="menu_top"),
        ],
        [
            InlineKeyboardButton("🎨 Kartım", callback_data="menu_mycard"),
            InlineKeyboardButton("ℹ️ Yardım", callback_data="menu_help"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def nav_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="back_main")]
    ])

def games_menu():
    keyboard = [
        [
            InlineKeyboardButton("🎡 Rulet", callback_data="info_rulet"),
            InlineKeyboardButton("🃏 Blackjack", callback_data="info_blackjack"),
        ],
        [
            InlineKeyboardButton("♠️ Poker", callback_data="info_poker"),
            InlineKeyboardButton("🎰 Slot", callback_data="info_slot"),
        ],
        [
            InlineKeyboardButton("🎲 Zar", callback_data="info_zar"),
            InlineKeyboardButton("🏀 Basket", callback_data="info_basket"),
        ],
        [
            InlineKeyboardButton("🪙 Coinflip", callback_data="info_coinflip"),
            InlineKeyboardButton("🔢 Guess", callback_data="info_guess"),
        ],
        [
            InlineKeyboardButton("📈 HighLow", callback_data="info_highlow"),
            InlineKeyboardButton("🚀 Crash", callback_data="info_crash"),
        ],
        [
            InlineKeyboardButton("💣 Mines", callback_data="info_mines"),
            InlineKeyboardButton("⚔️ Duel", callback_data="info_duel"),
        ],
        [InlineKeyboardButton("⬅️ Geri", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def safe_edit(message, text, reply_markup=None, min_interval=0):
    if min_interval > 0:
        await asyncio.sleep(min_interval)
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

async def animated_panel(message, frames, delay=0.8, fast_mode=False):
    actual_delay = 0.35 if fast_mode else delay
    first = True
    for frame in frames:
        try:
            if first:
                await safe_edit(message, frame)
                first = False
            else:
                await asyncio.sleep(actual_delay)
                await safe_edit(message, frame)
        except Exception:
            pass

def check_achievements(user_id):
    row = get_user_row(user_id)
    if not row:
        return

    total_won = row[6]
    games_played = row[8]
    games_won = row[9]
    level = row[5]

    if games_played >= 1:
        unlock_achievement(user_id, "İlk Oyun")
    if games_won >= 1:
        unlock_achievement(user_id, "İlk Galibiyet")
    if total_won >= 10000:
        unlock_achievement(user_id, "10K Kazanç")
    if level >= 10:
        unlock_achievement(user_id, "Seviye 10")
    if is_vip(user_id):
        unlock_achievement(user_id, "VIP Oyuncu")

def process_game_result(user_id, game_name, bet, result, profit=0):
    level_up_text = ""

    if result == "win":
        update_balance(user_id, profit)
        add_stats(user_id, won=profit, played=1, games_won=1)
        level_info = add_xp(user_id, XP_PER_GAME + XP_PER_WIN)
        log_game(user_id, game_name, bet, "win", profit)
        update_missions_played(user_id)
        update_missions_won(user_id)
    elif result == "lose":
        update_balance(user_id, -bet)
        add_stats(user_id, lost=bet, played=1, games_won=0)
        level_info = add_xp(user_id, XP_PER_GAME)
        log_game(user_id, game_name, bet, "lose", -bet)
        update_missions_played(user_id)
    else:
        add_stats(user_id, played=1, games_won=0)
        level_info = add_xp(user_id, XP_PER_GAME)
        log_game(user_id, game_name, bet, "draw", 0)
        update_missions_played(user_id)

    check_achievements(user_id)

    if level_info and level_info[0]:
        level_up_text = f"\n\n🎉 <b>Level atladın!</b> Yeni seviye: <b>{level_info[1]}</b>"

    return level_up_text

# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message:
        return
    get_user(user.id, get_display_name(user))
    await update.message.reply_text(
        start_text(get_display_name(user)),
        parse_mode="HTML",
        reply_markup=main_menu()
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)
    await update.message.reply_text(home_panel(row, user.id), parse_mode="HTML", reply_markup=main_menu())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("╔══════════════════════╗\n      ℹ️ <b>YARDIM PANELİ</b>\n╚══════════════════════╝\n\n"
            "• /start • /menu • /help • /balance • /profile • /top • /grouptop • /mycard\n"
            "• /vip • /bank • /deposit • /withdraw • /gonder\n"
            "• /gunluk • /haftalik • /faiz • /stats\n"
            "• /market • /buy • /inventory • /missions • /claim • /achievements • /use\n\n"
            "🎮 Oyunlar\n"
            "• /rulet [miktar] [kırmızı/siyah]\n"
            "• /blackjack [miktar]\n"
            "• /poker [miktar]\n"
            "• /slot [miktar]\n"
            "• /zar [miktar]\n"
            "• /basket [miktar]\n"
            "• /coinflip [miktar] [yazi/tura]\n"
            "• /guess [miktar] [1-5]\n"
            "• /highlow [miktar] [high/low]\n"
            "• /crash [miktar]\n"
            "• /mines [miktar]\n"
            "• /duel [miktar] (reply)")
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())


async def vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    remain = vip_remaining(user.id)
    if remain:
        text = ("╔══════════════════════╗\n        💎 <b>VIP PANEL</b>\n╚══════════════════════╝\n\n"
                "Durum: <b>Aktif ✅</b>\n"
                f"Kalan Süre: <b>{format_timedelta(remain)}</b>\n"
                f"Günlük VIP bonus: <b>{format_number(VIP_DAILY_BONUS)} 🪙</b>\n\n"
                "VIP bileti kullanmak için:\n• /use vip_ticket")
    else:
        text = ("╔══════════════════════╗\n        💎 <b>VIP PANEL</b>\n╚══════════════════════╝\n\n"
                "Durum: <b>Pasif ❌</b>\n"
                f"VIP süresi: <b>{VIP_DURATION_DAYS} gün</b>\n"
                f"Günlük VIP bonus: <b>{format_number(VIP_DAILY_BONUS)} 🪙</b>\n\n"
                "VIP için:\n• /buy vip_ticket\n• /use vip_ticket")
    await update.message.reply_text(text, parse_mode="HTML")


async def use_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        item_code = context.args[0].lower()
    except Exception:
        await update.message.reply_text("Kullanım: /use [item_adi]\nÖrnek: /use vip_ticket")
        return

    if item_code == "vip_ticket":
        if not has_item(user.id, "VIP Bilet"):
            await update.message.reply_text("❌ Envanterinde VIP Bilet yok.")
            return

        remove_item(user.id, "VIP Bilet", 1)
        until = activate_vip(user.id, VIP_DURATION_DAYS)
        check_achievements(user.id)

        await update.message.reply_text(
            f"💎 <b>VIP aktif edildi!</b>\n\n"
            f"Süre: <b>{VIP_DURATION_DAYS} gün</b>\n"
            f"Bitiş: <b>{until.strftime('%Y-%m-%d %H:%M')}</b>\n"
            f"Günlük bonusun artık <b>{format_number(VIP_DAILY_BONUS)} 🪙</b>",
            parse_mode="HTML"
        )
        return

    await update.message.reply_text("❌ Bu item kullanılamıyor.")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)
    cash, bankv = row[2], row[3]
    vip_tag = "💎 <b>VIP Oyuncu</b>\n" if is_vip(user.id) else ""
    await update.message.reply_text(
        f"{vip_tag}💰 <b>Cüzdan:</b> {format_number(cash)} 🪙\n"
        f"🏦 <b>Banka:</b> {format_number(bankv)} 🪙\n"
        f"📦 <b>Toplam:</b> {format_number(cash + bankv)} 🪙",
        parse_mode="HTML"
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)
    total = row[2] + row[3]
    games_played, games_won = row[8], row[9]
    winrate = round((games_won / games_played) * 100, 1) if games_played else 0
    vip_tag = "💎 VIP\n" if is_vip(user.id) else ""
    rank = get_global_rank(user.id)
    text = ("╔══════════════════════╗\n       📊 <b>PROFİL</b>\n╚══════════════════════╝\n\n"
            f"{vip_tag}"
            f"👤 İsim: <b>{row[1]}</b>\n"
            f"🆔 ID: <code>{row[0]}</code>\n"
            f"⭐ Level: <b>{row[5]}</b>\n"
            f"✨ XP: <b>{row[4]}</b>\n"
            f"🔥 Streak: <b>{row[13]}</b>\n"
            f"🏆 Global Sıra: <b>#{rank}</b>\n"
            f"💰 Servet: <b>{format_number(total)} 🪙</b>\n"
            f"🎮 Oyun: <b>{games_played}</b>\n"
            f"🏆 Galibiyet: <b>{games_won}</b>\n"
            f"📈 Winrate: <b>%{winrate}</b>\n"
            f"✅ Toplam Kazanç: <b>{format_number(row[6])} 🪙</b>\n"
            f"❌ Toplam Kayıp: <b>{format_number(row[7])} 🪙</b>\n\n"
            f"🎨 Kart: /mycard")
    await update.message.reply_text(text, parse_mode="HTML")


async def gunluk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)
    remain = daily_remaining(row[10])
    if remain:
        await update.message.reply_text(f"⏳ Günlük ödül hazır değil: <b>{format_timedelta(remain)}</b>", parse_mode="HTML")
        return
    reward = VIP_DAILY_BONUS if is_vip(user.id) else DAILY_REWARD
    streak = update_streak_on_daily(user.id)
    streak_bonus = min(streak * 50, 500)
    total_reward = reward + streak_bonus
    update_balance(user.id, total_reward)
    set_daily(user.id)
    check_achievements(user.id)
    vip_text = "\n💎 VIP bonus uygulandı!" if is_vip(user.id) else ""
    await update.message.reply_text(
        f"🎁 <b>Günlük ödül alındı!</b>\n"
        f"Asıl ödül: <b>{format_number(reward)} 🪙</b>\n"
        f"🔥 Streak bonusu: <b>{format_number(streak_bonus)} 🪙</b>\n"
        f"Toplam: <b>{format_number(total_reward)} 🪙</b>\n"
        f"Streak: <b>{streak}</b>{vip_text}",
        parse_mode="HTML"
    )


async def haftalik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)
    remain = weekly_remaining(row[11])
    if remain:
        await update.message.reply_text(f"⏳ Haftalık ödül hazır değil: <b>{format_timedelta(remain)}</b>", parse_mode="HTML")
        return
    update_balance(user.id, WEEKLY_REWARD)
    set_weekly(user.id)
    await update.message.reply_text(f"🎁 <b>Haftalık ödül alındı!</b>\n+{format_number(WEEKLY_REWARD)} 🪙", parse_mode="HTML")


async def faiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    last_interest = get_last_interest(user.id)
    remain = interest_remaining(last_interest)
    if remain:
        await update.message.reply_text(f"⏳ Faiz almak için bekle: <b>{format_timedelta(remain)}</b>", parse_mode="HTML")
        return
    bank_balance = get_bank(user.id)
    if bank_balance <= 0:
        await update.message.reply_text("❌ Bankada para yok.")
        return
    interest = int(bank_balance * BANK_INTEREST_RATE)
    if interest <= 0:
        await update.message.reply_text("❌ Faiz hesaplanamadı.")
        return
    update_bank(user.id, interest)
    set_last_interest(user.id)
    await update.message.reply_text(
        f"🏦 <b>Faiz alındı!</b>\nOran: <b>%{int(BANK_INTEREST_RATE * 100)}</b>\nKazanç: <b>+{format_number(interest)} 🪙</b>",
        parse_mode="HTML"
    )


async def bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    await update.message.reply_text(f"🏦 <b>Banka:</b> {format_number(get_bank(user.id))} 🪙\n💸 Faiz için: /faiz", parse_mode="HTML")


async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        amount = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /deposit [miktar]")
        return
    if not is_valid_amount(amount):
        await update.message.reply_text("❌ Geçerli miktar gir.")
        return
    if get_balance(user.id) < amount:
        await update.message.reply_text("❌ Cüzdanda yeterli para yok.")
        return
    if update_balance(user.id, -amount) and update_bank(user.id, amount):
        await update.message.reply_text(f"🏦 Bankaya yatırıldı: <b>{format_number(amount)} 🪙</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ İşlem başarısız.")


async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        amount = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /withdraw [miktar]")
        return
    if not is_valid_amount(amount):
        await update.message.reply_text("❌ Geçerli miktar gir.")
        return
    if get_bank(user.id) < amount:
        await update.message.reply_text("❌ Bankada yeterli para yok.")
        return
    if update_bank(user.id, -amount) and update_balance(user.id, amount):
        await update.message.reply_text(f"🏦 Bankadan çekildi: <b>{format_number(amount)} 🪙</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ İşlem başarısız.")


async def gonder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply ile kullan.\nÖrnek: /gonder 500")
        return
    sender = update.effective_user
    receiver = update.message.reply_to_message.from_user
    if sender.id == receiver.id:
        await update.message.reply_text("❌ Kendine para gönderemezsin.")
        return
    get_user(sender.id, get_display_name(sender))
    get_user(receiver.id, get_display_name(receiver))
    try:
        amount = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /gonder [miktar]")
        return
    if not is_valid_amount(amount):
        await update.message.reply_text("❌ Geçerli miktar gir.")
        return
    if get_balance(sender.id) < amount:
        await update.message.reply_text("❌ Yetersiz bakiye.")
        return
    ok1 = update_balance(sender.id, -amount)
    ok2 = update_balance(receiver.id, amount)
    if ok1 and ok2:
        await update.message.reply_text(f"💸 <b>{get_display_name(receiver)}</b> kullanıcısına <b>{format_number(amount)} 🪙</b> gönderildi.", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ Transfer başarısız.")


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "╔══════════════════════╗\n      🛒 <b>PREMIUM MARKET</b>\n╚══════════════════════╝\n\n"
    for code, item in MARKET_ITEMS.items():
        text += f"🎟 <b>{item['name']}</b>\n💰 Fiyat: <b>{format_number(item['price'])} 🪙</b>\n🧾 Kod: <code>{code}</code>\n\n"
    text += "Satın almak için:\n• /buy [item_kodu]"
    await update.message.reply_text(text, parse_mode="HTML")


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        code = context.args[0]
    except Exception:
        await update.message.reply_text("Kullanım: /buy [item_kodu]")
        return
    if code not in MARKET_ITEMS:
        await update.message.reply_text("❌ Böyle bir ürün yok.")
        return
    item = MARKET_ITEMS[code]
    price = item["price"]
    if get_balance(user.id) < price:
        await update.message.reply_text("❌ Yetersiz bakiye.")
        return
    if not update_balance(user.id, -price):
        await update.message.reply_text("❌ Satın alma başarısız.")
        return
    add_item(user.id, item["name"], 1)

    if code == "lucky_box":
        bonus = random.randint(500, 3000)
        update_balance(user.id, bonus)
        await update.message.reply_text(
            f"🛒 Satın alındı: <b>{item['name']}</b>\n💰 -{format_number(price)} 🪙\n🎁 Kutudan çıktı: <b>+{format_number(bonus)} 🪙</b>",
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(f"🛒 Satın alındı: <b>{item['name']}</b>\n💰 -{format_number(price)} 🪙", parse_mode="HTML")


async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    items = get_inventory(user.id)
    if not items:
        await update.message.reply_text("🎒 Envanterin boş.")
        return
    text = "╔══════════════════════╗\n      🎒 <b>PREMIUM ENVANTER</b>\n╚══════════════════════╝\n\n"
    for name, qty in items:
        text += f"• <b>{name}</b> × {qty}\n"
    text += "\nKullanım:\n• /use [item_adi]"
    await update.message.reply_text(text, parse_mode="HTML")


async def achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    achs = get_achievements(user.id)
    if not achs:
        await update.message.reply_text("🏅 Henüz başarımın yok.")
        return
    text = "╔══════════════════════╗\n      🏅 <b>BAŞARIM GALERİSİ</b>\n╚══════════════════════╝\n\n"
    for name, unlocked_at in achs:
        text += f"🏅 {name}\n"
    await update.message.reply_text(text, parse_mode="HTML")


async def missions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    data = get_missions(user.id)
    text = "╔══════════════════════╗\n      📜 <b>GÖREV PANOSU</b>\n╚══════════════════════╝\n\n"
    for m in data:
        mission_id, name, progress, target, reward, claimed = m
        status = "✅ Alındı" if claimed else ("🎯 Hazır" if progress >= target else "⏳ Devam")
        percent = int((progress / target) * 100) if target > 0 else 0
        if percent > 100:
            percent = 100
        text += f"🆔 <code>{mission_id}</code>\n<b>{name}</b>\nİlerleme: {progress}/{target} (%{percent})\nÖdül: {format_number(reward)} 🪙\nDurum: {status}\n\n"
    text += "Ödül almak için:\n• /claim [görev_id]"
    await update.message.reply_text(text, parse_mode="HTML")


async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        mission_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /claim [görev_id]")
        return
    ok, message, reward = claim_mission(user.id, mission_id)
    if not ok:
        await update.message.reply_text(f"❌ {message}")
        return
    await update.message.reply_text(f"🎁 <b>{message}</b>\n+{format_number(reward)} 🪙", parse_mode="HTML")


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = top_users(20)
    text = "╔══════════════════════╗\n      🏆 <b>GLOBAL LIDERLIK</b>\n╚══════════════════════╝\n\n"

    if not rows:
        text += "Henuz siralamada oyuncu yok."
        await update.message.reply_text(text, parse_mode="HTML")
        return

    for i, row in enumerate(rows, start=1):
        total = (row[1] or 0) + (row[2] or 0)
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
        username = row[0] or "Oyuncu"
        badge = "🔥" if total >= 100000 else "💎" if total >= 50000 else "⭐" if total >= 10000 else "•"
        text += (
            f"{medal} <b>{i}.</b> {username}\n"
            f"    {badge} Servet: <b>{format_number(total)} 🪙</b>\n"
            f"    🎚 Seviye: <b>{row[3]}</b>\n\n"
        )

    await update.message.reply_text(text, parse_mode="HTML")


async def grouptop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group_chat(update):
        await update.message.reply_text("❌ Bu komut sadece gruplarda kullanılır.")
        return

    group_id = update.effective_chat.id
    rows = get_group_top(group_id, 15)

    text = "╔══════════════════════╗\n      👥 <b>GRUP LIDERLIGI</b>\n╚══════════════════════╝\n\n"

    if not rows:
        text += "Bu grupta henuz oyun oynayan yok."
        await update.message.reply_text(text, parse_mode="HTML")
        return

    for i, row in enumerate(rows, start=1):
        user_id, username, total_won, total_played, level = row
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
        username = username or f"Oyuncu {user_id}"
        text += (
            f"{medal} <b>{i}.</b> {username}\n"
            f"    💰 Grup Kazanci: <b>{format_number(total_won)} 🪙</b>\n"
            f"    🎮 Oyun: <b>{total_played}</b> | ⭐ Lv.<b>{level}</b>\n\n"
        )

    await update.message.reply_text(text, parse_mode="HTML")


async def mycard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    msg = await update.message.reply_text("🎨 Profil karti hazirlaniyor...")

    try:
        card = generate_profile_card(user.id)
        if not card:
            await safe_edit(msg, "❌ Profil karti olusturulamadi.", min_interval=0.5)
            return

        await update.message.reply_photo(
            photo=card,
            caption=f"📊 <b>{get_display_name(user)}</b> profil karti",
            parse_mode="HTML"
        )
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as e:
        logging.error(f"Profil karti hatasi: {e}")
        try:
            await safe_edit(msg, "❌ Profil karti olusturulamadi.", min_interval=0.5)
        except Exception:
            await update.message.reply_text("❌ Profil karti olusturulamadi.")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users, money, logs = global_stats()
    await update.message.reply_text(
        f"📊 <b>Global İstatistik</b>\n\n"
        f"👥 Kullanıcı: {users}\n"
        f"💰 Toplam Varlık: {format_number(money)} 🪙\n"
        f"🎮 Toplam Oyun Logu: {format_number(logs)}",
        parse_mode="HTML"
    )


# =========================
# ADMIN COMMANDS
# =========================
async def addcoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Yetkin yok.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Bir kullanıcı mesajına reply yaparak kullan.\nÖrnek: /addcoin 5000")
        return

    target = update.message.reply_to_message.from_user
    get_user(target.id, get_display_name(target))

    try:
        amount = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /addcoin [miktar]\nÖrnek: /addcoin 5000")
        return

    if amount == 0:
        await update.message.reply_text("❌ 0 miktar kullanamazsın.")
        return

    if amount < 0 and get_balance(target.id) < abs(amount):
        await update.message.reply_text("❌ Kullanıcının bakiyesi yeterli değil.")
        return

    update_balance(target.id, amount)

    if amount > 0:
        await update.message.reply_text(f"✅ {target.first_name} kullanıcısına +{format_number(amount)} 🪙 verildi.")
    else:
        await update.message.reply_text(f"✅ {target.first_name} kullanıcısından {format_number(abs(amount))} 🪙 kesildi.")


async def setcoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Yetkin yok.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Bir kullanıcı mesajına reply yaparak kullan.\nÖrnek: /setcoin 50000")
        return

    target = update.message.reply_to_message.from_user
    get_user(target.id, get_display_name(target))

    try:
        amount = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /setcoin [miktar]")
        return

    if amount < 0:
        await update.message.reply_text("❌ Negatif değer kullanamazsın.")
        return

    set_balance(target.id, amount)
    await update.message.reply_text(f"✅ {target.first_name} kullanıcısının cüzdanı {format_number(amount)} 🪙 olarak ayarlandı.")


async def setbank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Yetkin yok.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Bir kullanıcı mesajına reply yaparak kullan.\nÖrnek: /setbank 100000")
        return

    target = update.message.reply_to_message.from_user
    get_user(target.id, get_display_name(target))

    try:
        amount = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /setbank [miktar]")
        return

    if amount < 0:
        await update.message.reply_text("❌ Negatif değer kullanamazsın.")
        return

    set_bank_value(target.id, amount)
    await update.message.reply_text(f"✅ {target.first_name} kullanıcısının bankası {format_number(amount)} 🪙 olarak ayarlandı.")


async def resetuser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Yetkin yok.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Bir kullanıcı mesajına reply yaparak kullan.")
        return

    target = update.message.reply_to_message.from_user
    reset_user(target.id)
    await update.message.reply_text(f"⚠️ {target.first_name} kullanıcısının tüm verisi sıfırlandı.")


async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    if not is_admin(admin.id):
        await update.message.reply_text("❌ Yetkin yok.")
        return

    data = {}
    cursor.execute("SELECT * FROM users"); data["users"] = cursor.fetchall()
    cursor.execute("SELECT * FROM inventory"); data["inventory"] = cursor.fetchall()
    cursor.execute("SELECT * FROM achievements"); data["achievements"] = cursor.fetchall()
    cursor.execute("SELECT * FROM missions"); data["missions"] = cursor.fetchall()
    cursor.execute("SELECT * FROM game_logs"); data["game_logs"] = cursor.fetchall()

    filename = f"backup_{int(datetime.utcnow().timestamp())}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    with open(filename, "rb") as f:
        await update.message.reply_document(f, filename=filename, caption="✅ Yedek alındı")


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 ID: <code>{update.effective_user.id}</code>", parse_mode="HTML")


# =========================
# GAMES
# =========================
async def rulet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
        color = context.args[1].lower()
    except Exception:
        await update.message.reply_text("Kullanım: /rulet [miktar] [kırmızı/siyah]")
        return

    if color not in ["kırmızı", "siyah"]:
        await update.message.reply_text("❌ kırmızı veya siyah yaz.")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    msg = await update.message.reply_text("🎡 <b>Rulet hazırlanıyor...</b>", parse_mode="HTML")
    result = random.choice(["kırmızı", "siyah"])

    await animated_panel(
        msg,
        [
            f"🎡 <b>Rulet dönüyor...</b>\n\n💰 Bahis: {format_number(bet)} 🪙\n🎯 Seçim: {color}",
            "⚫ 🔴 ⚫ 🔴 ⚫\n<b>Son...</b>"
        ],
        delay=0.8,
        fast_mode=fast
    )

    if color == result:
        lvl = process_game_result(user.id, "rulet", bet, "win", bet)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=bet, played=1)
        text = f"{short_result_prefix(update)}🎡 <b>RULET</b>\n\n🎯 {color} | 🎡 {result}\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "rulet", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🎡 <b>RULET</b>\n\n🎯 {color} | 🎡 {result}\n\n😢 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main() if not fast else None, min_interval=1.0)


async def blackjack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /blackjack [miktar]")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    player, botv = random.randint(15, 21), random.randint(15, 21)
    msg = await update.message.reply_text("🃏 <b>Kartlar dağıtılıyor...</b>", parse_mode="HTML")

    await animated_panel(msg, [f"🃏 <b>Açılıyor...</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>"], delay=0.8, fast_mode=fast)

    if player > botv:
        lvl = process_game_result(user.id, "blackjack", bet, "win", bet)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=bet, played=1)
        text = f"{short_result_prefix(update)}🃏 <b>BLACKJACK</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    elif player < botv:
        lvl = process_game_result(user.id, "blackjack", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🃏 <b>BLACKJACK</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "blackjack", bet, "draw")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🃏 <b>BLACKJACK</b>\n\n🤝 Berabere{lvl}"

    await safe_edit(msg, text, nav_main() if not fast else None, min_interval=1.0)


async def poker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /poker [miktar]")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    player, botv = random.randint(1, 100), random.randint(1, 100)
    msg = await update.message.reply_text("♠️ <b>Poker eli hazırlanıyor...</b>", parse_mode="HTML")

    await animated_panel(msg, [f"♠️ <b>Sonuç...</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>"], delay=0.8, fast_mode=fast)

    if player > botv:
        lvl = process_game_result(user.id, "poker", bet, "win", bet)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=bet, played=1)
        text = f"{short_result_prefix(update)}♠️ <b>POKER</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    elif player < botv:
        lvl = process_game_result(user.id, "poker", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}♠️ <b>POKER</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "poker", bet, "draw")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}♠️ <b>POKER</b>\n\n🤝 Berabere{lvl}"

    await safe_edit(msg, text, nav_main() if not fast else None, min_interval=1.0)


async def slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
    except Exception:
        bet = 50

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    await update.message.reply_text(f"🎰 <b>Makine çalıştırılıyor...</b>\n💰 Bahis: {format_number(bet)} 🪙", parse_mode="HTML")
    await asyncio.sleep(0.5)
    msg = await update.message.reply_dice(emoji="🎰")
    value = msg.dice.value
    await asyncio.sleep(2.5)

    if value > 50:
        profit = bet * 4
        lvl = process_game_result(user.id, "slot", bet, "win", profit)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=profit, played=1)
        text = f"{short_result_prefix(update)}🎰 <b>SLOT</b>\n\n💥 +{format_number(profit)} 🪙{lvl}"
    elif value > 25:
        profit = bet
        lvl = process_game_result(user.id, "slot", bet, "win", profit)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=profit, played=1)
        text = f"{short_result_prefix(update)}🎰 <b>SLOT</b>\n\n🙂 +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "slot", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🎰 <b>SLOT</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=None if fast else nav_main())


async def zar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
    except Exception:
        bet = 50

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    await update.message.reply_text(f"🎲 <b>Zar atılıyor...</b>\n💰 Bahis: {format_number(bet)} 🪙", parse_mode="HTML")
    msg = await update.message.reply_dice(emoji="🎲")
    player, botv = msg.dice.value, random.randint(1, 6)
    await asyncio.sleep(2.5)

    if player > botv:
        lvl = process_game_result(user.id, "zar", bet, "win", bet)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=bet, played=1)
        text = f"{short_result_prefix(update)}🎲 <b>ZAR</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    elif player < botv:
        lvl = process_game_result(user.id, "zar", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🎲 <b>ZAR</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "zar", bet, "draw")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🎲 <b>ZAR</b>\n\n🤝 Berabere{lvl}"

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=None if fast else nav_main())


async def basket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
    except Exception:
        bet = 50

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    await update.message.reply_text(f"🏀 <b>Top havalandı...</b>\n💰 Bahis: {format_number(bet)} 🪙", parse_mode="HTML")
    msg = await update.message.reply_dice(emoji="🏀")
    value = msg.dice.value
    await asyncio.sleep(2.5)

    if value >= 4:
        lvl = process_game_result(user.id, "basket", bet, "win", bet)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=bet, played=1)
        text = f"{short_result_prefix(update)}🏀 <b>BASKET</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "basket", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🏀 <b>BASKET</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=None if fast else nav_main())


async def coinflip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
        choice = context.args[1].lower()
    except Exception:
        await update.message.reply_text("Kullanım: /coinflip [miktar] [yazi/tura]")
        return

    if choice not in ["yazi", "tura"]:
        await update.message.reply_text("❌ yazi veya tura yaz.")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    result = random.choice(["yazi", "tura"])
    msg = await update.message.reply_text("🪙 <b>Para havaya atıldı...</b>", parse_mode="HTML")
    await animated_panel(msg, ["🪙 <b>Sonuç açılıyor...</b>"], delay=0.7, fast_mode=fast)

    if result == choice:
        lvl = process_game_result(user.id, "coinflip", bet, "win", bet)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=bet, played=1)
        text = f"{short_result_prefix(update)}🪙 <b>COINFLIP</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "coinflip", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🪙 <b>COINFLIP</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main() if not fast else None, min_interval=1.0)


async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
        guess_num = int(context.args[1])
    except Exception:
        await update.message.reply_text("Kullanım: /guess [miktar] [1-5]")
        return

    if guess_num < 1 or guess_num > 5:
        await update.message.reply_text("❌ 1 ile 5 arası sayı gir.")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    result = random.randint(1, 5)
    msg = await update.message.reply_text("🔢 <b>Sayı seçiliyor...</b>", parse_mode="HTML")
    await animated_panel(msg, ["🔢 <b>Sonuç açılıyor...</b>"], delay=0.7, fast_mode=fast)

    if result == guess_num:
        profit = bet * 4
        lvl = process_game_result(user.id, "guess", bet, "win", profit)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=profit, played=1)
        text = f"{short_result_prefix(update)}🔢 <b>GUESS</b>\n\n🔥 +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "guess", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🔢 <b>GUESS</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main() if not fast else None, min_interval=1.0)


async def highlow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
        choice = context.args[1].lower()
    except Exception:
        await update.message.reply_text("Kullanım: /highlow [miktar] [high/low]")
        return

    if choice not in ["high", "low"]:
        await update.message.reply_text("❌ high veya low yaz.")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    number = random.randint(1, 100)
    msg = await update.message.reply_text("📈 <b>Sayı hesaplanıyor...</b>", parse_mode="HTML")
    await animated_panel(msg, ["📊 <b>Sonuç açılıyor...</b>"], delay=0.7, fast_mode=fast)

    if number == 50:
        lvl = process_game_result(user.id, "highlow", bet, "draw")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}📈 <b>HIGHLOW</b>\n\n🤝 Berabere{lvl}"
    else:
        result = "high" if number > 50 else "low"
        if choice == result:
            lvl = process_game_result(user.id, "highlow", bet, "win", bet)
            if fast:
                update_group_leaderboard(update.effective_chat.id, user.id, won=bet, played=1)
            text = f"{short_result_prefix(update)}📈 <b>HIGHLOW</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
        else:
            lvl = process_game_result(user.id, "highlow", bet, "lose")
            if fast:
                update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
            text = f"{short_result_prefix(update)}📈 <b>HIGHLOW</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main() if not fast else None, min_interval=1.0)


async def crash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /crash [miktar]")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    multiplier = round(random.uniform(0.5, 5.0), 2)
    msg = await update.message.reply_text("🚀 <b>Roket kalkıyor...</b>\n\nx1.00", parse_mode="HTML")

    visible_steps = [1.30, 2.00] if fast else [1.20, 1.70, 2.30]
    for step in visible_steps:
        if step >= multiplier:
            break
        await safe_edit(msg, f"🚀 <b>Roket yükseliyor...</b>\n\nx{step:.2f}", min_interval=0.9 if fast else 1.0)
        await asyncio.sleep(0.7 if fast else 0.9)

    if multiplier >= 2.0:
        profit = int(bet * multiplier) - bet
        if profit < bet:
            profit = bet
        lvl = process_game_result(user.id, "crash", bet, "win", profit)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=profit, played=1)
        text = f"{short_result_prefix(update)}🚀 <b>CRASH</b>\n\n🎉 +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "crash", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}🚀 <b>CRASH</b>\n\n💥 Patladı!\n😢 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main() if not fast else None, min_interval=1.0)


async def mines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    fast = is_group_chat(update)
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /mines [miktar]")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    outcome = random.choice(["safe", "safe", "safe", "bomb"])
    msg = await update.message.reply_text("💣 <b>Maden sahasına giriliyor...</b>", parse_mode="HTML")

    await animated_panel(
        msg,
        [
            "⬜ ⬜ ⬜\n⬜ ⬜ ⬜\n⬜ ⬜ ⬜\n\n<b>Kare seçiliyor...</b>",
            "⬜ 💥 ⬜\n⬜ ⬜ ⬜\n⬜ ⬜ ⬜\n\n<b>Kontrol edildi...</b>" if outcome == "bomb"
            else "⬜ 💎 ⬜\n⬜ ⬜ ⬜\n⬜ ⬜ ⬜\n\n<b>Güvenli alan!</b>"
        ],
        delay=0.8,
        fast_mode=fast
    )

    if outcome == "safe":
        profit = bet * 2
        lvl = process_game_result(user.id, "mines", bet, "win", profit)
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=profit, played=1)
        text = f"{short_result_prefix(update)}💣 <b>MINES</b>\n\n💎 +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "mines", bet, "lose")
        if fast:
            update_group_leaderboard(update.effective_chat.id, user.id, won=0, played=1)
        text = f"{short_result_prefix(update)}💣 <b>MINES</b>\n\n💥 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main() if not fast else None, min_interval=1.0)


async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply ile kullan.\nÖrnek: /duel 500")
        return

    user1 = update.effective_user
    user2 = update.message.reply_to_message.from_user
    fast = is_group_chat(update)

    if user1.id == user2.id:
        await update.message.reply_text("❌ Kendinle düello yapamazsın.")
        return

    get_user(user1.id, get_display_name(user1))
    get_user(user2.id, get_display_name(user2))

    try:
        bet = int(context.args[0])
    except Exception:
        await update.message.reply_text("Kullanım: /duel [miktar]")
        return

    if not is_valid_amount(bet):
        await update.message.reply_text("❌ Geçerli miktar gir.")
        return

    if get_balance(user1.id) < bet or get_balance(user2.id) < bet:
        await update.message.reply_text("❌ Bir oyuncuda yeterli para yok.")
        return

    winner = random.choice([user1, user2])
    loser = user2 if winner.id == user1.id else user1

    msg = await update.message.reply_text("⚔️ <b>Düello başlıyor...</b>", parse_mode="HTML")
    await animated_panel(msg, [f"⚔️ <b>{get_display_name(user1)}</b> vs <b>{get_display_name(user2)}</b>\n\n⚡ <b>Son darbe...</b>"], delay=0.8, fast_mode=fast)

    update_balance(winner.id, bet)
    update_balance(loser.id, -bet)
    add_stats(winner.id, won=bet, played=1, games_won=1)
    add_stats(loser.id, lost=bet, played=1, games_won=0)
    add_xp(winner.id, XP_PER_GAME + XP_PER_WIN)
    add_xp(loser.id, XP_PER_GAME)
    update_missions_played(winner.id)
    update_missions_played(loser.id)
    update_missions_won(winner.id)
    log_game(winner.id, "duel", bet, "win", bet)
    log_game(loser.id, "duel", bet, "lose", -bet)
    check_achievements(winner.id)
    check_achievements(loser.id)

    if fast:
        update_group_leaderboard(update.effective_chat.id, winner.id, won=bet, played=1)
        update_group_leaderboard(update.effective_chat.id, loser.id, won=0, played=1)

    text = ("╔══════════════════════╗\n      ⚔️ <b>DÜELLO SONUCU</b>\n╚══════════════════════╝\n\n"
            f"🏆 Kazanan: <b>{get_display_name(winner)}</b>\n💰 Ödül: <b>+{format_number(bet)} 🪙</b>")
    await safe_edit(msg, text, nav_main() if not fast else None, min_interval=1.0)


# =========================
# CALLBACKS
# =========================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)
    data = query.data

    if data == "back_main":
        await safe_edit(query.message, home_panel(row, user.id), main_menu(), min_interval=0.8)
        return

    if data == "menu_balance":
        total = row[2] + row[3]
        vip_tag = "💎 VIP Aktif\n" if is_vip(user.id) else ""
        text = (f"╔══════════════════════╗\n      💰 <b>BAKİYE PANELİ</b>\n╚══════════════════════╝\n\n"
                f"{vip_tag}"
                f"👛 Cüzdan: <b>{format_number(row[2])} 🪙</b>\n"
                f"🏦 Banka: <b>{format_number(row[3])} 🪙</b>\n"
                f"📦 Toplam: <b>{format_number(total)} 🪙</b>\n\nEkonomini dikkatli yönet.")
        await safe_edit(query.message, text, main_menu(), min_interval=0.8)
        return

    if data == "menu_profile":
        total = row[2] + row[3]
        games_played, games_won = row[8], row[9]
        winrate = round((games_won / games_played) * 100, 1) if games_played else 0
        vip_tag = "💎 VIP Aktif\n" if is_vip(user.id) else ""
        rank = get_global_rank(user.id)
        text = (f"╔══════════════════════╗\n       📊 <b>PROFİL</b>\n╚══════════════════════╝\n\n"
                f"{vip_tag}"
                f"👤 İsim: <b>{row[1]}</b>\n"
                f"🆔 ID: <code>{row[0]}</code>\n"
                f"⭐ Level: <b>{row[5]}</b>\n"
                f"✨ XP: <b>{row[4]}</b>\n"
                f"🔥 Streak: <b>{row[13]}</b>\n"
                f"🏆 Global Sıra: <b>#{rank}</b>\n"
                f"💰 Servet: <b>{format_number(total)} 🪙</b>\n"
                f"🎮 Oyun: <b>{games_played}</b>\n"
                f"🏆 Galibiyet: <b>{games_won}</b>\n"
                f"📈 Winrate: <b>%{winrate}</b>\n\n"
                f"🎨 Kart için: /mycard")
        await safe_edit(query.message, text, main_menu(), min_interval=0.8)
        return

    if data == "menu_games":
        await safe_edit(
            query.message,
            "╔══════════════════════╗\n      🎮 <b>OYUN SALONU</b>\n╚══════════════════════╝\n\nŞansını denemek istediğin oyunu seç.",
            games_menu(),
            min_interval=0.8
        )
        return

    if data == "menu_top":
        await safe_edit(query.message, "🏆 <b>Global sıralama</b>\nKomut: /top\n\n👥 Grup sıralama:\nKomut: /grouptop", main_menu(), min_interval=0.8)
        return

    if data == "menu_mycard":
        card = generate_profile_card(user.id)
        if card:
            await query.message.reply_photo(photo=card, caption=f"📊 <b>{get_display_name(user)}</b> profil karti", parse_mode="HTML")
        else:
            await query.message.reply_text("❌ Profil karti olusturulamadi.")
        return

    if data == "menu_help":
        await safe_edit(query.message,
                        "╔══════════════════════╗\n        ℹ️ <b>YARDIM</b>\n╚══════════════════════╝\n\n"
                        "Komutlar:\n"
                        "• /top • /grouptop • /mycard\n"
                        "• /stats • /use vip_ticket\n"
                        "• Oyunlar: /rulet /blackjack /poker /slot /zar /basket /coinflip /guess /highlow /crash /mines /duel\n",
                        main_menu(),
                        min_interval=0.8)
        return

    game_infos = {
        "info_rulet": "🎡 /rulet [miktar] [kırmızı/siyah]",
        "info_blackjack": "🃏 /blackjack [miktar]",
        "info_poker": "♠️ /poker [miktar]",
        "info_slot": "🎰 /slot [miktar]",
        "info_zar": "🎲 /zar [miktar]",
        "info_basket": "🏀 /basket [miktar]",
        "info_coinflip": "🪙 /coinflip [miktar] [yazi/tura]",
        "info_guess": "🔢 /guess [miktar] [1-5]",
        "info_highlow": "📈 /highlow [miktar] [high/low]",
        "info_crash": "🚀 /crash [miktar]",
        "info_mines": "💣 /mines [miktar]",
        "info_duel": "⚔️ /duel [miktar] (reply)"
    }

    if data in game_infos:
        await safe_edit(query.message,
                        f"╔══════════════════════╗\n      🎮 <b>OYUN BİLGİSİ</b>\n╚══════════════════════╝\n\n{game_infos[data]}",
                        games_menu(),
                        min_interval=0.8)
        return

# =========================
# ERROR / MAIN
# =========================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, RetryAfter):
        logging.warning(f"429 rate limit: {err.retry_after} saniye beklenmeli.")
        return
    if isinstance(err, TimedOut):
        logging.warning("Telegram isteği timeout verdi.")
        return
    if isinstance(err, Conflict):
        logging.warning("409 conflict: Aynı token ile başka bir instance çalışıyor.")
        return
    logging.error("Hata oluştu:", exc_info=err)


def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    # core
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("vip", vip))
    app.add_handler(CommandHandler("gunluk", gunluk))
    app.add_handler(CommandHandler("haftalik", haftalik))
    app.add_handler(CommandHandler("faiz", faiz))
    app.add_handler(CommandHandler("bank", bank))
    app.add_handler(CommandHandler("deposit", deposit))
    app.add_handler(CommandHandler("withdraw", withdraw))
    app.add_handler(CommandHandler("gonder", gonder))
    app.add_handler(CommandHandler("use", use_item))
    app.add_handler(CommandHandler("stats", stats))

    # leaderboards + cards
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("grouptop", grouptop))
    app.add_handler(CommandHandler("mycard", mycard))

    # market/inv/ach/missions
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(CommandHandler("missions", missions))
    app.add_handler(CommandHandler("claim", claim))
    app.add_handler(CommandHandler("achievements", achievements))

    # games
    app.add_handler(CommandHandler("rulet", rulet))
    app.add_handler(CommandHandler("blackjack", blackjack))
    app.add_handler(CommandHandler("poker", poker))
    app.add_handler(CommandHandler("slot", slot))
    app.add_handler(CommandHandler("zar", zar))
    app.add_handler(CommandHandler("basket", basket))
    app.add_handler(CommandHandler("coinflip", coinflip))
    app.add_handler(CommandHandler("guess", guess))
    app.add_handler(CommandHandler("highlow", highlow))
    app.add_handler(CommandHandler("crash", crash))
    app.add_handler(CommandHandler("mines", mines))
    app.add_handler(CommandHandler("duel", duel))

    # admin
    app.add_handler(CommandHandler("addcoin", addcoin))
    app.add_handler(CommandHandler("setcoin", setcoin))
    app.add_handler(CommandHandler("setbank", setbank))
    app.add_handler(CommandHandler("resetuser", resetuser_cmd))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CommandHandler("myid", myid))

    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_error_handler(error_handler)

    print("🤖 Casino Bot çalışıyor...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
