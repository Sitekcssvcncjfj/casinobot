from datetime import datetime, timedelta

def format_number(n):
    return f"{n:,}".replace(",", ".")

def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except:
        return None

def now():
    return datetime.utcnow()

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
    remain = timedelta(days=1) - (now() - parse_time(last_daily))
    return remain if remain.total_seconds() > 0 else None

def weekly_remaining(last_weekly):
    if not last_weekly:
        return None
    remain = timedelta(days=7) - (now() - parse_time(last_weekly))
    return remain if remain.total_seconds() > 0 else None
