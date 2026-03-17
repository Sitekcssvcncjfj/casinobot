import os
import random
import logging
import asyncio
from datetime import datetime, timedelta

import psycopg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
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
DATABASE_URL = os.getenv("DATABASE_URL")
ADMINS = [6101127840, 8189353497]

START_BALANCE = 1000
DAILY_REWARD = 500
WEEKLY_REWARD = 2000
VIP_THRESHOLD = 50000
XP_PER_GAME = 10
XP_PER_WIN = 25

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

if not TOKEN:
    raise ValueError("BOT_TOKEN bulunamadı.")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL bulunamadı.")

# =========================
# DATABASE
# =========================
conn = psycopg.connect(DATABASE_URL)
conn.autocommit = True
cursor = conn.cursor()

def now():
    return datetime.utcnow()

def now_iso():
    return now().isoformat()

def init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        sikke BIGINT DEFAULT 1000,
        bank BIGINT DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        total_won BIGINT DEFAULT 0,
        total_lost BIGINT DEFAULT 0,
        games_played INTEGER DEFAULT 0,
        games_won INTEGER DEFAULT 0,
        last_daily TEXT DEFAULT NULL,
        last_weekly TEXT DEFAULT NULL,
        created_at TEXT DEFAULT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS game_logs (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        game_name TEXT,
        bet BIGINT,
        result TEXT,
        amount_change BIGINT,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        item_name TEXT,
        quantity INTEGER DEFAULT 1
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS achievements (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        achievement_name TEXT,
        unlocked_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS missions (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        mission_name TEXT,
        progress INTEGER DEFAULT 0,
        target INTEGER DEFAULT 1,
        reward BIGINT DEFAULT 0,
        claimed INTEGER DEFAULT 0
    )
    """)

def format_number(n):
    return f"{n:,}".replace(",", ".")

def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except:
        return None

def format_timedelta(td):
    total = int(td.total_seconds())
    days = total // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60
    if days > 0:
        return f"{days}g {hours}s {minutes}dk"
    return f"{hours}s {minutes}dk"

def is_valid_amount(amount):
    return amount > 0

def get_display_name(user):
    return user.first_name or user.username or str(user.id)

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

def create_default_missions(user_id):
    cursor.execute("SELECT COUNT(*) FROM missions WHERE user_id=%s", (user_id,))
    count = cursor.fetchone()[0]
    if count > 0:
        return

    defaults = [
        ("İlk Oyunun", 0, 1, 250, 0),
        ("5 Oyun Oyna", 0, 5, 500, 0),
        ("3 Oyun Kazan", 0, 3, 750, 0),
    ]

    for m in defaults:
        cursor.execute("""
            INSERT INTO missions (user_id, mission_name, progress, target, reward, claimed)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, m[0], m[1], m[2], m[3], m[4]))

def get_user(user_id, username):
    cursor.execute("SELECT sikke FROM users WHERE user_id=%s", (user_id,))
    row = cursor.fetchone()

    if row is None:
        cursor.execute("""
            INSERT INTO users (
                user_id, username, sikke, bank, xp, level,
                total_won, total_lost, games_played, games_won,
                last_daily, last_weekly, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id, username, START_BALANCE, 0, 0, 1,
            0, 0, 0, 0, None, None, now_iso()
        ))
        create_default_missions(user_id)
        return START_BALANCE

    cursor.execute("UPDATE users SET username=%s WHERE user_id=%s", (username, user_id))
    return row[0]

def get_user_row(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
    return cursor.fetchone()

def get_balance(user_id):
    cursor.execute("SELECT sikke FROM users WHERE user_id=%s", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def get_bank(user_id):
    cursor.execute("SELECT bank FROM users WHERE user_id=%s", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else 0

def update_balance(user_id, amount):
    current = get_balance(user_id)
    new_value = current + amount
    if new_value < 0:
        return False
    cursor.execute("UPDATE users SET sikke=%s WHERE user_id=%s", (new_value, user_id))
    return True

def update_bank(user_id, amount):
    current = get_bank(user_id)
    new_value = current + amount
    if new_value < 0:
        return False
    cursor.execute("UPDATE users SET bank=%s WHERE user_id=%s", (new_value, user_id))
    return True

def add_stats(user_id, won=0, lost=0, played=0, games_won=0):
    cursor.execute("""
        UPDATE users
        SET total_won = total_won + %s,
            total_lost = total_lost + %s,
            games_played = games_played + %s,
            games_won = games_won + %s
        WHERE user_id=%s
    """, (won, lost, played, games_won, user_id))

def add_xp(user_id, amount):
    cursor.execute("SELECT xp, level FROM users WHERE user_id=%s", (user_id,))
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

    cursor.execute("UPDATE users SET xp=%s, level=%s WHERE user_id=%s", (xp, level, user_id))
    return leveled_up, level, xp

def log_game(user_id, game_name, bet, result, amount_change):
    cursor.execute("""
        INSERT INTO game_logs (user_id, game_name, bet, result, amount_change, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, game_name, bet, result, amount_change, now_iso()))

def set_daily(user_id):
    cursor.execute("UPDATE users SET last_daily=%s WHERE user_id=%s", (now_iso(), user_id))

def set_weekly(user_id):
    cursor.execute("UPDATE users SET last_weekly=%s WHERE user_id=%s", (now_iso(), user_id))

def top_users(limit=10):
    cursor.execute("""
        SELECT username, sikke, bank, level
        FROM users
        ORDER BY (sikke + bank) DESC
        LIMIT %s
    """, (limit,))
    return cursor.fetchall()

def global_stats():
    cursor.execute("SELECT COUNT(*) FROM users")
    users = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(sikke + bank), 0) FROM users")
    money = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM game_logs")
    logs = cursor.fetchone()[0]

    return users, money, logs

def add_item(user_id, item_name, qty=1):
    cursor.execute("""
        SELECT quantity FROM inventory WHERE user_id=%s AND item_name=%s
    """, (user_id, item_name))
    row = cursor.fetchone()

    if row:
        cursor.execute("""
            UPDATE inventory SET quantity = quantity + %s
            WHERE user_id=%s AND item_name=%s
        """, (qty, user_id, item_name))
    else:
        cursor.execute("""
            INSERT INTO inventory (user_id, item_name, quantity)
            VALUES (%s, %s, %s)
        """, (user_id, item_name, qty))

def get_inventory(user_id):
    cursor.execute("""
        SELECT item_name, quantity FROM inventory
        WHERE user_id=%s
        ORDER BY item_name ASC
    """, (user_id,))
    return cursor.fetchall()

def has_achievement(user_id, name):
    cursor.execute("""
        SELECT id FROM achievements WHERE user_id=%s AND achievement_name=%s
    """, (user_id, name))
    return cursor.fetchone() is not None

def unlock_achievement(user_id, name):
    if has_achievement(user_id, name):
        return False
    cursor.execute("""
        INSERT INTO achievements (user_id, achievement_name, unlocked_at)
        VALUES (%s, %s, %s)
    """, (user_id, name, now_iso()))
    return True

def get_achievements(user_id):
    cursor.execute("""
        SELECT achievement_name, unlocked_at
        FROM achievements
        WHERE user_id=%s
        ORDER BY id DESC
    """, (user_id,))
    return cursor.fetchall()

def update_missions_played(user_id):
    cursor.execute("""
        UPDATE missions
        SET progress = progress + 1
        WHERE user_id=%s AND mission_name IN ('İlk Oyunun', '5 Oyun Oyna')
    """, (user_id,))

def update_missions_won(user_id):
    cursor.execute("""
        UPDATE missions
        SET progress = progress + 1
        WHERE user_id=%s AND mission_name='3 Oyun Kazan'
    """, (user_id,))

def get_missions(user_id):
    cursor.execute("""
        SELECT id, mission_name, progress, target, reward, claimed
        FROM missions
        WHERE user_id=%s
        ORDER BY id ASC
    """, (user_id,))
    return cursor.fetchall()

def claim_mission(user_id, mission_id):
    cursor.execute("""
        SELECT progress, target, reward, claimed
        FROM missions
        WHERE id=%s AND user_id=%s
    """, (mission_id, user_id))
    row = cursor.fetchone()

    if not row:
        return False, "Görev bulunamadı.", 0

    progress, target, reward, claimed = row

    if claimed:
        return False, "Bu ödül zaten alınmış.", 0

    if progress < target:
        return False, "Görev henüz tamamlanmadı.", 0

    cursor.execute("UPDATE missions SET claimed=1 WHERE id=%s", (mission_id,))
    update_balance(user_id, reward)
    return True, "Görev ödülü alındı.", reward

# =========================
# KEYBOARDS
# =========================
def nav_main():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏠 Ana Menü", callback_data="back_main"),
            InlineKeyboardButton("🎮 Oyunlar", callback_data="menu_games"),
        ],
        [
            InlineKeyboardButton("📊 Profil", callback_data="menu_profile"),
            InlineKeyboardButton("💰 Bakiye", callback_data="menu_balance"),
        ]
    ])

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Bakiye", callback_data="menu_balance"),
            InlineKeyboardButton("🎮 Oyunlar", callback_data="menu_games"),
        ],
        [
            InlineKeyboardButton("📊 Profil", callback_data="menu_profile"),
            InlineKeyboardButton("🏦 Banka", callback_data="menu_bank"),
        ],
        [
            InlineKeyboardButton("🎁 Ödüller", callback_data="menu_rewards"),
            InlineKeyboardButton("🛒 Market", callback_data="menu_market"),
        ],
        [
            InlineKeyboardButton("🎒 Envanter", callback_data="menu_inventory"),
            InlineKeyboardButton("🏆 Sıralama", callback_data="menu_top"),
        ],
        [
            InlineKeyboardButton("📜 Görevler", callback_data="menu_missions"),
            InlineKeyboardButton("🏅 Başarımlar", callback_data="menu_achievements"),
        ],
        [
            InlineKeyboardButton("ℹ️ Yardım", callback_data="menu_help")
        ]
    ])

def games_menu():
    return InlineKeyboardMarkup([
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
        [
            InlineKeyboardButton("⬅️ Ana Menü", callback_data="back_main")
        ]
    ])

# =========================
# UI HELPERS
# =========================
async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise

async def animated_panel(message, frames, delay=0.6, reply_markup=None):
    for i, frame in enumerate(frames):
        await safe_edit(message, frame, reply_markup if i == len(frames)-1 else None)
        if i != len(frames) - 1:
            await asyncio.sleep(delay)

def start_text(name):
    return (
        "╔════════════════════╗\n"
        "     ✨ <b>CASINO V3 PREMIUM</b>\n"
        "╚════════════════════╝\n\n"
        f"👋 Hoş geldin, <b>{name}</b>\n\n"
        "🎁 Başlangıç bonusun hazır\n"
        "🎮 Oyun oyna, coin kazan\n"
        "⭐ XP topla, level atla\n"
        "🏆 Sıralamada yüksel\n"
        "🛒 Marketten özel eşyalar al\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Aşağıdaki premium panelden devam et."
    )

def home_panel(row):
    total = row[2] + row[3]
    vip = "💎 VIP\n" if total >= VIP_THRESHOLD else ""
    return (
        "╔════════════════════╗\n"
        "      🏛 <b>ANA PANEL</b>\n"
        "╚════════════════════╝\n\n"
        f"{vip}"
        f"👤 Oyuncu: <b>{row[1]}</b>\n"
        f"⭐ Level: <b>{row[5]}</b>\n"
        f"✨ XP: <b>{row[4]}</b>\n"
        f"💰 Toplam Servet: <b>{format_number(total)} 🪙</b>\n\n"
        "Panelden bir bölüm seç."
    )

# =========================
# GAME SYSTEM
# =========================
MARKET_ITEMS = {
    "vip_ticket": {"name": "VIP Bilet", "price": 5000},
    "lucky_box": {"name": "Şans Kutusu", "price": 2500},
    "gold_chip": {"name": "Altın Chip", "price": 1000},
}

def check_achievements(user_id):
    row = get_user_row(user_id)
    if not row:
        return

    total_money = row[2] + row[3]
    games_played = row[8]
    games_won = row[9]
    level = row[5]

    if games_played >= 1:
        unlock_achievement(user_id, "İlk Oyun")
    if games_won >= 10:
        unlock_achievement(user_id, "10 Oyun Kazandın")
    if total_money >= 10000:
        unlock_achievement(user_id, "10K Servet")
    if level >= 5:
        unlock_achievement(user_id, "Level 5")

def process_game_result(user_id, game_name, bet, outcome, profit=0):
    update_missions_played(user_id)
    xp_gain = XP_PER_GAME

    if outcome == "win":
        update_balance(user_id, profit)
        add_stats(user_id, won=profit, played=1, games_won=1)
        log_game(user_id, game_name, bet, "win", profit)
        update_missions_won(user_id)
        xp_gain += XP_PER_WIN
    elif outcome == "lose":
        update_balance(user_id, -bet)
        add_stats(user_id, lost=bet, played=1, games_won=0)
        log_game(user_id, game_name, bet, "lose", -bet)
    else:
        add_stats(user_id, played=1, games_won=0)
        log_game(user_id, game_name, bet, "draw", 0)

    result = add_xp(user_id, xp_gain)
    levelup_text = ""
    if result:
        leveled_up, level, xp = result
        if leveled_up:
            levelup_text = f"\n\n🎉 <b>Level atladın!</b> Yeni level: <b>{level}</b>"

    check_achievements(user_id)
    return levelup_text

# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
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
    await update.message.reply_text(home_panel(row), parse_mode="HTML", reply_markup=main_menu())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "╔════════════════════╗\n"
        "      ℹ️ <b>YARDIM PANELİ</b>\n"
        "╚════════════════════╝\n\n"
        "👤 <b>Genel</b>\n"
        "• /start • /menu • /help • /balance • /profile • /top • /stats\n\n"
        "🏦 <b>Banka</b>\n"
        "• /bank • /deposit [miktar] • /withdraw [miktar] • /gonder [miktar] (reply)\n\n"
        "🎁 <b>Ödüller</b>\n"
        "• /gunluk • /haftalik\n\n"
        "🛒 <b>Ekstra</b>\n"
        "• /market • /buy [item_kodu] • /inventory • /missions • /claim [görev_id] • /achievements\n\n"
        "🎮 <b>Oyunlar</b>\n"
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
        "• /duel [miktar] (reply)"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)
    cash = row[2]
    bankv = row[3]
    total = cash + bankv
    vip = "💎 <b>VIP Oyuncu</b>\n" if total >= VIP_THRESHOLD else ""
    await update.message.reply_text(
        f"{vip}💰 <b>Cüzdan:</b> {format_number(cash)} 🪙\n"
        f"🏦 <b>Banka:</b> {format_number(bankv)} 🪙\n"
        f"📦 <b>Toplam:</b> {format_number(total)} 🪙",
        parse_mode="HTML"
    )

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)
    total = row[2] + row[3]
    games_played = row[8]
    games_won = row[9]
    winrate = round((games_won / games_played) * 100, 1) if games_played else 0

    text = (
        "╔════════════════════╗\n"
        "      📊 <b>PROFİL</b>\n"
        "╚════════════════════╝\n\n"
        f"👤 İsim: <b>{row[1]}</b>\n"
        f"🆔 ID: <code>{row[0]}</code>\n"
        f"⭐ Level: <b>{row[5]}</b>\n"
        f"✨ XP: <b>{row[4]}</b>\n"
        f"💰 Servet: <b>{format_number(total)} 🪙</b>\n"
        f"🎮 Oyun: <b>{games_played}</b>\n"
        f"🏆 Galibiyet: <b>{games_won}</b>\n"
        f"📈 Winrate: <b>%{winrate}</b>\n"
        f"✅ Toplam Kazanç: <b>{format_number(row[6])} 🪙</b>\n"
        f"❌ Toplam Kayıp: <b>{format_number(row[7])} 🪙</b>"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def gunluk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)

    remain = daily_remaining(row[10])
    if remain:
        await update.message.reply_text(
            f"⏳ Günlük ödül hazır değil: <b>{format_timedelta(remain)}</b>",
            parse_mode="HTML"
        )
        return

    update_balance(user.id, DAILY_REWARD)
    set_daily(user.id)

    await update.message.reply_text(
        f"🎁 <b>Günlük ödül alındı!</b>\n+{format_number(DAILY_REWARD)} 🪙",
        parse_mode="HTML"
    )

async def haftalik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)

    remain = weekly_remaining(row[11])
    if remain:
        await update.message.reply_text(
            f"⏳ Haftalık ödül hazır değil: <b>{format_timedelta(remain)}</b>",
            parse_mode="HTML"
        )
        return

    update_balance(user.id, WEEKLY_REWARD)
    set_weekly(user.id)

    await update.message.reply_text(
        f"🎁 <b>Haftalık ödül alındı!</b>\n+{format_number(WEEKLY_REWARD)} 🪙",
        parse_mode="HTML"
    )

async def bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    await update.message.reply_text(
        f"🏦 <b>Banka:</b> {format_number(get_bank(user.id))} 🪙",
        parse_mode="HTML"
    )

async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        amount = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /deposit [miktar]")
        return

    if not is_valid_amount(amount):
        await update.message.reply_text("❌ Geçerli miktar gir.")
        return

    if get_balance(user.id) < amount:
        await update.message.reply_text("❌ Cüzdanda yeterli para yok.")
        return

    if update_balance(user.id, -amount) and update_bank(user.id, amount):
        await update.message.reply_text(
            f"🏦 Bankaya yatırıldı: <b>{format_number(amount)} 🪙</b>",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ İşlem başarısız.")

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        amount = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /withdraw [miktar]")
        return

    if not is_valid_amount(amount):
        await update.message.reply_text("❌ Geçerli miktar gir.")
        return

    if get_bank(user.id) < amount:
        await update.message.reply_text("❌ Bankada yeterli para yok.")
        return

    if update_bank(user.id, -amount) and update_balance(user.id, amount):
        await update.message.reply_text(
            f"🏦 Bankadan çekildi: <b>{format_number(amount)} 🪙</b>",
            parse_mode="HTML"
        )
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
    except:
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
        await update.message.reply_text(
            f"💸 <b>{get_display_name(receiver)}</b> kullanıcısına <b>{format_number(amount)} 🪙</b> gönderildi.",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ Transfer başarısız.")

# =========================
# MARKET / INVENTORY / MISSIONS / ACHIEVEMENTS
# =========================
async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "╔════════════════════╗\n      🛒 <b>MARKET</b>\n╚════════════════════╝\n\n"
    for code, item in MARKET_ITEMS.items():
        text += f"🎟 <b>{item['name']}</b>\n💰 Fiyat: <b>{format_number(item['price'])} 🪙</b>\n🧾 Kod: <code>{code}</code>\n\n"
    text += "Satın almak için:\n• /buy [item_kodu]"
    await update.message.reply_text(text, parse_mode="HTML")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        code = context.args[0]
    except:
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
            f"🛒 Satın alındı: <b>{item['name']}</b>\n"
            f"💰 -{format_number(price)} 🪙\n"
            f"🎁 Kutudan çıktı: <b>+{format_number(bonus)} 🪙</b>",
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(
        f"🛒 Satın alındı: <b>{item['name']}</b>\n💰 -{format_number(price)} 🪙",
        parse_mode="HTML"
    )

async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    items = get_inventory(user.id)
    if not items:
        await update.message.reply_text("🎒 Envanterin boş.")
        return

    text = "╔════════════════════╗\n      🎒 <b>ENVANTER</b>\n╚════════════════════╝\n\n"
    for name, qty in items:
        text += f"• <b>{name}</b> × {qty}\n"

    await update.message.reply_text(text, parse_mode="HTML")

async def missions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    data = get_missions(user.id)
    text = "╔════════════════════╗\n      📜 <b>GÖREVLER</b>\n╚════════════════════╝\n\n"

    for m in data:
        mission_id, name, progress, target, reward, claimed = m
        status = "✅ Alındı" if claimed else ("🎯 Hazır" if progress >= target else "⏳ Devam")
        text += (
            f"🆔 <code>{mission_id}</code>\n"
            f"<b>{name}</b>\n"
            f"İlerleme: {progress}/{target}\n"
            f"Ödül: {format_number(reward)} 🪙\n"
            f"Durum: {status}\n\n"
        )

    text += "Ödül almak için:\n• /claim [görev_id]"
    await update.message.reply_text(text, parse_mode="HTML")

async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        mission_id = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /claim [görev_id]")
        return

    ok, message, reward = claim_mission(user.id, mission_id)
    if not ok:
        await update.message.reply_text(f"❌ {message}")
        return

    await update.message.reply_text(
        f"🎁 <b>{message}</b>\n+{format_number(reward)} 🪙",
        parse_mode="HTML"
    )

async def achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    achs = get_achievements(user.id)
    if not achs:
        await update.message.reply_text("🏅 Henüz başarımın yok.")
        return

    text = "╔════════════════════╗\n      🏅 <b>BAŞARIMLAR</b>\n╚════════════════════╝\n\n"
    for name, unlocked_at in achs:
        text += f"🏅 {name}\n"

    await update.message.reply_text(text, parse_mode="HTML")

# =========================
# ANIMATED GAMES
# =========================
async def rulet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0]); color = context.args[1].lower()
    except:
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

    frames = [
        f"🎡 <b>Rulet dönüyor...</b>\n\n💰 Bahis: {format_number(bet)} 🪙\n🎯 Seçim: {color}",
        "🔴 ⚫ 🔴 ⚫ 🔴\n<b>Dönüyor...</b>",
        "⚫ 🔴 ⚫ 🔴 ⚫\n<b>Yavaşlıyor...</b>",
    ]
    await animated_panel(msg, frames, 0.7)

    if color == result:
        lvl = process_game_result(user.id, "rulet", bet, "win", bet)
        text = (
            "╔════════════════════╗\n"
            "      🎡 <b>RULET SONUCU</b>\n"
            "╚════════════════════╝\n\n"
            f"🎯 Seçimin: <b>{color}</b>\n"
            f"🎡 Sonuç: <b>{result}</b>\n\n"
            f"🎉 Kazandın: <b>+{format_number(bet)} 🪙</b>{lvl}"
        )
    else:
        lvl = process_game_result(user.id, "rulet", bet, "lose")
        text = (
            "╔════════════════════╗\n"
            "      🎡 <b>RULET SONUCU</b>\n"
            "╚════════════════════╝\n\n"
            f"🎯 Seçimin: <b>{color}</b>\n"
            f"🎡 Sonuç: <b>{result}</b>\n\n"
            f"😢 Kaybettin: <b>-{format_number(bet)} 🪙</b>{lvl}"
        )
    await safe_edit(msg, text, nav_main())

async def blackjack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /blackjack [miktar]")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    player = random.randint(15, 21)
    botv = random.randint(15, 21)

    msg = await update.message.reply_text("🃏 <b>Kartlar dağıtılıyor...</b>", parse_mode="HTML")
    await animated_panel(msg, [
        "🃏 <b>Kartlar dağıtılıyor...</b>\n\nSen: 🂠🂠\nBot: 🂠🂠",
        "🃏 <b>Masadaki gerilim artıyor...</b>\n\nSen: 🂡🂭\nBot: 🂠🂠",
        f"🃏 <b>Kartlar açılıyor...</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>",
    ], 0.8)

    if player > botv:
        lvl = process_game_result(user.id, "blackjack", bet, "win", bet)
        text = f"🃏 <b>BLACKJACK</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    elif player < botv:
        lvl = process_game_result(user.id, "blackjack", bet, "lose")
        text = f"🃏 <b>BLACKJACK</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "blackjack", bet, "draw")
        text = f"🃏 <b>BLACKJACK</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>\n\n🤝 Berabere{lvl}"

    await safe_edit(msg, text, nav_main())

async def poker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /poker [miktar]")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    player = random.randint(1, 100)
    botv = random.randint(1, 100)

    msg = await update.message.reply_text("♠️ <b>Poker eli hazırlanıyor...</b>", parse_mode="HTML")
    await animated_panel(msg, [
        "♠️ <b>Kartlar karılıyor...</b>",
        "♠️ <b>Dağıtılıyor...</b>\n\nSen: 🂠🂠🂠\nBot: 🂠🂠🂠",
        f"♠️ <b>Eller açılıyor...</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>"
    ], 0.7)

    if player > botv:
        lvl = process_game_result(user.id, "poker", bet, "win", bet)
        text = f"♠️ <b>POKER</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    elif player < botv:
        lvl = process_game_result(user.id, "poker", bet, "lose")
        text = f"♠️ <b>POKER</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "poker", bet, "draw")
        text = f"♠️ <b>POKER</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>\n\n🤝 Berabere{lvl}"

    await safe_edit(msg, text, nav_main())

async def slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
    except:
        bet = 50

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    await update.message.reply_text(f"🎰 <b>Makine çalıştırılıyor...</b>\n💰 Bahis: {format_number(bet)} 🪙", parse_mode="HTML")
    await asyncio.sleep(0.6)
    msg = await update.message.reply_dice(emoji="🎰")
    value = msg.dice.value
    await asyncio.sleep(2.5)

    if value > 50:
        profit = bet * 4
        lvl = process_game_result(user.id, "slot", bet, "win", profit)
        text = f"🎰 <b>SLOT SONUCU</b>\n\nDeğer: <b>{value}</b>\n💥 JACKPOT!\n🎉 +{format_number(profit)} 🪙{lvl}"
    elif value > 25:
        profit = bet
        lvl = process_game_result(user.id, "slot", bet, "win", profit)
        text = f"🎰 <b>SLOT SONUCU</b>\n\nDeğer: <b>{value}</b>\n🙂 Kazandın!\n🎉 +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "slot", bet, "lose")
        text = f"🎰 <b>SLOT SONUCU</b>\n\nDeğer: <b>{value}</b>\n😢 Kaybettin!\n-{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=nav_main())

async def zar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
    except:
        bet = 50

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    await update.message.reply_text(f"🎲 <b>Zar atılıyor...</b>\n💰 Bahis: {format_number(bet)} 🪙", parse_mode="HTML")
    msg = await update.message.reply_dice(emoji="🎲")
    player = msg.dice.value
    botv = random.randint(1, 6)
    await asyncio.sleep(2.5)

    if player > botv:
        lvl = process_game_result(user.id, "zar", bet, "win", bet)
        text = f"🎲 <b>ZAR SONUCU</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    elif player < botv:
        lvl = process_game_result(user.id, "zar", bet, "lose")
        text = f"🎲 <b>ZAR SONUCU</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "zar", bet, "draw")
        text = f"🎲 <b>ZAR SONUCU</b>\n\nSen: <b>{player}</b>\nBot: <b>{botv}</b>\n\n🤝 Berabere{lvl}"

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=nav_main())

async def basket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
    except:
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
        text = f"🏀 <b>BASKET SONUCU</b>\n\nAtış: <b>{value}</b>\n🎉 Basket oldu!\n+{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "basket", bet, "lose")
        text = f"🏀 <b>BASKET SONUCU</b>\n\nAtış: <b>{value}</b>\n😢 Kaçtı!\n-{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=nav_main())

async def coinflip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0]); choice = context.args[1].lower()
    except:
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
    await animated_panel(msg, [
        "🪙 <b>Takla 1...</b>",
        "🪙 <b>Takla 2...</b>",
        "🪙 <b>Takla 3...</b>",
    ], 0.5)

    if result == choice:
        lvl = process_game_result(user.id, "coinflip", bet, "win", bet)
        text = f"🪙 <b>COINFLIP SONUCU</b>\n\n🎯 Seçim: <b>{choice}</b>\n📌 Sonuç: <b>{result}</b>\n\n🎉 +{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "coinflip", bet, "lose")
        text = f"🪙 <b>COINFLIP SONUCU</b>\n\n🎯 Seçim: <b>{choice}</b>\n📌 Sonuç: <b>{result}</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main())

async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0]); guess_num = int(context.args[1])
    except:
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
    await animated_panel(msg, [
        "🔢 1... 2... 3...",
        "🔢 4... 5...",
        "🔢 <b>Son sayı belirleniyor...</b>"
    ], 0.6)

    if result == guess_num:
        profit = bet * 4
        lvl = process_game_result(user.id, "guess", bet, "win", profit)
        text = f"🔢 <b>TAHMİN SONUCU</b>\n\n🎯 Tahminin: <b>{guess_num}</b>\n📌 Sayı: <b>{result}</b>\n\n🔥 +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "guess", bet, "lose")
        text = f"🔢 <b>TAHMİN SONUCU</b>\n\n🎯 Tahminin: <b>{guess_num}</b>\n📌 Sayı: <b>{result}</b>\n\n😢 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main())

async def highlow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0]); choice = context.args[1].lower()
    except:
        await update.message.reply_text("Kullanım: /highlow [miktar] [high/low]")
        return

    if choice not in ["high", "low"]:
        await update.message.reply_text("❌ high veya low yaz.")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    number = random.randint(1, 100)
    msg = await update.message.reply_text("📈 <b>Sayı yükseliyor mu düşüyor mu...</b>", parse_mode="HTML")
    await animated_panel(msg, [
        "📈 <b>Rastgele sayı üretiliyor...</b>",
        "📉 <b>Grafik hareket ediyor...</b>",
        "📊 <b>Sonuç açılıyor...</b>",
    ], 0.6)

    if number == 50:
        lvl = process_game_result(user.id, "highlow", bet, "draw")
        text = f"📈 <b>HIGHLOW SONUCU</b>\n\nSayı: <b>{number}</b>\n🤝 Berabere{lvl}"
    else:
        result = "high" if number > 50 else "low"
        if choice == result:
            lvl = process_game_result(user.id, "highlow", bet, "win", bet)
            text = f"📈 <b>HIGHLOW SONUCU</b>\n\nSeçim: <b>{choice}</b>\nSayı: <b>{number}</b>\n🎉 +{format_number(bet)} 🪙{lvl}"
        else:
            lvl = process_game_result(user.id, "highlow", bet, "lose")
            text = f"📈 <b>HIGHLOW SONUCU</b>\n\nSeçim: <b>{choice}</b>\nSayı: <b>{number}</b>\n😢 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main())

async def crash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /crash [miktar]")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    multiplier = round(random.uniform(0.5, 5.0), 2)
    msg = await update.message.reply_text("🚀 <b>Roket kalkıyor...</b>\n\nx1.00", parse_mode="HTML")
    current = 1.00

    while current < min(multiplier, 3.5):
        current = round(current + random.uniform(0.2, 0.6), 2)
        await safe_edit(msg, f"🚀 <b>Roket yükseliyor...</b>\n\nx{current}")
        await asyncio.sleep(0.5)

    if multiplier >= 2.0:
        profit = int(bet * multiplier) - bet
        if profit < bet:
            profit = bet
        lvl = process_game_result(user.id, "crash", bet, "win", profit)
        text = (
            "╔════════════════════╗\n"
            "      🚀 <b>CRASH SONUCU</b>\n"
            "╚════════════════════╝\n\n"
            f"Çarpan: <b>x{multiplier}</b>\n"
            f"🎉 Kazanç: <b>+{format_number(profit)} 🪙</b>{lvl}"
        )
    else:
        lvl = process_game_result(user.id, "crash", bet, "lose")
        text = (
            "╔════════════════════╗\n"
            "      🚀 <b>CRASH SONUCU</b>\n"
            "╚════════════════════╝\n\n"
            f"Çarpan: <b>x{multiplier}</b>\n"
            f"💥 Patladı!\n😢 <b>-{format_number(bet)} 🪙</b>{lvl}"
        )

    await safe_edit(msg, text, nav_main())

async def mines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /mines [miktar]")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    outcome = random.choice(["safe", "safe", "safe", "bomb"])
    msg = await update.message.reply_text("💣 <b>Maden sahasına giriliyor...</b>", parse_mode="HTML")
    await animated_panel(msg, [
        "⬜ ⬜ ⬜\n⬜ ⬜ ⬜\n⬜ ⬜ ⬜\n\n<b>Kare seçiliyor...</b>",
        "⬜ 💥 ⬜\n⬜ ⬜ ⬜\n⬜ ⬜ ⬜\n\n<b>Bir alan kontrol edildi...</b>" if outcome == "bomb" else
        "⬜ 💎 ⬜\n⬜ ⬜ ⬜\n⬜ ⬜ ⬜\n\n<b>Güvenli alan!</b>"
    ], 0.8)

    if outcome == "safe":
        profit = bet * 2
        lvl = process_game_result(user.id, "mines", bet, "win", profit)
        text = f"💣 <b>MINES SONUCU</b>\n\n💎 Güvenli kare buldun!\n🎉 +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "mines", bet, "lose")
        text = f"💣 <b>MINES SONUCU</b>\n\n💥 Bombaya bastın!\n😢 -{format_number(bet)} 🪙{lvl}"

    await safe_edit(msg, text, nav_main())

async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply ile kullan.\nÖrnek: /duel 500")
        return

    user1 = update.effective_user
    user2 = update.message.reply_to_message.from_user

    if user1.id == user2.id:
        await update.message.reply_text("❌ Kendinle düello yapamazsın.")
        return

    get_user(user1.id, get_display_name(user1))
    get_user(user2.id, get_display_name(user2))

    try:
        bet = int(context.args[0])
    except:
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
    await animated_panel(msg, [
        f"⚔️ <b>{get_display_name(user1)}</b> vs <b>{get_display_name(user2)}</b>\n\nHazırlanıyor...",
        "🗡 Hamle 1...",
        "🛡 Savunma...",
        "⚡ Son darbe..."
    ], 0.6)

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

    text = (
        "╔════════════════════╗\n"
        "      ⚔️ <b>DÜELLO SONUCU</b>\n"
        "╚════════════════════╝\n\n"
        f"🏆 Kazanan: <b>{get_display_name(winner)}</b>\n"
        f"💰 Ödül: <b>+{format_number(bet)} 🪙</b>"
    )
    await safe_edit(msg, text, nav_main())

# =========================
# TOP / STATS / ADMIN
# =========================
async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = top_users(20)
    text = "╔════════════════════╗\n      🏆 <b>SIRALAMA</b>\n╚════════════════════╝\n\n"
    for i, row in enumerate(rows, start=1):
        total = row[1] + row[2]
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
        text += f"{medal} <b>{i}.</b> {row[0]} — {format_number(total)} 🪙 | Lv.{row[3]}\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users, money, logs = global_stats()
    await update.message.reply_text(
        f"📊 <b>Global İstatistik</b>\n\n"
        f"👥 Kullanıcı: {users}\n"
        f"💰 Toplam Varlık: {format_number(money)} 🪙\n"
        f"🎮 Toplam Oyun Logu: {format_number(logs)}",
        parse_mode="HTML"
    )

async def addcoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = update.effective_user
    if admin.id not in ADMINS:
        await update.message.reply_text("❌ Yetkin yok.")
        return

    try:
        uid = int(context.args[0]); amount = int(context.args[1])
    except:
        await update.message.reply_text("Kullanım: /addcoin [user_id] [miktar]")
        return

    get_user(uid, str(uid))

    if amount < 0 and get_balance(uid) < abs(amount):
        await update.message.reply_text("❌ Kullanıcının bakiyesi yeterli değil.")
        return

    update_balance(uid, amount)
    await update.message.reply_text("✅ İşlem tamamlandı.")

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
        await safe_edit(query.message, home_panel(row), main_menu())
        return

    if data == "menu_balance":
        total = row[2] + row[3]
        text = (
            "╔════════════════════╗\n"
            "      💰 <b>BAKİYE PANELİ</b>\n"
            "╚════════════════════╝\n\n"
            f"👛 Cüzdan: <b>{format_number(row[2])} 🪙</b>\n"
            f"🏦 Banka: <b>{format_number(row[3])} 🪙</b>\n"
            f"📦 Toplam: <b>{format_number(total)} 🪙</b>\n\n"
            "Ekonomini dikkatli yönet."
        )
        await safe_edit(query.message, text, main_menu())
        return

    if data == "menu_profile":
        total = row[2] + row[3]
        games_played = row[8]
        games_won = row[9]
        winrate = round((games_won / games_played) * 100, 1) if games_played else 0
        text = (
            "╔════════════════════╗\n"
            "      📊 <b>PROFİL</b>\n"
            "╚════════════════════╝\n\n"
            f"👤 İsim: <b>{row[1]}</b>\n"
            f"🆔 ID: <code>{row[0]}</code>\n"
            f"⭐ Level: <b>{row[5]}</b>\n"
            f"✨ XP: <b>{row[4]}</b>\n"
            f"💰 Servet: <b>{format_number(total)} 🪙</b>\n"
            f"🎮 Oyun: <b>{games_played}</b>\n"
            f"🏆 Galibiyet: <b>{games_won}</b>\n"
            f"📈 Winrate: <b>%{winrate}</b>"
        )
        await safe_edit(query.message, text, main_menu())
        return

    if data == "menu_games":
        text = (
            "╔════════════════════╗\n"
            "      🎮 <b>OYUN SALONU</b>\n"
            "╚════════════════════╝\n\n"
            "Şansını denemek istediğin oyunu seç.\n"
            "Her oyunun kullanım bilgisi aşağıda."
        )
        await safe_edit(query.message, text, games_menu())
        return

    if data == "menu_bank":
        text = (
            "╔════════════════════╗\n"
            "      🏦 <b>BANKA</b>\n"
            "╚════════════════════╝\n\n"
            f"Mevcut banka bakiyen: <b>{format_number(row[3])} 🪙</b>\n\n"
            "İşlemler:\n• /deposit [miktar]\n• /withdraw [miktar]"
        )
        await safe_edit(query.message, text, main_menu())
        return

    if data == "menu_rewards":
        d = daily_remaining(row[10])
        w = weekly_remaining(row[11])
        text = (
            "╔════════════════════╗\n"
            "      🎁 <b>ÖDÜLLER</b>\n"
            "╚════════════════════╝\n\n"
            f"📅 Günlük: <b>{'Hazır ✅' if not d else format_timedelta(d)}</b>\n"
            f"🗓 Haftalık: <b>{'Hazır ✅' if not w else format_timedelta(w)}</b>\n\n"
            "Komutlar:\n• /gunluk\n• /haftalik"
        )
        await safe_edit(query.message, text, main_menu())
        return

    if data == "menu_market":
        text = "╔════════════════════╗\n      🛒 <b>MARKET</b>\n╚════════════════════╝\n\n"
        for code, item in MARKET_ITEMS.items():
            text += f"🎟 <b>{item['name']}</b>\n💰 Fiyat: <b>{format_number(item['price'])} 🪙</b>\n🧾 Kod: <code>{code}</code>\n\n"
        text += "Satın almak için:\n• /buy [item_kodu]"
        await safe_edit(query.message, text, main_menu())
        return

    if data == "menu_inventory":
        items = get_inventory(user.id)
        if not items:
            text = "╔════════════════════╗\n      🎒 <b>ENVANTER</b>\n╚════════════════════╝\n\nEnvanterin şu anda boş."
        else:
            text = "╔════════════════════╗\n      🎒 <b>ENVANTER</b>\n╚════════════════════╝\n\n"
            for name, qty in items:
                text += f"• <b>{name}</b> × {qty}\n"
        await safe_edit(query.message, text, main_menu())
        return

    if data == "menu_top":
        rows = top_users(10)
        text = "╔════════════════════╗\n      🏆 <b>SIRALAMA</b>\n╚════════════════════╝\n\n"
        for i, r in enumerate(rows, start=1):
            total = r[1] + r[2]
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
            text += f"{medal} <b>{i}.</b> {r[0]} — {format_number(total)} 🪙 | Lv.{r[3]}\n"
        await safe_edit(query.message, text, main_menu())
        return

    if data == "menu_missions":
        data_rows = get_missions(user.id)
        text = "╔════════════════════╗\n      📜 <b>GÖREVLER</b>\n╚════════════════════╝\n\n"
        for m in data_rows:
            status = "✅ Alındı" if m[5] else ("🎯 Hazır" if m[2] >= m[3] else "⏳ Devam")
            text += (
                f"🆔 <code>{m[0]}</code>\n"
                f"<b>{m[1]}</b>\n"
                f"İlerleme: {m[2]}/{m[3]}\n"
                f"Ödül: {format_number(m[4])} 🪙\n"
                f"Durum: {status}\n\n"
            )
        text += "Ödül almak için:\n• /claim [görev_id]"
        await safe_edit(query.message, text, main_menu())
        return

    if data == "menu_achievements":
        achs = get_achievements(user.id)
        text = "╔════════════════════╗\n      🏅 <b>BAŞARIMLAR</b>\n╚════════════════════╝\n\n"
        if not achs:
            text += "Henüz başarımın yok."
        else:
            for a in achs:
                text += f"🏅 {a[0]}\n"
        await safe_edit(query.message, text, main_menu())
        return

    if data == "menu_help":
        text = (
            "╔════════════════════╗\n"
            "      ℹ️ <b>YARDIM</b>\n"
            "╚════════════════════╝\n\n"
            "Komut listesi için /help yaz.\n"
            "Oyunlarda animasyonlu akış aktiftir.\n\n"
            "🎮 Oyun oyna\n💰 Coin kazan\n⭐ XP kas\n🏆 Sıralamada yüksel"
        )
        await safe_edit(query.message, text, main_menu())
        return

    game_infos = {
        "info_rulet": "🎡 /rulet [miktar] [kırmızı/siyah]\nAnimasyon: rulet dönüşü",
        "info_blackjack": "🃏 /blackjack [miktar]\nAnimasyon: kart açılışı",
        "info_poker": "♠️ /poker [miktar]\nAnimasyon: el açılışı",
        "info_slot": "🎰 /slot [miktar]\nAnimasyon: Telegram slot zarı",
        "info_zar": "🎲 /zar [miktar]\nAnimasyon: Telegram zar",
        "info_basket": "🏀 /basket [miktar]\nAnimasyon: Telegram basket atışı",
        "info_coinflip": "🪙 /coinflip [miktar] [yazi/tura]\nAnimasyon: para taklası",
        "info_guess": "🔢 /guess [miktar] [1-5]\nAnimasyon: sayı açılışı",
        "info_highlow": "📈 /highlow [miktar] [high/low]\nAnimasyon: grafik açılışı",
        "info_crash": "🚀 /crash [miktar]\nAnimasyon: roket çarpanı",
        "info_mines": "💣 /mines [miktar]\nAnimasyon: maden sahası",
        "info_duel": "⚔️ /duel [miktar] (reply)\nAnimasyon: düello akışı",
    }

    if data in game_infos:
        text = (
            "╔════════════════════╗\n"
            "      🎮 <b>OYUN BİLGİSİ</b>\n"
            "╚════════════════════╝\n\n"
            f"{game_infos[data]}"
        )
        await safe_edit(query.message, text, games_menu())
        return

# =========================
# ERROR
# =========================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("Hata oluştu:", exc_info=context.error)

# =========================
# MAIN
# =========================
def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    # general
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("stats", stats))

    # rewards
    app.add_handler(CommandHandler("gunluk", gunluk))
    app.add_handler(CommandHandler("haftalik", haftalik))

    # bank
    app.add_handler(CommandHandler("bank", bank))
    app.add_handler(CommandHandler("deposit", deposit))
    app.add_handler(CommandHandler("withdraw", withdraw))
    app.add_handler(CommandHandler("gonder", gonder))

    # market / inventory
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("inventory", inventory))

    # missions / achievements
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

    # callbacks
    app.add_handler(CallbackQueryHandler(callbacks))

    app.add_error_handler(error_handler)

    print("🤖 Casino Bot Premium V3 PostgreSQL ile çalışıyor...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
