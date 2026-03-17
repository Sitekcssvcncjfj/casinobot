import sqlite3
from datetime import datetime
from config import DB_NAME, START_BALANCE

conn = sqlite3.connect(DB_NAME, check_same_thread=False)
cursor = conn.cursor()

def init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
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
        created_at TEXT DEFAULT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS game_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        game_name TEXT,
        bet INTEGER,
        result TEXT,
        amount_change INTEGER,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        item_name TEXT,
        quantity INTEGER DEFAULT 1
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS achievements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        achievement_name TEXT,
        unlocked_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS missions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        mission_name TEXT,
        progress INTEGER DEFAULT 0,
        target INTEGER DEFAULT 1,
        reward INTEGER DEFAULT 0,
        claimed INTEGER DEFAULT 0
    )
    """)

    conn.commit()

def now_iso():
    return datetime.utcnow().isoformat()

def get_user(user_id, username):
    cursor.execute("SELECT sikke FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    if row is None:
        cursor.execute("""
            INSERT INTO users (
                user_id, username, sikke, bank, xp, level, total_won, total_lost,
                games_played, games_won, last_daily, last_weekly, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, username, START_BALANCE, 0, 0, 1, 0, 0,
            0, 0, None, None, now_iso()
        ))
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

def update_bank(user_id, amount):
    current = get_bank(user_id)
    new_value = current + amount
    if new_value < 0:
        return False
    cursor.execute("UPDATE users SET bank=? WHERE user_id=?", (new_value, user_id))
    conn.commit()
    return True

def add_stats(user_id, won=0, lost=0, played=0, games_won=0):
    cursor.execute("""
        UPDATE users
        SET total_won = total_won + ?,
            total_lost = total_lost + ?,
            games_played = games_played + ?,
            games_won = games_won + ?
        WHERE user_id=?
    """, (won, lost, played, games_won, user_id))
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
    cursor.execute("""
        INSERT INTO game_logs (user_id, game_name, bet, result, amount_change, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, game_name, bet, result, amount_change, now_iso()))
    conn.commit()

def set_daily(user_id):
    cursor.execute("UPDATE users SET last_daily=? WHERE user_id=?", (now_iso(), user_id))
    conn.commit()

def set_weekly(user_id):
    cursor.execute("UPDATE users SET last_weekly=? WHERE user_id=?", (now_iso(), user_id))
    conn.commit()

def top_users(limit=10):
    cursor.execute("""
        SELECT username, sikke, bank, level
        FROM users
        ORDER BY (sikke + bank) DESC
        LIMIT ?
    """, (limit,))
    return cursor.fetchall()

def global_stats():
    cursor.execute("SELECT COUNT(*) FROM users")
    users = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(sikke + bank) FROM users")
    money = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM game_logs")
    logs = cursor.fetchone()[0]

    return users, money, logs

# MARKET / INVENTORY
def add_item(user_id, item_name, qty=1):
    cursor.execute("""
        SELECT quantity FROM inventory WHERE user_id=? AND item_name=?
    """, (user_id, item_name))
    row = cursor.fetchone()

    if row:
        cursor.execute("""
            UPDATE inventory SET quantity = quantity + ?
            WHERE user_id=? AND item_name=?
        """, (qty, user_id, item_name))
    else:
        cursor.execute("""
            INSERT INTO inventory (user_id, item_name, quantity)
            VALUES (?, ?, ?)
        """, (user_id, item_name, qty))
    conn.commit()

def get_inventory(user_id):
    cursor.execute("""
        SELECT item_name, quantity FROM inventory
        WHERE user_id=?
        ORDER BY item_name ASC
    """, (user_id,))
    return cursor.fetchall()

# ACHIEVEMENTS
def has_achievement(user_id, name):
    cursor.execute("""
        SELECT id FROM achievements WHERE user_id=? AND achievement_name=?
    """, (user_id, name))
    return cursor.fetchone() is not None

def unlock_achievement(user_id, name):
    if has_achievement(user_id, name):
        return False
    cursor.execute("""
        INSERT INTO achievements (user_id, achievement_name, unlocked_at)
        VALUES (?, ?, ?)
    """, (user_id, name, now_iso()))
    conn.commit()
    return True

def get_achievements(user_id):
    cursor.execute("""
        SELECT achievement_name, unlocked_at
        FROM achievements
        WHERE user_id=?
        ORDER BY id DESC
    """, (user_id,))
    return cursor.fetchall()

# MISSIONS
def create_default_missions(user_id):
    cursor.execute("SELECT COUNT(*) FROM missions WHERE user_id=?", (user_id,))
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
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, m[0], m[1], m[2], m[3], m[4]))
    conn.commit()

def update_missions_played(user_id):
    cursor.execute("""
        UPDATE missions
        SET progress = progress + 1
        WHERE user_id=? AND mission_name IN ('İlk Oyunun', '5 Oyun Oyna')
    """, (user_id,))
    conn.commit()

def update_missions_won(user_id):
    cursor.execute("""
        UPDATE missions
        SET progress = progress + 1
        WHERE user_id=? AND mission_name='3 Oyun Kazan'
    """, (user_id,))
    conn.commit()

def get_missions(user_id):
    cursor.execute("""
        SELECT id, mission_name, progress, target, reward, claimed
        FROM missions
        WHERE user_id=?
        ORDER BY id ASC
    """, (user_id,))
    return cursor.fetchall()

def claim_mission(user_id, mission_id):
    cursor.execute("""
        SELECT progress, target, reward, claimed
        FROM missions
        WHERE id=? AND user_id=?
    """, (mission_id, user_id))
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
