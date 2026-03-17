from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💰 Bakiye", callback_data="menu_balance"),
            InlineKeyboardButton("📊 Profil", callback_data="menu_profile"),
        ],
        [
            InlineKeyboardButton("🎮 Oyunlar", callback_data="menu_games"),
            InlineKeyboardButton("🏦 Banka", callback_data="menu_bank"),
        ],
        [
            InlineKeyboardButton("🎁 Ödüller", callback_data="menu_rewards"),
            InlineKeyboardButton("🛒 Market", callback_data="menu_market"),
        ],
        [
            InlineKeyboardButton("🎒 Envanter", callback_data="menu_inventory"),
            InlineKeyboardButton("🏆 Top", callback_data="menu_top"),
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
