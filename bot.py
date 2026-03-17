import random
import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

from config import TOKEN, ADMINS, DAILY_REWARD, WEEKLY_REWARD, VIP_THRESHOLD, XP_PER_GAME, XP_PER_WIN
from database import (
    init_db, get_user, get_user_row, get_balance, get_bank,
    update_balance, update_bank, add_stats, add_xp, log_game,
    set_daily, set_weekly, top_users, global_stats,
    add_item, get_inventory,
    unlock_achievement, get_achievements,
    update_missions_played, update_missions_won,
    get_missions, claim_mission
)
from utils import (
    format_number, get_display_name, is_valid_amount,
    daily_remaining, weekly_remaining, format_timedelta
)
from keyboards import main_menu, games_menu

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

MARKET_ITEMS = {
    "vip_ticket": {"name": "VIP Bilet", "price": 5000},
    "lucky_box": {"name": "Şans Kutusu", "price": 2500},
    "gold_chip": {"name": "Altın Chip", "price": 1000},
}

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

# =========================
# GENERAL
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    text = (
        f"🎰 <b>Casino Bot V2'ye hoş geldin {get_display_name(user)}!</b>\n\n"
        f"💰 Başlangıç bakiyesi: <b>1.000 🪙</b>\n"
        f"📌 Menü için /menu\n"
        f"📖 Yardım için /help"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    await update.message.reply_text("📋 <b>Ana Menü</b>", parse_mode="HTML", reply_markup=main_menu())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎰 <b>Komutlar</b>\n\n"
        "/start\n/menu\n/help\n/balance\n/profile\n/top\n/stats\n"
        "/bank\n/deposit <miktar>\n/withdraw <miktar>\n"
        "/gonder <miktar> (reply)\n"
        "/gunluk\n/haftalik\n"
        "/market\n/buy <item_kodu>\n/inventory\n"
        "/missions\n/claim <görev_id>\n/achievements\n\n"
        "🎮 <b>Oyunlar</b>\n"
        "/rulet <miktar> <kırmızı/siyah>\n"
        "/blackjack <miktar>\n"
        "/poker <miktar>\n"
        "/slot <miktar>\n"
        "/zar <miktar>\n"
        "/basket <miktar>\n"
        "/coinflip <miktar> <yazi/tura>\n"
        "/guess <miktar> <1-5>\n"
        "/highlow <miktar> <high/low>\n"
        "/crash <miktar>\n"
        "/mines <miktar>\n"
        "/duel <miktar> (reply)\n"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)

    cash = row[2]
    bank = row[3]
    total = cash + bank
    vip = "💎 <b>VIP Oyuncu</b>\n" if total >= VIP_THRESHOLD else ""

    await update.message.reply_text(
        f"{vip}"
        f"💰 <b>Cüzdan:</b> {format_number(cash)} 🪙\n"
        f"🏦 <b>Banka:</b> {format_number(bank)} 🪙\n"
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
        f"📊 <b>Profil</b>\n\n"
        f"👤 <b>İsim:</b> {row[1]}\n"
        f"🆔 <b>ID:</b> <code>{row[0]}</code>\n"
        f"⭐ <b>Level:</b> {row[5]}\n"
        f"✨ <b>XP:</b> {row[4]}\n"
        f"💰 <b>Cüzdan:</b> {format_number(row[2])} 🪙\n"
        f"🏦 <b>Banka:</b> {format_number(row[3])} 🪙\n"
        f"📦 <b>Toplam Varlık:</b> {format_number(total)} 🪙\n\n"
        f"🎮 <b>Oynanan:</b> {games_played}\n"
        f"🏆 <b>Kazanılan:</b> {games_won}\n"
        f"📈 <b>Winrate:</b> %{winrate}\n"
        f"✅ <b>Toplam Kazanç:</b> {format_number(row[6])} 🪙\n"
        f"❌ <b>Toplam Kayıp:</b> {format_number(row[7])} 🪙"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def gunluk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    row = get_user_row(user.id)

    remain = daily_remaining(row[10])
    if remain:
        await update.message.reply_text(f"⏳ Günlük ödül hazır değil: <b>{format_timedelta(remain)}</b>", parse_mode="HTML")
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
        await update.message.reply_text(f"⏳ Haftalık ödül hazır değil: <b>{format_timedelta(remain)}</b>", parse_mode="HTML")
        return

    update_balance(user.id, WEEKLY_REWARD)
    set_weekly(user.id)

    await update.message.reply_text(
        f"🎁 <b>Haftalık ödül alındı!</b>\n+{format_number(WEEKLY_REWARD)} 🪙",
        parse_mode="HTML"
    )

# =========================
# BANK
# =========================
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
        await update.message.reply_text("Kullanım: /deposit <miktar>")
        return

    if not is_valid_amount(amount):
        await update.message.reply_text("❌ Geçerli miktar gir.")
        return

    if get_balance(user.id) < amount:
        await update.message.reply_text("❌ Cüzdanda yeterli para yok.")
        return

    if update_balance(user.id, -amount) and update_bank(user.id, amount):
        await update.message.reply_text(f"🏦 Bankaya yatırıldı: {format_number(amount)} 🪙")
    else:
        await update.message.reply_text("❌ İşlem başarısız.")

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        amount = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /withdraw <miktar>")
        return

    if not is_valid_amount(amount):
        await update.message.reply_text("❌ Geçerli miktar gir.")
        return

    if get_bank(user.id) < amount:
        await update.message.reply_text("❌ Bankada yeterli para yok.")
        return

    if update_bank(user.id, -amount) and update_balance(user.id, amount):
        await update.message.reply_text(f"🏦 Bankadan çekildi: {format_number(amount)} 🪙")
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
        await update.message.reply_text("Kullanım: /gonder <miktar>")
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
            f"💸 <b>{get_display_name(receiver)}</b> kullanıcısına {format_number(amount)} 🪙 gönderildi.",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ Transfer başarısız.")

# =========================
# MARKET / INVENTORY / MISSIONS / ACHIEVEMENTS
# =========================
async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🛒 <b>Market</b>\n\n"
    for code, item in MARKET_ITEMS.items():
        text += f"• <b>{item['name']}</b> — {format_number(item['price'])} 🪙\n"
        text += f"  Kod: <code>{code}</code>\n\n"
    text += "Satın almak için: /buy <item_kodu>"
    await update.message.reply_text(text, parse_mode="HTML")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        code = context.args[0]
    except:
        await update.message.reply_text("Kullanım: /buy <item_kodu>")
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

    text = "🎒 <b>Envanterin</b>\n\n"
    for name, qty in items:
        text += f"• {name} x{qty}\n"

    await update.message.reply_text(text, parse_mode="HTML")

async def missions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    data = get_missions(user.id)
    text = "📜 <b>Görevler</b>\n\n"

    for m in data:
        mission_id, name, progress, target, reward, claimed = m
        status = "✅ Alındı" if claimed else ("🎯 Hazır" if progress >= target else "⏳ Devam ediyor")
        text += (
            f"ID: <code>{mission_id}</code>\n"
            f"{name}\n"
            f"İlerleme: {progress}/{target}\n"
            f"Ödül: {format_number(reward)} 🪙\n"
            f"Durum: {status}\n\n"
        )

    text += "Ödül almak için: /claim <görev_id>"
    await update.message.reply_text(text, parse_mode="HTML")

async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        mission_id = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /claim <görev_id>")
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

    text = "🏅 <b>Başarımlar</b>\n\n"
    for name, unlocked_at in achs:
        text += f"• {name}\n"

    await update.message.reply_text(text, parse_mode="HTML")

# =========================
# GAMES
# =========================
async def rulet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))

    try:
        bet = int(context.args[0])
        color = context.args[1].lower()
    except:
        await update.message.reply_text("Kullanım: /rulet <miktar> <kırmızı/siyah>")
        return

    if color not in ["kırmızı", "siyah"]:
        await update.message.reply_text("❌ kırmızı veya siyah yaz.")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    result = random.choice(["kırmızı", "siyah"])
    if color == result:
        lvl = process_game_result(user.id, "rulet", bet, "win", bet)
        await update.message.reply_text(
            f"🎡 Sonuç: <b>{result}</b>\n🎉 Kazandın: +{format_number(bet)} 🪙{lvl}",
            parse_mode="HTML"
        )
    else:
        lvl = process_game_result(user.id, "rulet", bet, "lose")
        await update.message.reply_text(
            f"🎡 Sonuç: <b>{result}</b>\n😢 Kaybettin: -{format_number(bet)} 🪙{lvl}",
            parse_mode="HTML"
        )

async def blackjack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /blackjack <miktar>")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    player = random.randint(15, 21)
    bot = random.randint(15, 21)

    if player > bot:
        lvl = process_game_result(user.id, "blackjack", bet, "win", bet)
        text = f"🃏 Sen: {player}\n🤖 Bot: {bot}\n🎉 +{format_number(bet)} 🪙{lvl}"
    elif player < bot:
        lvl = process_game_result(user.id, "blackjack", bet, "lose")
        text = f"🃏 Sen: {player}\n🤖 Bot: {bot}\n😢 -{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "blackjack", bet, "draw")
        text = f"🃏 Sen: {player}\n🤖 Bot: {bot}\n🤝 Berabere{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

async def poker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /poker <miktar>")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    player = random.randint(1, 100)
    bot = random.randint(1, 100)

    if player > bot:
        lvl = process_game_result(user.id, "poker", bet, "win", bet)
        text = f"♠️ Sen: {player}\n🤖 Bot: {bot}\n🎉 +{format_number(bet)} 🪙{lvl}"
    elif player < bot:
        lvl = process_game_result(user.id, "poker", bet, "lose")
        text = f"♠️ Sen: {player}\n🤖 Bot: {bot}\n😢 -{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "poker", bet, "draw")
        text = f"♠️ Sen: {player}\n🤖 Bot: {bot}\n🤝 Berabere{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

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

    msg = await update.message.reply_dice(emoji="🎰")
    value = msg.dice.value

    if value > 50:
        profit = bet * 4
        lvl = process_game_result(user.id, "slot", bet, "win", profit)
        text = f"🎰 Değer: {value}\n💥 JACKPOT +{format_number(profit)} 🪙{lvl}"
    elif value > 25:
        profit = bet
        lvl = process_game_result(user.id, "slot", bet, "win", profit)
        text = f"🎰 Değer: {value}\n🙂 +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "slot", bet, "lose")
        text = f"🎰 Değer: {value}\n😢 -{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

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

    msg = await update.message.reply_dice(emoji="🎲")
    player = msg.dice.value
    bot = random.randint(1, 6)

    if player > bot:
        lvl = process_game_result(user.id, "zar", bet, "win", bet)
        text = f"🎲 Sen:{player}\n🤖 Bot:{bot}\n🎉 +{format_number(bet)} 🪙{lvl}"
    elif player < bot:
        lvl = process_game_result(user.id, "zar", bet, "lose")
        text = f"🎲 Sen:{player}\n🤖 Bot:{bot}\n😢 -{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "zar", bet, "draw")
        text = f"🎲 Sen:{player}\n🤖 Bot:{bot}\n🤝 Berabere{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

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

    msg = await update.message.reply_dice(emoji="🏀")
    value = msg.dice.value

    if value >= 4:
        lvl = process_game_result(user.id, "basket", bet, "win", bet)
        text = f"🏀 Atış:{value}\n🎉 +{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "basket", bet, "lose")
        text = f"🏀 Atış:{value}\n😢 -{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

async def coinflip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
        choice = context.args[1].lower()
    except:
        await update.message.reply_text("Kullanım: /coinflip <miktar> <yazi/tura>")
        return

    if choice not in ["yazi", "tura"]:
        await update.message.reply_text("❌ yazi veya tura yaz.")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    result = random.choice(["yazi", "tura"])
    if result == choice:
        lvl = process_game_result(user.id, "coinflip", bet, "win", bet)
        text = f"🪙 Sonuç: {result}\n🎉 +{format_number(bet)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "coinflip", bet, "lose")
        text = f"🪙 Sonuç: {result}\n😢 -{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
        guess_num = int(context.args[1])
    except:
        await update.message.reply_text("Kullanım: /guess <miktar> <1-5>")
        return

    if guess_num < 1 or guess_num > 5:
        await update.message.reply_text("❌ 1 ile 5 arası sayı gir.")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    result = random.randint(1, 5)
    if result == guess_num:
        profit = bet * 4
        lvl = process_game_result(user.id, "guess", bet, "win", profit)
        text = f"🔢 Sayı: {result}\n🔥 Doğru bildin! +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "guess", bet, "lose")
        text = f"🔢 Sayı: {result}\n😢 -{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

async def highlow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
        choice = context.args[1].lower()
    except:
        await update.message.reply_text("Kullanım: /highlow <miktar> <high/low>")
        return

    if choice not in ["high", "low"]:
        await update.message.reply_text("❌ high veya low yaz.")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    number = random.randint(1, 100)
    if number == 50:
        lvl = process_game_result(user.id, "highlow", bet, "draw")
        text = f"📈 Sayı: {number}\n🤝 Berabere{lvl}"
    else:
        result = "high" if number > 50 else "low"
        if choice == result:
            lvl = process_game_result(user.id, "highlow", bet, "win", bet)
            text = f"📈 Sayı: {number}\n🎉 +{format_number(bet)} 🪙{lvl}"
        else:
            lvl = process_game_result(user.id, "highlow", bet, "lose")
            text = f"📈 Sayı: {number}\n😢 -{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

async def crash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /crash <miktar>")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    multiplier = round(random.uniform(0.5, 5.0), 2)

    if multiplier >= 2.0:
        profit = int(bet * multiplier) - bet
        if profit < bet:
            profit = bet
        lvl = process_game_result(user.id, "crash", bet, "win", profit)
        text = f"🚀 Crash çarpanı: x{multiplier}\n🎉 Kazandın: +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "crash", bet, "lose")
        text = f"🚀 Crash çarpanı: x{multiplier}\n💥 Patladı! -{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

async def mines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, get_display_name(user))
    try:
        bet = int(context.args[0])
    except:
        await update.message.reply_text("Kullanım: /mines <miktar>")
        return

    if not is_valid_amount(bet) or get_balance(user.id) < bet:
        await update.message.reply_text("❌ Geçersiz bahis.")
        return

    outcome = random.choice(["safe", "safe", "safe", "bomb"])
    if outcome == "safe":
        profit = bet * 2
        lvl = process_game_result(user.id, "mines", bet, "win", profit)
        text = f"💣 Güvenli kare buldun!\n🎉 +{format_number(profit)} 🪙{lvl}"
    else:
        lvl = process_game_result(user.id, "mines", bet, "lose")
        text = f"💣 Bombaya bastın!\n😢 -{format_number(bet)} 🪙{lvl}"

    await update.message.reply_text(text, parse_mode="HTML")

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
        await update.message.reply_text("Kullanım: /duel <miktar>")
        return

    if not is_valid_amount(bet):
        await update.message.reply_text("❌ Geçerli miktar gir.")
        return

    if get_balance(user1.id) < bet or get_balance(user2.id) < bet:
        await update.message.reply_text("❌ Bir oyuncuda yeterli para yok.")
        return

    winner = random.choice([user1, user2])
    loser = user2 if winner.id == user1.id else user1

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

    await update.message.reply_text(
        f"⚔️ <b>Düello Sonucu</b>\n\n"
        f"🏆 Kazanan: <b>{get_display_name(winner)}</b>\n"
        f"💰 Ödül: +{format_number(bet)} 🪙",
        parse_mode="HTML"
    )

# =========================
# TOP / STATS / ADMIN
# =========================
async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = top_users(20)
    text = "🏆 <b>En Zengin Oyuncular</b>\n\n"
    for i, row in enumerate(rows, start=1):
        total = row[1] + row[2]
        text += f"{i}. <b>{row[0]}</b> — {format_number(total)} 🪙 | Lv.{row[3]}\n"
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
        uid = int(context.args[0])
        amount = int(context.args[1])
    except:
        await update.message.reply_text("Kullanım: /addcoin <user_id> <miktar>")
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
        await query.edit_message_text("📋 <b>Ana Menü</b>", parse_mode="HTML", reply_markup=main_menu())
        return

    if data == "menu_balance":
        total = row[2] + row[3]
        await query.edit_message_text(
            f"💰 <b>Bakiye</b>\n\nCüzdan: {format_number(row[2])} 🪙\nBanka: {format_number(row[3])} 🪙\nToplam: {format_number(total)} 🪙",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        return

    if data == "menu_profile":
        total = row[2] + row[3]
        await query.edit_message_text(
            f"📊 <b>Profil</b>\n\n"
            f"👤 {row[1]}\n"
            f"⭐ Level: {row[5]}\n"
            f"✨ XP: {row[4]}\n"
            f"💰 Cüzdan: {format_number(row[2])} 🪙\n"
            f"🏦 Banka: {format_number(row[3])} 🪙\n"
            f"📦 Toplam: {format_number(total)} 🪙",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        return

    if data == "menu_games":
        await query.edit_message_text(
            "🎮 <b>Oyunlar Menüsü</b>\n\nBir oyunun kullanım şeklini seç.",
            parse_mode="HTML",
            reply_markup=games_menu()
        )
        return

    if data == "menu_bank":
        await query.edit_message_text(
            f"🏦 <b>Banka</b>\n\nBakiyen: {format_number(row[3])} 🪙\n\nKomutlar:\n/deposit <miktar>\n/withdraw <miktar>",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        return

    if data == "menu_rewards":
        d = daily_remaining(row[10])
        w = weekly_remaining(row[11])
        await query.edit_message_text(
            f"🎁 <b>Ödüller</b>\n\n"
            f"Günlük: {'Hazır ✅' if not d else format_timedelta(d)}\n"
            f"Haftalık: {'Hazır ✅' if not w else format_timedelta(w)}\n\n"
            f"Komutlar:\n/gunluk\n/haftalik",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        return

    if data == "menu_market":
        text = "🛒 <b>Market</b>\n\n"
        for code, item in MARKET_ITEMS.items():
            text += f"{item['name']} — {format_number(item['price'])} 🪙\nKod: <code>{code}</code>\n\n"
        text += "Satın almak için /buy <item_kodu>"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=main_menu())
        return

    if data == "menu_inventory":
        items = get_inventory(user.id)
        if not items:
            text = "🎒 Envanterin boş."
        else:
            text = "🎒 <b>Envanterin</b>\n\n"
            for name, qty in items:
                text += f"• {name} x{qty}\n"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=main_menu())
        return

    if data == "menu_top":
        rows = top_users(10)
        text = "🏆 <b>Top 10 Oyuncu</b>\n\n"
        for i, r in enumerate(rows, start=1):
            total = r[1] + r[2]
            text += f"{i}. <b>{r[0]}</b> — {format_number(total)} 🪙 | Lv.{r[3]}\n"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=main_menu())
        return

    if data == "menu_missions":
        data_rows = get_missions(user.id)
        text = "📜 <b>Görevler</b>\n\n"
        for m in data_rows:
            status = "✅" if m[5] else ("🎯" if m[2] >= m[3] else "⏳")
            text += f"ID:{m[0]} | {m[1]}\n{m[2]}/{m[3]} | Ödül: {format_number(m[4])} 🪙 {status}\n\n"
        text += "/claim <görev_id>"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=main_menu())
        return

    if data == "menu_achievements":
        achs = get_achievements(user.id)
        text = "🏅 <b>Başarımlar</b>\n\n"
        if not achs:
            text += "Henüz başarım yok."
        else:
            for a in achs:
                text += f"• {a[0]}\n"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=main_menu())
        return

    if data == "menu_help":
        await query.edit_message_text(
            "ℹ️ Komutlar için /help yaz.",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        return

    game_infos = {
        "info_rulet": "🎡 /rulet <miktar> <kırmızı/siyah>",
        "info_blackjack": "🃏 /blackjack <miktar>",
        "info_poker": "♠️ /poker <miktar>",
        "info_slot": "🎰 /slot <miktar>",
        "info_zar": "🎲 /zar <miktar>",
        "info_basket": "🏀 /basket <miktar>",
        "info_coinflip": "🪙 /coinflip <miktar> <yazi/tura>",
        "info_guess": "🔢 /guess <miktar> <1-5>",
        "info_highlow": "📈 /highlow <miktar> <high/low>",
        "info_crash": "🚀 /crash <miktar>",
        "info_mines": "💣 /mines <miktar>",
        "info_duel": "⚔️ /duel <miktar> (reply)",
    }

    if data in game_infos:
        await query.edit_message_text(
            f"ℹ️ <b>Oyun Bilgisi</b>\n\n{game_infos[data]}",
            parse_mode="HTML",
            reply_markup=games_menu()
        )
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
    if not TOKEN:
        raise ValueError("BOT_TOKEN bulunamadı.")

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

    # market
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("inventory", inventory))

    # missions
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

    print("🤖 Casino Bot V2 Railway/GitHub için hazır...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
