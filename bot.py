import os
import json
import aiohttp
import asyncio
import time
import logging
import traceback
import base64
import re
import shutil # Added for file copying
import hashlib
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone
from html import escape
from collections import defaultdict

# Telegram Bot Library Imports
from telegram import (
    Update,
    InputFile,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    CallbackContext,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden, BadRequest

# Environment Variable Loading
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()

# Essential: Get Bot Token
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '9022251823:AAGLKE5FK8-KtQRQU8lBtjzoaAXGkrF6EXE')
if not TOKEN or TOKEN == "9022251823:AAGLKE5FK8-KtQRQU8lBtjzoaAXGkrF6EXE":
    TOKEN = "9022251823:AAGLKE5FK8-KtQRQU8lBtjzoaAXGkrF6EXE" # FALLBACK - Highly recommended to use .env

# Optional: API Configuration — Updated to new fast API
API_BASE_URL = os.getenv('JWT_API_URL', 'https://fast-ob55-jwt.lovable.app/api/public/token')
ACCESS_TOKEN_API_URL = os.getenv('ACCESS_TOKEN_API_URL', 'https://fast-ob55-jwt.lovable.app/api/public/token')

# Optional: Bot Settings
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', 5 * 1024 * 1024))
ADMIN_ID = int(os.getenv('ADMIN_ID', 6627454933))
if not ADMIN_ID or ADMIN_ID == 0:
    print("WARNING: ADMIN_ID environment variable is not set, invalid, or 0.")
MAX_CONCURRENT_REQUESTS = int(os.getenv('MAX_CONCURRENT_REQUESTS', 5))
ADMIN_CONTACT_LINK = os.getenv('ADMIN_CONTACT_LINK', 'https://t.me/PRINCE_H4X')
AUTO_PROCESS_CHECK_INTERVAL = int(os.getenv('AUTO_PROCESS_CHECK_INTERVAL', 60))
# How often (seconds) the live auto-processing status message is refreshed.
PROGRESS_UPDATE_SECONDS = max(2, int(os.getenv('PROGRESS_UPDATE_SECONDS', 5)))
# Timezone used for daily clock-time schedules such as "/setfile 02:30 bd_ac.json". Set SCHEDULE_TIMEZONE to change it.
DEFAULT_SCHEDULE_TZ = os.getenv('SCHEDULE_TIMEZONE', 'Asia/Kolkata')
# Retry settings for failed accounts (used by manual processing AND auto-processing schedules).
# MAX_RETRY_ROUNDS = how many times failed accounts are retried (default 3). RETRY_DELAY_SECONDS = wait before each retry round.
MAX_RETRY_ROUNDS = max(1, int(os.getenv('MAX_RETRY_ROUNDS', 3)))
RETRY_DELAY_SECONDS = max(0, int(os.getenv('RETRY_DELAY_SECONDS', 60)))


# --- Flask Keep-Alive Server ---
from flask import Flask, jsonify
from threading import Thread

flask_app = Flask(__name__)


@flask_app.route('/')
def flask_index():
    return "Bot is alive and running!", 200


@flask_app.route('/health')
def flask_health():
    return jsonify({
        "status": "ok",
        "bot": "jwt-token-bot",
        "time": datetime.now(timezone.utc).isoformat()
    }), 200


def run_flask_server():
    port = int(os.getenv('PORT', 8080))
    try:
        flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as flask_err:
        print(f"Flask server error: {flask_err}")


def keep_alive():
    t = Thread(target=run_flask_server, daemon=True)
    t.start()
    print(f"Flask keep-alive server started on port {os.getenv('PORT', 8080)}")

# --- File Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'bot_data')
TEMP_DIR = os.path.join(DATA_DIR, 'temp_files')
SCHEDULED_FILES_DATA_DIR = os.path.join(DATA_DIR, 'scheduled_files_data')

VIP_FILE = os.path.join(DATA_DIR, 'vip_users.json')
GITHUB_CONFIG_FILE = os.path.join(DATA_DIR, 'githubconfigs.json')
KNOWN_USERS_FILE = os.path.join(DATA_DIR, 'knownusers.json')
SCHEDULED_FILES_CONFIG = os.path.join(DATA_DIR, 'scheduledfiles.json')

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.vendor.ptb_urllib3.urllib3").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def format_time(seconds: float) -> str:
    if seconds is None or seconds < 0: return "N/A"
    try:
        seconds_int = int(seconds)
        if seconds_int < 60:
            return f"{seconds_int}s" if seconds_int >= 0 else "0s"
        delta = timedelta(seconds=seconds_int)
        total_seconds = delta.total_seconds()
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if hours > 0: parts.append(f"{int(hours)}h")
        if minutes > 0 or (hours > 0 and seconds > 0): parts.append(f"{int(minutes)}m")
        if seconds > 0 or (not parts and total_seconds >= 0): parts.append(f"{int(seconds)}s")
        if not parts: return "0s"
        return " ".join(parts).strip()
    except (OverflowError, ValueError):
        return "Infinity"
    except Exception as e:
        logger.warning(f"Error formatting time {seconds}: {e}")
        return "Format Error"

# --- Indian Standard Time (IST) display helpers ---
# All times are STORED in UTC internally, but shown to the user in IST with the UTC time in brackets.
IST_TZ = timezone(timedelta(hours=5, minutes=30), name='IST')

def to_ist(dt: datetime) -> datetime:
    """Converts any datetime (naive is treated as UTC) to IST."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_TZ)

def format_ist(dt: datetime, with_seconds: bool = True, show_utc: bool = True) -> str:
    """'25-10-2026 04:59:10 AM IST (UTC: 2026-10-24 23:29)' - IST first, UTC inside the brackets."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ist_dt = to_ist(dt)
    fmt = '%d-%m-%Y %I:%M:%S %p' if with_seconds else '%d-%m-%Y %I:%M %p'
    text = f"{ist_dt.strftime(fmt)} IST"
    if show_utc:
        utc_dt = dt.astimezone(timezone.utc)
        text += f" (UTC: {utc_dt.strftime('%Y-%m-%d %H:%M')})"
    return text

def build_progress_bar(done: int, total: int, length: int = 10) -> str:
    """Text progress bar such as '▓▓▓▓▓░░░░░'. Reaches a full bar exactly when done == total."""
    if total <= 0:
        return '░' * length
    filled = int(round(length * min(done, total) / total))
    return '▓' * filled + '░' * (length - filled)

def build_auto_progress_text(stage: str, done: int, total: int, success: int, failed: int, started_at: float, extra_line: str = "") -> str:
    """Live dashboard text used by the auto-processing status message (updated every PROGRESS_UPDATE_SECONDS)."""
    elapsed = time.time() - started_at
    pct = (done / total * 100) if total > 0 else 100.0
    est_remaining = -1
    if 0 < done < total and elapsed > 2:
        est_remaining = (elapsed / done) * (total - done)
    lines = [
        f"{stage}",
        f"📊 Progress: {done}/{total} ({pct:.0f}%)",
        f"{build_progress_bar(done, total)} {pct:.0f}%",
        f"✅ Successful: {success}  |  ❌ Failed: {failed}",
        f"⏱️ Elapsed: {format_time(elapsed)}" + (f"  |  ⏳ Left: {format_time(est_remaining)}" if est_remaining >= 0 else ""),
    ]
    if extra_line:
        lines.append(extra_line)
    return "\n".join(lines)

def sanitize_filename(name: str) -> str:
    if not name: return 'Unknown.json'
    sanitized = re.sub(r'[^a-zA-Z0-9_.-]', '_', name)
    sanitized = sanitized.strip(' _.-')
    if not sanitized.lower().endswith('.json'):
        base, _ = os.path.splitext(sanitized)
        sanitized = base + ".json"
    if not sanitized or sanitized == '.json':
        return 'Unknown.json'
    return sanitized

def parse_interval(interval_str: str) -> int | None:
    match = re.match(r'^(\d+)\s*(m|h|d)$', interval_str.lower().strip())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == 'm':
        return value * 60
    elif unit == 'h':
        return value * 3600
    elif unit == 'd':
        return value * 86400
    return None

# --- Daily clock-time schedules + per-schedule GitHub targets ---

def parse_daily_time(time_str: str) -> tuple[int, int] | None:
    """Parses a clock time like '02:30', '2:30am', '2:30 pm', '14:05' or '2am' into (hour24, minute).
    Returns None when the text is not a clock time (for example an interval such as '30m' or '6h')."""
    s = time_str.strip().lower().replace(' ', '')
    match = re.match(r'^(\d{1,2})(?::(\d{2}))?(am|pm)?$', s)
    if not match:
        return None
    hour_str, minute_str, meridiem = match.groups()
    if minute_str is None and meridiem is None:
        return None  # a bare number is not a clock time
    hour = int(hour_str)
    minute = int(minute_str) if minute_str is not None else 0
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == 'pm' else 0)
    elif hour > 23:
        return None
    return hour, minute

def get_schedule_tz(tz_name: str | None):
    name = tz_name or DEFAULT_SCHEDULE_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        fixed_offsets = {'Asia/Kolkata': timedelta(hours=5, minutes=30), 'Asia/Dhaka': timedelta(hours=6)}
        if name in fixed_offsets:
            return timezone(fixed_offsets[name])
        logger.warning(f"Timezone '{name}' is not available on this system. Falling back to UTC.")
        return timezone.utc

def next_daily_run_utc(hour: int, minute: int, tz_name: str | None, after_utc: datetime) -> datetime:
    """Next occurrence (in UTC) of hour:minute in the schedule's timezone, strictly after `after_utc`."""
    tz = get_schedule_tz(tz_name)
    local_after = after_utc.astimezone(tz)
    candidate = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_after:
        candidate = (candidate + timedelta(days=1)).replace(hour=hour, minute=minute)
    return candidate.astimezone(timezone.utc)

def compute_next_run_time(schedule_info: dict, after_utc: datetime) -> datetime | None:
    """Next run for a schedule: the next clock time for daily schedules, now + interval for interval schedules."""
    daily_time = schedule_info.get('daily_time')
    if daily_time:
        parsed = parse_daily_time(str(daily_time))
        if parsed:
            return next_daily_run_utc(parsed[0], parsed[1], schedule_info.get('schedule_tz'), after_utc)
        logger.error(f"Invalid daily_time '{daily_time}' in schedule info.")
        return None
    interval_s = schedule_info.get('interval_seconds')
    if isinstance(interval_s, int) and interval_s > 0:
        return after_utc + timedelta(seconds=interval_s)
    return None

def derive_token_filename(schedule_name: str) -> str | None:
    """ind_ac.json -> ind_token.json, bd_ac.json -> bd_token.json. Returns None if the name does not end with _ac.json."""
    match = re.match(r'^(.+)_ac\.json$', schedule_name, flags=re.IGNORECASE)
    return f"{match.group(1)}_token.json" if match else None

def make_schedule_key(schedule_name: str, github_repo: str | None) -> str:
    """Schedules tied to a GitHub repo are stored as 'name@owner/repo' so the same file name can be used for several repos."""
    return f"{schedule_name}@{github_repo}" if github_repo else schedule_name

def schedule_storage_name(schedule_key: str) -> str:
    """Filesystem-safe version of a schedule key (plain names are returned unchanged)."""
    safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', schedule_key)
    if safe != schedule_key:
        digest = hashlib.sha1(schedule_key.encode('utf-8')).hexdigest()[:8]
        safe = f"{safe}_{digest}.json"
    return safe

def load_json_data(filepath: str, default_value=None) -> dict | list:
    if default_value is None:
        default_value = {}
    try:
        dir_name = os.path.dirname(filepath)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info(f"File {filepath} not found, creating with default value.")
        save_json_data(filepath, default_value)
        return default_value
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {filepath}: {e}. Backing up corrupted file and returning default.")
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            corrupted_backup_path = f"{filepath}.corrupted_{timestamp}"
            os.rename(filepath, corrupted_backup_path)
            logger.info(f"Backed up corrupted file to {corrupted_backup_path}")
        except OSError as ren_err:
            logger.error(f"Could not backup corrupted file {filepath}: {ren_err}")
        save_json_data(filepath, default_value)
        return default_value
    except Exception as e:
        logger.error(f"Unexpected error loading {filepath}: {e}. Returning default value.", exc_info=True)
        return default_value

def save_json_data(filepath: str, data: dict | list) -> bool:
    temp_filepath = filepath + ".tmp"
    try:
        dir_name = os.path.dirname(filepath)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_filepath, filepath)
        logger.debug(f"Successfully saved data to {filepath}")
        return True
    except OSError as e:
        logger.error(f"OS Error saving data to {filepath}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error saving data to {filepath}: {e}", exc_info=True)
        return False
    finally:
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except OSError as e:
                logger.warning(f"Could not remove temporary save file {temp_filepath}: {e}")

# --- VIP User Management ---
def load_vip_data() -> dict:
    return load_json_data(VIP_FILE, {})

def save_vip_data(data: dict) -> bool:
    return save_json_data(VIP_FILE, data)

def is_user_vip(user_id: int) -> bool:
    if ADMIN_ID and ADMIN_ID != 0 and user_id == ADMIN_ID:
        return True
    vip_data = load_vip_data()
    user_id_str = str(user_id)
    if user_id_str in vip_data and isinstance(vip_data.get(user_id_str), dict):
        try:
            expiry_iso = vip_data[user_id_str].get('expiry')
            if expiry_iso:
                expiry_dt = datetime.fromisoformat(expiry_iso.replace('Z', '+00:00'))
                return expiry_dt > datetime.now(timezone.utc)
            else:
                return False
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Invalid VIP data format for {user_id_str}: {e}")
            return False
    return False

def get_vip_expiry(user_id: int) -> str | None:
    if ADMIN_ID and ADMIN_ID != 0 and user_id == ADMIN_ID:
        return "Permanent (Bot Owner/Admin) 👑"
    vip_data = load_vip_data()
    user_id_str = str(user_id)
    if user_id_str in vip_data and isinstance(vip_data.get(user_id_str), dict):
        try:
            expiry_iso = vip_data[user_id_str].get('expiry')
            if expiry_iso:
                expiry_dt = datetime.fromisoformat(expiry_iso.replace('Z', '+00:00'))
                if expiry_dt > datetime.now(timezone.utc):
                    return format_ist(expiry_dt)
                else:
                    return None
            else:
                return None
        except (ValueError, KeyError, TypeError):
            return "Invalid Date Stored"
    return None

# --- GitHub Config Management ---
def load_github_configs() -> dict:
    return load_json_data(GITHUB_CONFIG_FILE, {})

def save_github_configs(data: dict) -> bool:
    return save_json_data(GITHUB_CONFIG_FILE, data)

def get_user_github_configs(user_id_str: str) -> dict:
    github_configs = load_github_configs()
    user_data = github_configs.get(user_id_str)
    if not user_data:
        return {}
    if isinstance(user_data, dict) and 'github_token' in user_data:
        repo_key = user_data.get('github_repo', 'unknown_repo')
        new_format = {repo_key: user_data}
        github_configs[user_id_str] = new_format
        if save_github_configs(github_configs):
            logger.info(f"Migrated old single GitHub config for user {user_id_str}")
        return new_format
    if isinstance(user_data, dict):
        return user_data
    return {}

# --- Multi-file per repo helpers (same repo, different token files such as ind_token.json and bd_token.json) ---
def get_config_repo_name(config_key: str, config) -> str:
    """The real 'owner/repo' of a stored GitHub config (keys can be 'owner/repo' or 'owner/repo#file.json')."""
    if isinstance(config, dict) and config.get('github_repo'):
        return str(config['github_repo'])
    return str(config_key).split('#', 1)[0]

def find_github_config_keys(configs: dict, repo: str, filename: str | None = None) -> list:
    """Keys of all configs for `repo` (case-insensitive). If `filename` is given, only configs uploading to that file."""
    matches = []
    for key, cfg in (configs or {}).items():
        if not isinstance(cfg, dict):
            continue
        if get_config_repo_name(key, cfg).lower() != str(repo).lower():
            continue
        if filename is not None and str(cfg.get('github_filename', '')).lower() != str(filename).lower():
            continue
        matches.append(key)
    return matches

def make_github_config_key(configs: dict, repo: str, filename: str) -> tuple[str, bool]:
    """Storage key for a /setgithub call. Returns (key, is_update).
    Same repo + same filename updates that config. Same repo + NEW filename is stored as an extra config
    (key 'owner/repo#filename.json') so it never overwrites the other file. The first config of a repo keeps the plain 'owner/repo' key."""
    existing = find_github_config_keys(configs, repo, filename)
    if existing:
        return existing[0], True
    if repo not in (configs or {}):
        return repo, False
    return f"{repo}#{filename}", False

def pick_github_configs_for_repo(configs: dict, repo: str, target_filename: str | None = None) -> dict:
    """Configs a schedule bound to `repo` should use. With a target file name: the config for that file (or the first config of the repo,
    whose token/branch are reused). Without one: every config of that repo."""
    keys = find_github_config_keys(configs, repo)
    if not keys:
        return {}
    if target_filename:
        exact = find_github_config_keys(configs, repo, target_filename)
        chosen = exact[0] if exact else keys[0]
        return {chosen: configs[chosen]}
    return {k: configs[k] for k in keys}

# --- Known User Management ---
def load_known_users() -> set:
    user_list = load_json_data(KNOWN_USERS_FILE, [])
    valid_users = set()
    if isinstance(user_list, list):
        for item in user_list:
            if isinstance(item, int) and item != 0:
                valid_users.add(item)
            elif isinstance(item, str) and item.isdigit() and int(item) != 0:
                valid_users.add(int(item))
    else:
        logger.error(f"Known users data not a list. Resetting.")
        save_known_users(set())
        return set()
    return valid_users

def save_known_users(user_set: set) -> bool:
    int_user_list = sorted([int(uid) for uid in user_set if isinstance(uid, (int, str)) and str(uid).isdigit() and int(str(uid)) != 0])
    return save_json_data(KNOWN_USERS_FILE, int_user_list)

def add_known_user(user_id: int) -> None:
    if not isinstance(user_id, int) or user_id == 0:
        return
    known_users = load_known_users()
    if user_id not in known_users:
        known_users.add(user_id)
        if save_known_users(known_users):
            logger.info(f"Added user {user_id} ({len(known_users)} total).")
        else:
            logger.error(f"Failed to save known users after adding {user_id}.")

# --- Scheduled File Management ---
def load_scheduled_files() -> dict:
    return load_json_data(SCHEDULED_FILES_CONFIG, {})

def save_scheduled_files(data: dict) -> bool:
    return save_json_data(SCHEDULED_FILES_CONFIG, data)


# ==========================================
# 🎨 PREMIUM EMOJI MAPPING
# ==========================================
EMOJI_MAP = {
    "👋": ("5258029071207505708", "👋"),
    "🚀": ("5188481279963715781", "🚀"),
    "📁": ("6235478849417647339", "📁"),
    "✔️": ("5208893571599449245", "✔️"),
    "⚠️": ("5447644880824181073", "⚠️"),
    "✨": ("5325547803936572038", "✨"),
    "📩": ("5253742260054409879", "📩"),
    "ℹ️": ("5334544901428229844", "ℹ️"),
    "🌟": ("4920593664222168414", "🌟"),
    "🔄": ("5226702984204797593", "🔄"),
    "⏰": ("5413704112220949842", "⏰"),
    "⏱️": ("5382194935057372936", "⏱️"),
    "📊": ("5231200819986047254", "📊"),
    "📅": ("5265168676948035250", "📅"),
    "🗓️": ("5472026645659401564", "🗓️"),
    "🎯": ("5310278924616356636", "🎯"),
    "💼": ("5947137379641855171", "💼"),
    "🏷️": ("5370673049630691987", "🏷️"),
    "📄": ("5298877105000439431", "📄"),
    "⚙️": ("5307544885874664176", "⚙️"),
    "🏁": ("5411520005386806155", "🏁"),
    "⬇️": ("5443127283898405358", "⬇️"),
    "💥": ("5276032951342088188", "💥"),
    "♻️": ("5377584064326804458", "♻️"),
    "⏳": ("5451732530048802485", "⏳"),
    "🤖": ("4940635119791637363", "🤖"),
    "🎉": ("5461151367559141950", "🎉"),
    "📣": ("5472291748220771063", "📣"),
    "🧹": ("5424818078833715060", "🧹"),
    "❓": ("5463139580934892960", "❓"),
    "🚫": ("5240241223632954241", "🚫"),
    "❌": ("6114014038960638990", "❌"),
    "✅": ("6114060708075277880", "✅"),
    "🔧": ("5462921117423384478", "🔧"),
    "📤": ("5445355530111437729", "📤"),
    "📇": ("5032963696746300412", "📇"),
    "🛒": ("5312361253610475399", "🛒"),
    "🆘": ("5238025132177369293", "🆘"),
    "👑": ("6079910448147205721", "👑"),
    "🆔": ("5301096984617166561", "🆔"),
    "🏆": ("5188344996356448758", "🏆"),
    "📌": ("5397782960512444700", "📌"),
    "🔑": ("5330115548900501467", "🔑"),
    "🌐": ("5375177250553487549", "🌐"),
    "🛡️": ("5285087932107989972", "🛡️"),
    "📋": ("5886223731088431288", "📋"),
    "🔀": ("5974582963692637085", "🔀"),
    "📚": ("5222444124698853913", "📚"),
    "👉": ("5199412938099666948", "👉"),
    "💡": ("5422439311196834318", "💡"),
    "📎": ("6097974883025688046", "📎"),
    "👥": ("5453957997418004470", "👥"),
    "⬆️": ("5445355530111437729", "⬆️"),
    "⚡": ("6257850144356569423", "⚡"),
    "👤": ("5249053508681883137", "👤"),
}

# ==========================================
# 🎨 INLINE BUTTON ICON MAPPING
# ==========================================
BUTTON_ICON_MAP = {
    "Process File": "5445355530111437729",
    "Vip Status": "5032963696746300412",
    "Vip Shop": "5312361253610475399",
    "GitHub Status": "5231200819986047254",
    "Scheduled Files": "5307544885874664176",
    "Help": "5238025132177369293",
    "Cancel": "6282852025159913806",
    "Remove GitHub": "5462921117423384478",
    "VIP Info": "4920593664222168414",
    "Back": "5886676966102274844",
    "Refresh": "5226702984204797593",
    "Upload": "5445355530111437729",
    "Download": "5443127283898405358",
}

# ==========================================
# 🎨 REPLY KEYBOARD BUTTON ICON MAPPING
# ==========================================
REPLY_KEYBOARD_BUTTON_ICON_MAP = {
    "Process File 📤": "5445355530111437729",
    "Vip Status 📇": "5032963696746300412",
    "Vip Shop 🛒": "5312361253610475399",
    "GitHub Status 📊": "5231200819986047254",
    "Scheduled Files ⚙️": "5307544885874664176",
    "Help 🆘": "5238025132177369293",
    "Cancel ❌": "6114014038960638990",
}


# ==========================================
# 🔧 strip_emojis
# ==========================================
def strip_emojis(text: str) -> str:
    emoji_pattern = re.compile(
        r'['
        r'\U0001F1E6-\U0001F1FF\U0001F600-\U0001F64F\U0001F300-\U0001F5FF'
        r'\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF'
        r'\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF'
        r'\u2600-\u26FF\u2700-\u27BF\u2300-\u23FF\uFE0F\u200D'
        r']+',
        flags=re.UNICODE
    )
    return emoji_pattern.sub('', text).strip()


# ==========================================
# 🎨 convert_to_premium_emoji
# ==========================================
def convert_to_premium_emoji(text: str) -> str:
    result = []
    i = 0
    while i < len(text):
        # Already converted text: copy existing <tg-emoji ...>..</tg-emoji> tags untouched (prevents nested/double wrapping)
        if text.startswith('<tg-emoji', i):
            close_idx = text.find('</tg-emoji>', i)
            if close_idx != -1:
                close_end = close_idx + len('</tg-emoji>')
                result.append(text[i:close_end])
                i = close_end
                continue
        matched = False
        for length in (2, 1):
            chunk = text[i:i + length]
            if chunk in EMOJI_MAP:
                emoji_id, fallback = EMOJI_MAP[chunk]
                result.append(
                    f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
                )
                i += length
                matched = True
                break
        if not matched:
            result.append(text[i])
            i += 1
    return ''.join(result)


# ==========================================
# 🎨 PREMIUM INLINE BUTTON BUILDER
# ==========================================
def make_premium_button(
    text: str,
    callback_data: str = None,
    url: str = None,
    icon_id: str = None,
    style: str = None
) -> InlineKeyboardButton:
    clean_text = strip_emojis(text) or "Button"
    kwargs = {"text": clean_text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if icon_id:
        kwargs["icon_custom_emoji_id"] = str(icon_id)
    if style:
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)


# ==========================================
# ⌨️ PREMIUM REPLY KEYBOARD BUTTON BUILDER
# ==========================================
def make_reply_button(text: str, icon_id: str = None, style: str = None) -> KeyboardButton:
    kwargs = {"text": text}
    if icon_id:
        kwargs["icon_custom_emoji_id"] = str(icon_id)
    if style:
        kwargs["style"] = style
    return KeyboardButton(**kwargs)


# --- Command Buttons ---
COMMAND_BUTTONS_LAYOUT = [
    ["Process File", "Vip Status"],
    ["Vip Shop", "GitHub Status"],
    ["Scheduled Files", "Help"],
    ["Cancel"]
]

# Premium reply keyboard with icons + colours
main_reply_markup = ReplyKeyboardMarkup(
    [
        [
            make_reply_button("Process File", REPLY_KEYBOARD_BUTTON_ICON_MAP["Process File 📤"], "primary"),
            make_reply_button("Vip Status", REPLY_KEYBOARD_BUTTON_ICON_MAP["Vip Status 📇"], "success"),
        ],
        [
            make_reply_button("Vip Shop", REPLY_KEYBOARD_BUTTON_ICON_MAP["Vip Shop 🛒"], "primary"),
            make_reply_button("GitHub Status", REPLY_KEYBOARD_BUTTON_ICON_MAP["GitHub Status 📊"], "success"),
        ],
        [
            make_reply_button("Scheduled Files", REPLY_KEYBOARD_BUTTON_ICON_MAP["Scheduled Files ⚙️"], "primary"),
            make_reply_button("Help", REPLY_KEYBOARD_BUTTON_ICON_MAP["Help 🆘"], "success"),
        ],
        [
            make_reply_button("Cancel", REPLY_KEYBOARD_BUTTON_ICON_MAP["Cancel ❌"], "danger"),
        ],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)


# --- Inline Keyboard Builders ---
def build_main_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            make_premium_button("Process File", callback_data="inline_process",
                                icon_id=BUTTON_ICON_MAP["Process File"], style="primary"),
            make_premium_button("Vip Status", callback_data="inline_vipstatus",
                                icon_id=BUTTON_ICON_MAP["Vip Status"], style="success"),
        ],
        [
            make_premium_button("Vip Shop", callback_data="inline_vipshop",
                                icon_id=BUTTON_ICON_MAP["Vip Shop"], style="primary"),
            make_premium_button("GitHub Status", callback_data="inline_github",
                                icon_id=BUTTON_ICON_MAP["GitHub Status"], style="success"),
        ],
        [
            make_premium_button("Scheduled Files", callback_data="inline_scheduledfiles",
                                icon_id=BUTTON_ICON_MAP["Scheduled Files"], style="primary"),
            make_premium_button("Help", callback_data="inline_help",
                                icon_id=BUTTON_ICON_MAP["Help"], style="success"),
        ],
        [
            make_premium_button("Cancel", callback_data="inline_cancel",
                                icon_id=BUTTON_ICON_MAP["Cancel"], style="danger"),
        ],
    ])


# --- Bot Command Handlers ---

async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not user: return
    add_known_user(user.id)
    context.user_data.pop('pending_schedule', None)
    context.user_data.pop('waiting_for_json', None)

    username = escape(user.first_name) or "there"

    raw_msg = (
        f"👋 Hello <b>{username}</b>!\n\n"
        "🚀 Welcome to the <b>JWT Token Generator Bot</b>!\n\n"
        "📁 Send me a JSON file containing account credentials.\n\n"
        "✅ <b>Supported Input Formats</b>\n"
        "<i>All formats can be mixed in the same file.</i>\n\n"
        "🔑 <b>UID + Password Format</b>\n"
        '<pre>[\n'
        '  {\n'
        '    "uid": "12345",\n'
        '    "password": "PASS123"\n'
        '  },\n'
        '  {\n'
        '    "uid": "67890",\n'
        '    "password": "PASS456"\n'
        '  }\n'
        ']</pre>\n\n'
        "🔑 <b>Access Token Format</b>\n"
        '<pre>[\n'
        '  {\n'
        '    "access_token": "YOUR_ACCESS_TOKEN_1"\n'
        '  },\n'
        '  {\n'
        '    "access_token": "YOUR_ACCESS_TOKEN_2"\n'
        '  }\n'
        ']</pre>\n\n'
        "🔀 <b>Mixed Format</b>\n"
        '<pre>[\n'
        '  {\n'
        '    "uid": "12345",\n'
        '    "password": "PASS123"\n'
        '  },\n'
        '  {\n'
        '    "access_token": "YOUR_ACCESS_TOKEN"\n'
        '  }\n'
        ']</pre>\n\n'
        "✔️ Tokens returned in the <b>same order</b> as your input file.\n"
        "✅ Successful tokens → <code>jwt_token.json</code> + region files\n"
        "✔️ Working entries → <code>working_account.json</code>\n"
        "❌ Failed entries → <code>lost_account.json</code>\n\n"
        f"⚠️ Max file size: {MAX_FILE_SIZE / 1024 / 1024:.1f} MB\n\n"
        "✨ <b>VIP Features:</b>\n"
        "  • Auto-upload tokens to <b>multiple</b> GitHub repos.\n"
        "  • Schedule files for automatic periodic processing "
        "(<code>/setfile</code>).\n\n"
        "📚 Use /help or the Help button to see all commands."
    )
    start_msg = convert_to_premium_emoji(raw_msg)

    await update.effective_message.reply_text(
        start_msg,
        reply_markup=main_reply_markup,
        parse_mode=ParseMode.HTML
    )


async def help_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user: add_known_user(user.id)
    context.user_data.pop('pending_schedule', None)
    context.user_data.pop('waiting_for_json', None)

    help_text_raw = (
        "🆘 <b>Help Center</b>\n\n"
        "📌 <b>Available Commands:</b>\n"
        "  <code>/start</code> - Show the main welcome message\n"
        "  <code>/help</code> - Show this help message\n"
        "  <code>/vipstatus</code> - Check your current VIP status\n"
        "  <code>/vipshop</code> - View available VIP plans\n"
        "  <code>/cancel</code> - Cancel the current operation\n\n"
        "🔧 <b>VIP Commands (for VIP users only):</b>\n"
        "  <code>/setgithub &lt;TOKEN&gt; &lt;owner/repo&gt; &lt;branch&gt; &lt;filename.json&gt;</code> - Add/update a GitHub config.\n"
        "  <code>/removegithub &lt;owner/repo&gt; [filename.json]</code> - Remove a specific GitHub config (add the file name if the repo has several).\n"
        "  <code>/mygithub</code> - Show all your current GitHub configurations.\n"
        "  <code>/setfile &lt;Interval or Time&gt; &lt;ScheduleName.json&gt; [owner/repo] [TokenFile.json]</code> - Schedule a file for auto-processing.\n"
        "     <i>Interval format:</i> <code>Xm</code> (minutes), <code>Xh</code> (hours), <code>Xd</code> (days). Min: 5m.\n"
        "     <i>Daily time:</i> <code>02:30</code> or <code>2:30am</code> - runs every day at that time.\n"
        "     <i>Example:</i> <code>/setfile 02:00 ind_ac.json MyUser/Repo1</code> (updates <code>ind_token.json</code> in that repo)\n"
        "  <code>/removefile &lt;ScheduleName.json&gt; [owner/repo]</code> - Stop auto-processing for a scheduled file.\n"
        "  <code>/scheduledfiles</code> - List your currently scheduled files.\n\n"
        "📋 <b>Input File Format:</b>\n\n"
        "🔑 <b>UID + Password:</b>\n"
        '<pre>[\n'
        '  {\n'
        '    "uid": "12345",\n'
        '    "password": "PASS123"\n'
        '  }\n'
        ']</pre>\n'
        "🔑 <b>Access Token:</b>\n"
        '<pre>[\n'
        '  {\n'
        '    "access_token": "YOUR_ACCESS_TOKEN"\n'
        '  }\n'
        ']</pre>\n'
        "  ✅ Both types can be <b>mixed</b> in the same file!\n"
        "  ✅ Output is always in <b>same order</b> as input.\n\n"
        "👑 <b>Admin Commands (Bot Admin only):</b>\n"
        "  <code>/vip add &lt;user_id&gt; &lt;days&gt;</code> - Add/extend VIP\n"
        "  <code>/vip remove &lt;user_id&gt;</code> - Remove VIP, all GitHub configs &amp; scheduled files\n"
        "  <code>/vip list</code> - Show active VIP users\n"
        "  <code>/broadcast &lt;message&gt;</code> - Send a message to all known users\n\n"
        "📤 <b>Manual Processing:</b>\n"
        "  1. Send a JSON file (uid/password pairs or access_token entries or mixed).\n"
        "  2. Bot processes it and returns result files in original order.\n"
        "  3. VIPs with GitHub config get <code>jwt_token.json</code> uploaded to all configured repos.\n\n"
        "⚙️ <b>Automatic Processing (VIP):</b>\n"
        "  1. Use <code>/setfile</code> to define a schedule and name.\n"
        "  2. Send the corresponding JSON file when prompted.\n"
        "  3. Bot auto-processes at the set interval or daily time and uploads to the chosen GitHub repo (or all repos if none is chosen).\n"
    )
    help_text = convert_to_premium_emoji(help_text_raw)
    await update.effective_message.reply_text(
        help_text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_markup,
        disable_web_page_preview=True
    )


async def vip_shop_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user: add_known_user(user.id)
    context.user_data.pop('pending_schedule', None)
    context.user_data.pop('waiting_for_json', None)

    vip_shop_text_raw = (
        "✨ Unlock <b>Automatic GitHub Uploads</b> &amp; <b>Scheduled File Processing</b> ✨\n"
        "&amp; Other Premium Features Instantly!\n\n"
        "💼 <b>Available Plans &amp; Prices:</b>\n\n"
        "🗓️   <code>7 Days</code>       —   <code>₹ 10</code>\n"
        "🗓️  <code>15 Days</code>     —   <code>₹ 49</code>\n"
        "📅  <code>1 Month</code>      —   <code>₹ 69</code>\n"
        "📅  <code>2 Months</code>     —   <code>₹ 89</code>\n"
        "📅  <code>3 Months</code>     —   <code>₹ 99</code>\n"
        "🎯  <code>1 Year</code>       —   <code>₹ 159</code>\n\n"
        "📩 <b>To Purchase VIP Membership:</b>\n"
        f"Contact Admin 👉 <a href='{ADMIN_CONTACT_LINK}'>Admin Contact</a>"
    )

    vip_shop_text = convert_to_premium_emoji(vip_shop_text_raw)
    await update.effective_message.reply_text(
        vip_shop_text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_markup,
        disable_web_page_preview=True
    )


async def vip_status_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not user: return
    user_id = user.id
    add_known_user(user_id)
    context.user_data.pop('pending_schedule', None)
    context.user_data.pop('waiting_for_json', None)

    expiry_date_str = get_vip_expiry(user_id)

    if expiry_date_str and "Invalid" not in expiry_date_str:
        status_msg_raw = f"🌟 <b>VIP Status:</b> Active\n<b>Expires:</b> <code>{escape(expiry_date_str)}</code>"
    elif expiry_date_str == "Invalid Date Stored":
        status_msg_raw = "⚠️ <b>VIP Status:</b> Error reading expiry date. Please contact admin."
    else:
        status_msg_raw = "ℹ️ <b>Status:</b> Regular User\nUse /vipshop to upgrade and unlock premium features!"

    status_msg = convert_to_premium_emoji(status_msg_raw)
    await update.effective_message.reply_text(
        status_msg,
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_markup
    )


async def cancel(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    user_id = user.id if user else "Unknown"
    cleared_action = False
    if context.user_data.pop('pending_schedule', None):
        cleared_action = True
        logger.info(f"User {user_id} cancelled pending file schedule setup.")
        await update.effective_message.reply_text(
            convert_to_premium_emoji("✅ Scheduled file setup cancelled. Returning to main menu."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
    elif context.user_data.pop('waiting_for_json', None):
        cleared_action = True
        logger.info(f"User {user_id} cancelled waiting for manual JSON process.")
        await update.effective_message.reply_text(
            convert_to_premium_emoji("✅ Waiting for manual process file cancelled. Returning to main menu."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )

    if not cleared_action:
        logger.info(f"User {user_id} used /cancel, but no active operation found.")
        await update.effective_message.reply_text(
            convert_to_premium_emoji("ℹ️ No active operation to cancel. Returning to main menu."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )


# --- Inline Button Callback Handler ---
async def inline_button_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data
    user = update.effective_user
    if user:
        add_known_user(user.id)

    try:
        if data == "inline_help":
            await help_command(update, context)
        elif data == "inline_vipshop":
            await vip_shop_command(update, context)
        elif data == "inline_vipstatus":
            await vip_status_command(update, context)
        elif data == "inline_github":
            await my_github_config(update, context)
        elif data == "inline_scheduledfiles":
            await list_scheduled_files(update, context)
        elif data == "inline_cancel":
            await cancel(update, context)
        elif data == "inline_process":
            context.user_data['waiting_for_json'] = True
            await query.message.reply_text(
                convert_to_premium_emoji(
                    "📁 Please send the JSON file now for manual processing.\n\n"
                    "✅ <b>Supported Formats</b>\n\n"
                    "🔑 <b>UID + Password:</b>\n"
                    '<pre>[\n'
                    '  {\n'
                    '    "uid": "12345",\n'
                    '    "password": "PASS123"\n'
                    '  }\n'
                    ']</pre>\n'
                    "🔑 <b>Access Token:</b>\n"
                    '<pre>[\n'
                    '  {\n'
                    '    "access_token": "YOUR_ACCESS_TOKEN"\n'
                    '  }\n'
                    ']</pre>\n'
                    "🔀 <b>Mixed Format</b> (both in one file) is also supported!"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await query.answer("Unknown action.", show_alert=True)
    except TelegramError as e:
        logger.error(f"Inline callback error: {e}")
        try:
            await query.answer("An error occurred. Please try again.", show_alert=True)
        except TelegramError:
            pass


# --- File Processing Logic ---

async def _process_by_uid_password(
    session: aiohttp.ClientSession,
    account: dict,
    semaphore: asyncio.Semaphore
) -> tuple:
    uid = account.get("uid")
    password = account.get("password")
    original_account_info = account.copy()
    error_reason = None

    if not uid:
        error_reason = "Missing 'uid'"
    elif not password:
        error_reason = "Missing 'password'"

    if error_reason:
        logger.debug(f"Skipping uid/password account: {error_reason}")
        return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason

    uid_str = str(uid)

    async with semaphore:
        params = {'uid': uid_str, 'password': password}
        try:
            async with session.get(API_BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=60)) as response:
                response_text = await response.text()

                if 200 <= response.status < 300:
                    try:
                        result = json.loads(response_text)
                        if isinstance(result, dict) and result.get('success') and result.get('token'):
                            token = result['token']
                            region = result.get('region') or result.get('noti_region') or result.get('lock_region')
                            logger.info(f"Success [uid/pass]: Token for UID: {uid_str} (Region: {region})")
                            return token, region, original_account_info, None, None
                        else:
                            err_msg = result.get('message') or result.get('error') or "API OK but no token/success"
                            logger.warning(f"uid/pass API no token for UID {uid_str}: {err_msg}")
                            return None, None, None, {**original_account_info, "error_reason": err_msg}, err_msg
                    except json.JSONDecodeError:
                        err_msg = f"API OK ({response.status}) but Non-JSON response"
                        logger.error(f"{err_msg} for UID: {uid_str}")
                        return None, None, None, {**original_account_info, "error_reason": err_msg}, err_msg
                    except Exception as e:
                        err_msg = f"API OK ({response.status}) but parsing error: {e}"
                        logger.error(f"{err_msg} for UID: {uid_str}", exc_info=True)
                        return None, None, None, {**original_account_info, "error_reason": err_msg}, err_msg
                else:
                    error_detail = f"API Error ({response.status})"
                    try:
                        error_json = json.loads(response_text)
                        if isinstance(error_json, dict):
                            msg = error_json.get('message') or error_json.get('error') or error_json.get('detail')
                            if msg and isinstance(msg, str):
                                error_detail += f": {msg[:100]}"
                    except (json.JSONDecodeError, TypeError):
                        pass
                    logger.warning(f"API Error [uid/pass] UID: {uid_str}. Status: {response.status}")
                    return None, None, None, {**original_account_info, "error_reason": error_detail}, error_detail

        except asyncio.TimeoutError:
            error_reason = "Request Timeout"
            logger.warning(f"Timeout [uid/pass] UID: {uid_str}")
            return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason
        except aiohttp.ClientConnectorError as e:
            error_reason = f"Network Error: {e}"
            logger.error(f"Network Error [uid/pass] UID {uid_str}: {e}")
            return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason
        except aiohttp.ClientError as e:
            error_reason = f"HTTP Client Error: {e}"
            logger.error(f"AIOHTTP Error [uid/pass] UID {uid_str}: {e}")
            return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason
        except Exception as e:
            error_reason = f"Unexpected Processing Error: {e}"
            logger.error(f"Unexpected error [uid/pass] UID {uid_str}: {e}", exc_info=True)
            return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason


async def _process_by_access_token(
    session: aiohttp.ClientSession,
    account: dict,
    semaphore: asyncio.Semaphore
) -> tuple:
    access_token = account.get("access_token")
    original_account_info = account.copy()

    if not access_token:
        error_reason = "Missing 'access_token'"
        return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason

    async with semaphore:
        params = {'access_token': access_token}
        try:
            async with session.get(ACCESS_TOKEN_API_URL, params=params, timeout=aiohttp.ClientTimeout(total=60)) as response:
                response_text = await response.text()

                if 200 <= response.status < 300:
                    try:
                        result = json.loads(response_text)
                        body = result.get('body') if isinstance(result, dict) else None

                        if body and isinstance(body, dict) and body.get('success') and body.get('token'):
                            token = body['token']
                            region = body.get('region') or body.get('noti_region') or body.get('lock_region')
                            logger.info(f"Success [access_token]: Token ending ...{access_token[-8:]} (Region: {region})")
                            return token, region, original_account_info, None, None
                        elif result.get('success') and result.get('token'):
                            token = result['token']
                            region = result.get('region')
                            logger.info(f"Success [access_token flat]: Token (Region: {region})")
                            return token, region, original_account_info, None, None
                        else:
                            err_msg = (body or result).get('message') or (body or result).get('error') or "API OK but no token/success"
                            logger.warning(f"access_token API no token.")
                            return None, None, None, {**original_account_info, "error_reason": err_msg}, err_msg
                    except json.JSONDecodeError:
                        err_msg = f"API OK ({response.status}) but Non-JSON response"
                        logger.error(f"{err_msg} for access_token.")
                        return None, None, None, {**original_account_info, "error_reason": err_msg}, err_msg
                    except Exception as e:
                        err_msg = f"API OK ({response.status}) but parsing error: {e}"
                        logger.error(f"{err_msg} for access_token", exc_info=True)
                        return None, None, None, {**original_account_info, "error_reason": err_msg}, err_msg
                else:
                    error_detail = f"API Error ({response.status})"
                    try:
                        error_json = json.loads(response_text)
                        if isinstance(error_json, dict):
                            body_err = error_json.get('body', {})
                            msg = error_json.get('message') or (body_err.get('message') if isinstance(body_err, dict) else None) or error_json.get('error')
                            if msg and isinstance(msg, str):
                                error_detail += f": {msg[:100]}"
                    except (json.JSONDecodeError, TypeError):
                        pass
                    logger.warning(f"API Error [access_token]. Status: {response.status}")
                    return None, None, None, {**original_account_info, "error_reason": error_detail}, error_detail

        except asyncio.TimeoutError:
            error_reason = "Request Timeout"
            logger.warning(f"Timeout [access_token] ...{access_token[-8:]}")
            return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason
        except aiohttp.ClientConnectorError as e:
            error_reason = f"Network Error: {e}"
            logger.error(f"Network Error [access_token]: {e}")
            return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason
        except aiohttp.ClientError as e:
            error_reason = f"HTTP Client Error: {e}"
            logger.error(f"AIOHTTP Error [access_token]: {e}")
            return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason
        except Exception as e:
            error_reason = f"Unexpected Processing Error: {e}"
            logger.error(f"Unexpected error [access_token]: {e}", exc_info=True)
            return None, None, None, {**original_account_info, "error_reason": error_reason}, error_reason


def is_failed_result(res) -> bool:
    """True when a process_account result is an exception or did not produce a token (so it should be retried)."""
    if isinstance(res, Exception):
        return True
    try:
        return not res[0]
    except Exception:
        return True


async def process_account(
    session: aiohttp.ClientSession,
    account: dict,
    semaphore: asyncio.Semaphore
) -> tuple:
    if not isinstance(account, dict):
        error_reason = f"Invalid entry type: expected dict, got {type(account)}"
        return None, None, None, {"error_reason": error_reason}, error_reason

    has_access_token = bool(account.get("access_token"))
    has_uid_pass = bool(account.get("uid")) and bool(account.get("password"))

    if has_access_token:
        return await _process_by_access_token(session, account, semaphore)
    elif has_uid_pass:
        return await _process_by_uid_password(session, account, semaphore)
    else:
        error_reason = "Skipped: entry must have 'access_token' OR both 'uid' and 'password'"
        logger.debug(f"Skipping invalid entry: {account}")
        return None, None, None, {**account, "error_reason": error_reason}, error_reason


async def handle_document(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message: return
    user_id = user.id
    chat_id = message.chat_id
    add_known_user(user.id)

    if context.user_data.get('pending_schedule'):
        await handle_scheduled_file_upload(update, context)
        return

    process_button_text = COMMAND_BUTTONS_LAYOUT[0][0]
    if message.text == process_button_text and not message.document:
        await message.reply_text(
            convert_to_premium_emoji(
                "📁 Please send the JSON file now for manual processing.\n\n"
                "✅ <b>Supported Formats</b>\n\n"
                "🔑 <b>UID + Password:</b>\n"
                '<pre>[\n'
                '  {\n'
                '    "uid": "12345",\n'
                '    "password": "PASS123"\n'
                '  }\n'
                ']</pre>\n'
                "🔑 <b>Access Token:</b>\n"
                '<pre>[\n'
                '  {\n'
                '    "access_token": "YOUR_ACCESS_TOKEN"\n'
                '  }\n'
                ']</pre>\n'
                "🔀 <b>Mixed Format</b> (both in one file) is also supported!"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data['waiting_for_json'] = True
        return

    was_waiting_manual = context.user_data.pop('waiting_for_json', False)
    if was_waiting_manual and not message.document:
        await message.reply_text(
            convert_to_premium_emoji("⚠️ Looks like you sent text instead of a file. Please send the JSON file or use /cancel."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return
    elif not was_waiting_manual and not message.document:
        known_button_texts = {btn for row in COMMAND_BUTTONS_LAYOUT for btn in row}
        if message.text not in known_button_texts:
            logger.debug(f"Ignoring unhandled text message from user {user_id}.")
        return

    document = message.document
    if not document: return

    is_json_mime = document.mime_type and document.mime_type.lower() == 'application/json'
    has_json_extension = document.file_name and document.file_name.lower().endswith('.json')

    if not is_json_mime and not has_json_extension:
        await message.reply_text(
            convert_to_premium_emoji("❌ File does not appear to be a JSON file. Please ensure it has a `.json` extension."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    file_id = document.file_id
    file_name = document.file_name or f"file_{file_id}.json"

    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await message.reply_text(
            convert_to_premium_emoji(f"⚠️ File is too large ({document.file_size / 1024 / 1024:.2f} MB). Max: {MAX_FILE_SIZE / 1024 / 1024:.1f} MB."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    temp_file_path = os.path.join(TEMP_DIR, f'input_manual_{user_id}_{int(time.time())}.json')
    progress_message = None
    accounts_data = []

    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        progress_message = await message.reply_text(
            convert_to_premium_emoji(f"⏳ Downloading <code>{escape(file_name)}</code>..."),
            parse_mode=ParseMode.HTML
        )

        bot_file = await context.bot.get_file(file_id)
        await bot_file.download_to_drive(temp_file_path)
        logger.info(f"User {user_id} uploaded file '{file_name}' to {temp_file_path}")

        await context.bot.edit_message_text(
            chat_id=progress_message.chat_id, message_id=progress_message.message_id,
            text=convert_to_premium_emoji(f"⏳ Downloaded <code>{escape(file_name)}</code>. Parsing JSON..."),
            parse_mode=ParseMode.HTML
        )

        actual_size = os.path.getsize(temp_file_path)
        if actual_size > MAX_FILE_SIZE:
            raise ValueError(f"Downloaded file size ({actual_size / 1024 / 1024:.2f} MB) exceeds limit.")

        with open(temp_file_path, 'r', encoding='utf-8') as f:
            try:
                accounts_data = json.load(f)
            except json.JSONDecodeError as e:
                error_line_info = ""
                if hasattr(e, 'lineno') and hasattr(e, 'colno'):
                    error_line_info = f" near line {e.lineno}, column {e.colno}"
                error_msg = convert_to_premium_emoji(f"❌ Invalid JSON format in <code>{escape(file_name)}</code>{error_line_info}.\nError: <code>{escape(e.msg)}</code>.")
                await context.bot.edit_message_text(
                    chat_id=progress_message.chat_id, message_id=progress_message.message_id,
                    text=error_msg, parse_mode=ParseMode.HTML
                )
                if ADMIN_ID and ADMIN_ID != 0:
                    try:
                        await context.bot.send_message(ADMIN_ID, convert_to_premium_emoji(f"⚠️ User {user.id} uploaded invalid JSON: <code>{escape(file_name)}</code>. Error: <code>{escape(e.msg)}</code>{error_line_info}"), parse_mode=ParseMode.HTML)
                    except Exception as forward_e:
                        logger.error(f"Failed to forward invalid JSON notice: {forward_e}")
                return

        if not isinstance(accounts_data, list):
            raise ValueError("Input JSON structure is invalid. It must be an array (a list `[...]`) of objects.")
        if accounts_data and not all(isinstance(item, dict) for item in accounts_data):
            first_bad_item = next((item for item in accounts_data if not isinstance(item, dict)), None)
            raise ValueError(f"All items inside the JSON array must be objects (`{{...}}`). Found: `<code>{escape(str(first_bad_item)[:50])}</code>`...")

    except ValueError as e:
        logger.warning(f"Input validation failed for user {user_id} ('{file_name}'): {e}")
        error_text = convert_to_premium_emoji(f"❌ Validation Error: {escape(str(e))}")
        if progress_message:
            await context.bot.edit_message_text(chat_id=progress_message.chat_id, message_id=progress_message.message_id, text=error_text, parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(error_text, reply_markup=main_reply_markup, parse_mode=ParseMode.HTML)
        return
    except TelegramError as e:
        logger.error(f"Telegram error during file handling for user {user_id}: {e}")
        try:
            error_text = convert_to_premium_emoji(f"⚠️ A Telegram error occurred: <code>{escape(str(e))}</code>.")
            if progress_message:
                await context.bot.edit_message_text(chat_id=progress_message.chat_id, message_id=progress_message.message_id, text=error_text, parse_mode=ParseMode.HTML)
            else:
                await message.reply_text(error_text, reply_markup=main_reply_markup, parse_mode=ParseMode.HTML)
        except TelegramError:
            logger.error(f"Could not inform user {user_id} about Telegram error: {e}")
        return
    except Exception as e:
        logger.error(f"Error downloading/parsing file from user {user_id}: {e}", exc_info=True)
        error_text = convert_to_premium_emoji(f"⚠️ An unexpected error occurred while handling the file. Please try again.")
        if progress_message:
            try:
                await context.bot.edit_message_text(chat_id=progress_message.chat_id, message_id=progress_message.message_id, text=error_text, parse_mode=ParseMode.HTML)
            except TelegramError:
                await message.reply_text(error_text, reply_markup=main_reply_markup, parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(error_text, reply_markup=main_reply_markup, parse_mode=ParseMode.HTML)
        return
    finally:
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except OSError as e:
                logger.warning(f"Could not remove temp input file {temp_file_path}: {e}")

    total_count = len(accounts_data)
    if total_count == 0:
        await context.bot.edit_message_text(
            chat_id=progress_message.chat_id, message_id=progress_message.message_id,
            text=convert_to_premium_emoji("ℹ️ The provided JSON file is empty or contains no valid account objects."),
            parse_mode=ParseMode.HTML
        )
        return

    await context.bot.edit_message_text(
        chat_id=progress_message.chat_id, message_id=progress_message.message_id,
        text=convert_to_premium_emoji(f"🔄 <b>Processing {total_count} Accounts (Manual)</b>\nInitializing API calls (max {MAX_CONCURRENT_REQUESTS} parallel)...\n\n⚡ Output will be in same order as input file."),
        parse_mode=ParseMode.HTML
    )

    start_time = time.time()
    # count = accounts finished, success/failed = live counters shown to the user
    progress = {"count": 0, "success": 0, "failed": 0}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    manual_last_text = {"text": ""}

    async def show_manual_progress(stage: str, done: int, extra_line: str = "") -> None:
        """Edits the progress message (same live dashboard as auto-processing)."""
        text = convert_to_premium_emoji(
            build_auto_progress_text(stage, done, total_count, progress["success"], progress["failed"], start_time, extra_line)
        )
        if text == manual_last_text["text"]:
            return
        try:
            await context.bot.edit_message_text(
                chat_id=progress_message.chat_id, message_id=progress_message.message_id,
                text=text, parse_mode=ParseMode.HTML
            )
            manual_last_text["text"] = text
        except TelegramError as edit_err:
            if "Message is not modified" not in str(edit_err):
                logger.warning(f"Could not edit progress message: {edit_err}")

    async def tracked_process(account_item):
        result = await process_account(session_obj, account_item, semaphore)
        progress["count"] += 1
        if is_failed_result(result):
            progress["failed"] += 1
        else:
            progress["success"] += 1
        return result

    async def progress_updater():
        # Refreshes the message every PROGRESS_UPDATE_SECONDS (default 5s).
        while True:
            await asyncio.sleep(PROGRESS_UPDATE_SECONDS)
            await show_manual_progress("⚡ <b>Processing Accounts (Manual)...</b>", progress["count"])

    async with aiohttp.ClientSession() as session_obj:
        tasks = [tracked_process(account) for account in accounts_data]
        prog_task = asyncio.create_task(progress_updater())
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        prog_task.cancel()
        try:
            await prog_task
        except asyncio.CancelledError:
            pass

        # Final update of the first pass: always show the full 100% with the final success/failed numbers
        await show_manual_progress("🏁 <b>First pass complete (Manual)</b>", total_count)

        # --- RETRY LOGIC START ---
        final_results = list(raw_results)
        failed_indices = []
        for i, res in enumerate(raw_results):
            if isinstance(res, Exception):
                failed_indices.append(i)
            else:
                token, _, _, _, _ = res
                if not token:
                    failed_indices.append(i)

        if failed_indices:
            for retry_round in range(1, MAX_RETRY_ROUNDS + 1):
                if not failed_indices:
                    break

                retry_file_path = os.path.join(TEMP_DIR, f'retry_{user_id}_{int(time.time())}_r{retry_round}.json')
                failed_data = [accounts_data[idx] for idx in failed_indices]
                save_json_data(retry_file_path, failed_data)

                await context.bot.edit_message_text(
                    chat_id=progress_message.chat_id, message_id=progress_message.message_id,
                    text=convert_to_premium_emoji(
                        f"⏳ <b>{len(failed_indices)} accounts failed!</b>\n\nWe saved them to a retry file.\n🔄 Retry <b>{retry_round}/{MAX_RETRY_ROUNDS}</b> in <b>{RETRY_DELAY_SECONDS} seconds</b>...\n\n"
                        + build_auto_progress_text("📊 <b>Current status</b>", total_count, total_count, progress["success"], progress["failed"], start_time)
                    ),
                    parse_mode=ParseMode.HTML
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)

                retry_accounts = load_json_data(retry_file_path, [])
                retry_progress = {"count": 0}

                async def retry_tracked(acc, retry_progress=retry_progress):
                    res = await process_account(session_obj, acc, semaphore)
                    retry_progress["count"] += 1
                    # A recovered account moves from "failed" to "successful" in the live counters
                    if not is_failed_result(res):
                        progress["success"] += 1
                        progress["failed"] -= 1
                    return res

                async def retry_progress_updater(retry_accounts=retry_accounts, retry_progress=retry_progress, retry_round=retry_round):
                    while True:
                        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)
                        await show_manual_progress(
                            f"🔄 <b>Retry {retry_round}/{MAX_RETRY_ROUNDS}</b> ({retry_progress['count']}/{len(retry_accounts)} retried)",
                            total_count
                        )

                retry_tasks = [retry_tracked(acc) for acc in retry_accounts]
                retry_prog_task = asyncio.create_task(retry_progress_updater())
                retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
                retry_prog_task.cancel()
                try: await retry_prog_task
                except asyncio.CancelledError: pass

                for idx, res in zip(failed_indices, retry_results):
                    final_results[idx] = res

                if os.path.exists(retry_file_path): os.remove(retry_file_path)

                # Only the accounts that are STILL failing go to the next retry round
                _retried_total = len(failed_indices)
                failed_indices = [idx for idx in failed_indices if is_failed_result(final_results[idx])]
                await show_manual_progress(
                    f"🏁 <b>Retry {retry_round}/{MAX_RETRY_ROUNDS} complete</b> (♻️ recovered {_retried_total - len(failed_indices)}/{_retried_total})",
                    total_count
                )
        # --- RETRY LOGIC END ---

    successful_tokens = []
    working_accounts = []
    lost_accounts = []
    errors_summary = defaultdict(int)

    for i, result in enumerate(final_results):
        if isinstance(result, Exception):
            error_msg = f"Internal task error: {result}"
            logger.error(f"Error in task for account index {i}: {result}", exc_info=True)
            lost_entry = {**accounts_data[i], "error_reason": error_msg} if i < len(accounts_data) else {"error_reason": error_msg}
            lost_accounts.append(lost_entry)
            errors_summary["Internal task error"] += 1
            continue

        token, region, working_acc, lost_acc, error_reason = result
        if token and working_acc:
            successful_tokens.append({"token": token, "region": region})
            working_accounts.append(working_acc)
        elif lost_acc:
            lost_accounts.append(lost_acc)
            reason = lost_acc.get("error_reason", "Unknown Failure")
            simple_error = reason.split(':')[0].strip()
            errors_summary[simple_error] += 1
        else:
            logger.error(f"Task completed unexpectedly for account index {i}. Token:{token}, Region:{region}")
            generic_lost_info = {**accounts_data[i], "error_reason": "Processing function returned unexpected state"} if i < len(accounts_data) else {"error_reason": "unexpected state"}
            lost_accounts.append(generic_lost_info)
            errors_summary["Processing function error"] += 1

    final_elapsed_time = time.time() - start_time
    escaped_file_name = escape(file_name)
    final_summary_parts = [
        f"🏁 <b>Manual Processing Complete for <code>{escaped_file_name}</code></b>\n",
        f"📊 Total Accounts Processed: {total_count}",
        f"✅ Successful Tokens: {len(successful_tokens)}",
        f"❌ Failed/Invalid Accounts: {len(lost_accounts)}",
        f"⏱️ Total Time Taken: {format_time(final_elapsed_time)}",
        f"⏰ Finished: {escape(format_ist(datetime.now(timezone.utc)))}",
        f"✔️ Output order: matches input file order"
    ]

    successful_by_region = defaultdict(list)
    if successful_tokens:
        for token_entry in successful_tokens:
            region = token_entry.get('region')
            region_name = region if region else "Unknown Region"
            successful_by_region[region_name].append(token_entry)

        if successful_by_region:
            final_summary_parts.append(f"\n<b>Successful by Region ({len(successful_by_region)} total):</b>")
            sorted_regions = sorted(successful_by_region.keys())
            for region_number, region in enumerate(sorted_regions, 1):
                count = len(successful_by_region[region])
                final_summary_parts.append(f"{region_number}. {escape(region)}: {count} tokens")
    else:
        final_summary_parts.append("\n<b>Successful by Region:</b> 0 tokens found.")

    if errors_summary:
        final_summary_parts.append(f"\n<b>Error Summary (Top 5 of {len(errors_summary)} types):</b>")
        sorted_errors = sorted(errors_summary.items(), key=lambda item: item[1], reverse=True)
        for error_number, (msg, count) in enumerate(sorted_errors[:5], 1):
            final_summary_parts.append(f"{error_number}. <code>{escape(msg)}</code>: {count} times")
        if len(sorted_errors) > 5:
            final_summary_parts.append(f"... and {len(sorted_errors) - 5} more error types.")

    final_summary = "\n".join(final_summary_parts)
    final_summary_premium = convert_to_premium_emoji(final_summary)

    try:
        if progress_message:
            await context.bot.delete_message(chat_id=progress_message.chat_id, message_id=progress_message.message_id)
        await message.reply_text(
            final_summary_premium,
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
    except TelegramError as final_msg_err:
        logger.error(f"Could not delete progress message or send final summary: {final_msg_err}")
        try:
            await message.reply_text(
                final_summary_premium,
                parse_mode=ParseMode.HTML,
                reply_markup=main_reply_markup
            )
        except Exception as fallback_err:
            logger.critical(f"Failed even fallback sending final summary: {fallback_err}")

    file_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_files_to_send = []
    cleanup_paths = []
    jwt_token_path_for_upload = None

    try:
        os.makedirs(TEMP_DIR, exist_ok=True)

        if successful_tokens:
            jwt_token_path = os.path.join(TEMP_DIR, f'jwt_only_{user_id}_{file_timestamp}.json')
            tokens_only_list_for_file = [{"token": entry.get("token")} for entry in successful_tokens if entry.get("token")]

            if tokens_only_list_for_file:
                if save_json_data(jwt_token_path, tokens_only_list_for_file):
                    output_files_to_send.append((jwt_token_path, 'jwt_token.json'))
                    cleanup_paths.append(jwt_token_path)
                    jwt_token_path_for_upload = jwt_token_path
                else:
                    await message.reply_text(convert_to_premium_emoji("⚠️ Error saving main `jwt_token.json` to temporary storage."), parse_mode=ParseMode.HTML)
            else:
                logger.warning(f"User {user_id}: No valid tokens to save in main jwt_token.json.")

        if successful_by_region:
            logger.info(f"Creating region-specific files for user {user_id}")
            for region_name, entries in successful_by_region.items():
                if not entries: continue

                region_tokens_only = [{"token": entry.get("token")} for entry in entries if entry.get("token")]

                if region_tokens_only:
                    sanitized_region_name = sanitize_filename(region_name)
                    base_region_name = os.path.splitext(sanitized_region_name)[0]
                    region_file_name = f'accounts{base_region_name}.json'
                    region_file_path = os.path.join(TEMP_DIR, f'{base_region_name}_{user_id}_{file_timestamp}.json')

                    if save_json_data(region_file_path, region_tokens_only):
                        output_files_to_send.append((region_file_path, region_file_name))
                        cleanup_paths.append(region_file_path)
                        logger.debug(f"Created region file: {region_file_name} with {len(region_tokens_only)} tokens.")
                    else:
                        await message.reply_text(convert_to_premium_emoji(f"⚠️ Error saving region file <code>{escape(region_file_name)}</code>."), parse_mode=ParseMode.HTML)
                        logger.error(f"Failed to save region file {region_file_name} for user {user_id}")
                else:
                    logger.debug(f"Region '{region_name}' had entries but no valid tokens. Skipping.")

        if working_accounts:
            working_account_path = os.path.join(TEMP_DIR, f'working_{user_id}_{file_timestamp}.json')
            if save_json_data(working_account_path, working_accounts):
                output_files_to_send.append((working_account_path, 'working_account.json'))
                cleanup_paths.append(working_account_path)
            else:
                await message.reply_text(convert_to_premium_emoji("⚠️ Error saving `working_account.json`."), parse_mode=ParseMode.HTML)

        if lost_accounts:
            lost_account_path = os.path.join(TEMP_DIR, f'lost_{user_id}_{file_timestamp}.json')
            if save_json_data(lost_account_path, lost_accounts):
                output_files_to_send.append((lost_account_path, 'lost_account.json'))
                cleanup_paths.append(lost_account_path)
            else:
                await message.reply_text(convert_to_premium_emoji("⚠️ Error saving `lost_account.json`."), parse_mode=ParseMode.HTML)

        if output_files_to_send:
            await message.reply_text(convert_to_premium_emoji(f"⬇️ Sending {len(output_files_to_send)} result file(s)..."), parse_mode=ParseMode.HTML)
            output_files_to_send.sort(key=lambda x: x[1])
            for temp_path, desired_filename in output_files_to_send:
                if not os.path.exists(temp_path):
                    logger.error(f"Output file {temp_path} (for {desired_filename}) not found before sending.")
                    await message.reply_text(convert_to_premium_emoji(f"⚠️ Internal Error: Could not find <code>{escape(desired_filename)}</code>."), parse_mode=ParseMode.HTML)
                    continue
                try:
                    with open(temp_path, 'rb') as f:
                        await message.reply_document(
                            document=InputFile(f, filename=desired_filename),
                            caption=convert_to_premium_emoji(f"<code>{escape(desired_filename)}</code>\nFrom: <code>{escaped_file_name}</code>\nTotal: {total_count} | ✅ {len(successful_tokens)} | ❌ {len(lost_accounts)}"),
                            parse_mode=ParseMode.HTML
                        )
                    logger.info(f"Sent '{desired_filename}' to user {user_id} (manual process)")
                    await asyncio.sleep(0.5)
                except TelegramError as send_err:
                    logger.error(f"Failed to send '{desired_filename}' to user {user_id}: {send_err}")
                    await message.reply_text(convert_to_premium_emoji(f"⚠️ Failed to send <code>{escape(desired_filename)}</code>: {escape(str(send_err))}"), parse_mode=ParseMode.HTML)
                except Exception as general_err:
                    logger.error(f"Unexpected error sending '{desired_filename}' to {user_id}: {general_err}", exc_info=True)
                    await message.reply_text(convert_to_premium_emoji(f"⚠️ Unexpected error sending <code>{escape(desired_filename)}</code>."), parse_mode=ParseMode.HTML)
        elif total_count > 0:
            await message.reply_text(convert_to_premium_emoji("ℹ️ No output files were generated (0 successful tokens or error saving files)."), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)

        if is_user_vip(user_id) and jwt_token_path_for_upload:
            user_id_str = str(user_id)
            user_gh_configs = get_user_github_configs(user_id_str)

            if user_gh_configs:
                logger.info(f"User {user_id} is VIP with {len(user_gh_configs)} GitHub config(s). Auto-upload.")
                if os.path.exists(jwt_token_path_for_upload):
                    for repo_key, config in user_gh_configs.items():
                        if config and isinstance(config, dict):
                            await upload_to_github_background(
                                context.bot,
                                user_id,
                                jwt_token_path_for_upload,
                                config
                            )
                        else:
                            logger.error(f"GitHub config for repo '{repo_key}' (user {user_id}) invalid. Skipping.")
                else:
                    logger.error(f"JWT file {jwt_token_path_for_upload} missing for GitHub upload (user {user_id}).")
                    await message.reply_text(convert_to_premium_emoji("⚠️ Internal Error: Token file for GitHub upload not found."), parse_mode=ParseMode.HTML, disable_notification=True)
            else:
                logger.info(f"User {user_id} is VIP but no GitHub configs.")
                await message.reply_text(
                    convert_to_premium_emoji("ℹ️ GitHub auto-upload skipped: No GitHub configuration found. Use `/setgithub`."),
                    disable_notification=True, parse_mode=ParseMode.HTML
                )
        elif is_user_vip(user_id) and not jwt_token_path_for_upload and successful_tokens:
            await message.reply_text(convert_to_premium_emoji("⚠️ GitHub upload skipped: Error saving main token file locally."), parse_mode=ParseMode.HTML, disable_notification=True)
        elif is_user_vip(user_id) and not successful_tokens and total_count > 0:
            await message.reply_text(convert_to_premium_emoji("ℹ️ GitHub auto-upload skipped: No successful tokens in this batch."), parse_mode=ParseMode.HTML, disable_notification=True)

    except Exception as final_err:
        logger.error(f"Error during file generation/sending stage for user {user_id}: {final_err}", exc_info=True)
        await message.reply_text(convert_to_premium_emoji(f"⚠️ An error occurred while generating/sending result files: {escape(str(final_err))}"), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)
    finally:
        for path in cleanup_paths:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as e:
                    logger.warning(f"Could not remove temp output file {path}: {e}")

        if ADMIN_ID and ADMIN_ID != 0:
            try:
                temp_forward_path = os.path.join(TEMP_DIR, f'forward_{user_id}_{message.message_id}.json')
                try:
                    bot_file = await context.bot.get_file(file_id)
                    await bot_file.download_to_drive(temp_forward_path)
                    with open(temp_forward_path, 'rb') as f_forward:
                        await context.bot.send_document(
                            chat_id=ADMIN_ID,
                            document=InputFile(f_forward, filename=file_name),
                            caption=convert_to_premium_emoji(f"Manually processed input file from user: <code>{user_id}</code> (<code>{escape(user.first_name or '')}</code> @{escape(user.username or 'NoUsername')})\nFilename: <code>{escape(file_name)}</code>"),
                            parse_mode=ParseMode.HTML
                        )
                    logger.info(f"Forwarded original input file '{file_name}' from user {user_id} to admin {ADMIN_ID}")
                except Exception as download_err:
                    logger.error(f"Could not re-download file for forwarding to admin: {download_err}")
                    await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=message.chat_id, message_id=message.message_id)
                    await context.bot.send_message(ADMIN_ID, f"(Forwarded original message as file re-download failed for admin log)")
                finally:
                    if os.path.exists(temp_forward_path):
                        try: os.remove(temp_forward_path)
                        except OSError: pass

            except Forbidden:
                logger.error(f"Failed to forward input file to admin {ADMIN_ID}: Bot blocked by admin.")
            except TelegramError as e:
                logger.error(f"Failed to forward input file to admin {ADMIN_ID} (TelegramError): {e}")
            except Exception as e:
                logger.error(f"Unexpected error forwarding input file to admin {ADMIN_ID}: {e}", exc_info=True)
        else:
            logger.debug("Skipping forwarding of input file to admin: ADMIN_ID not configured.")

# --- GitHub Auto-Upload Logic ---

async def upload_to_github_background(bot, user_id: int, local_token_file_path: str, config: dict) -> bool:
    notify_chat_id = user_id
    upload_start_time = time.time()
    logger.info(f"Starting GitHub background upload for user {user_id}...")
    status_msg_obj = None
    upload_success = False

    try:
        status_msg_obj = await bot.send_message(notify_chat_id, convert_to_premium_emoji("⚙️ GitHub Upload: Initializing..."), parse_mode=ParseMode.HTML)
    except Forbidden:
        logger.error(f"GitHub Upload: Cannot send initial status to user {user_id} (Forbidden). Aborting.")
        return False
    except TelegramError as e:
        logger.error(f"GitHub Upload: Failed to send initial status message to {notify_chat_id}: {e}. Aborting.")
        return False

    try:
        github_token = config.get('github_token')
        repo_full_name = config.get('github_repo')
        branch = config.get('github_branch')
        target_filename = config.get('github_filename')

        validation_errors = []
        if not github_token: validation_errors.append("Missing GitHub Token")
        if not repo_full_name: validation_errors.append("Missing Repository Name")
        elif '/' not in repo_full_name or len(repo_full_name.split('/')) != 2 or not all(p.strip() for p in repo_full_name.split('/')):
            validation_errors.append("Invalid Repository format (must be `owner/repo`)")
        if not branch: validation_errors.append("Missing Branch Name")
        elif ' ' in branch or branch.startswith('/') or branch.endswith('/'):
            validation_errors.append("Invalid Branch name (no spaces/slashes at ends)")
        if not target_filename: validation_errors.append("Missing Target Filename")
        elif not target_filename.lower().endswith('.json'):
            validation_errors.append("Filename must end with `.json`")
        elif target_filename.startswith('/') or ' ' in target_filename:
            validation_errors.append("Invalid Filename (no spaces or leading slash)")

        if validation_errors:
            error_str = ", ".join(validation_errors)
            logger.warning(f"Invalid GitHub config for user {user_id}. Errors: {error_str}.")
            await bot.edit_message_text(
                chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                text=convert_to_premium_emoji(f"⚠️ GitHub upload skipped: Configuration invalid.\nErrors: {escape(error_str)}\nPlease use <code>/setgithub</code> again."),
                parse_mode=ParseMode.HTML
            )
            return False

        try:
            with open(local_token_file_path, 'rb') as f:
                content_bytes = f.read()
            if not content_bytes:
                logger.info(f"Local token file {local_token_file_path} empty. Skipping.")
                await bot.edit_message_text(
                    chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                    text=convert_to_premium_emoji("ℹ️ GitHub upload skipped: The generated token file was empty."),
                    parse_mode=ParseMode.HTML
                )
                return True
            content_b64 = base64.b64encode(content_bytes).decode('utf-8')
        except FileNotFoundError:
            logger.error(f"Local token file {local_token_file_path} not found.")
            await bot.edit_message_text(
                chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                text=convert_to_premium_emoji("⚠️ GitHub upload failed: Couldn't find the generated token file internally."),
                parse_mode=ParseMode.HTML
            )
            return False
        except Exception as e:
            logger.error(f"Error reading/encoding local token file: {e}", exc_info=True)
            await bot.edit_message_text(
                chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                text=convert_to_premium_emoji(f"⚠️ GitHub upload failed: Error reading local token file: {escape(str(e))}"),
                parse_mode=ParseMode.HTML
            )
            return False

        api_url_base = "https://api.github.com"
        clean_repo_name = repo_full_name.strip()
        clean_filename = target_filename.strip()
        contents_url = f"{api_url_base}/repos/{clean_repo_name}/contents/{clean_filename}"
        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        sha = None

        async with aiohttp.ClientSession(headers=headers) as session:
            clean_branch = branch.strip()
            status_text = convert_to_premium_emoji(f"⚙️ GitHub Upload: Checking <code>{escape(clean_filename)}</code> in <code>{escape(clean_repo_name)}</code>...")
            await bot.edit_message_text(
                chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                text=status_text, parse_mode=ParseMode.HTML
            )

            try:
                get_url = f"{contents_url}?ref={clean_branch}"
                async with session.get(get_url, timeout=20) as response:
                    response_text = await response.text()
                    if response.status == 200:
                        try:
                            sha = json.loads(response_text).get('sha')
                            if sha: logger.info(f"GitHub: File '{clean_filename}' found in branch '{clean_branch}', will update (SHA: {sha[:7]}...).")
                            else: logger.warning(f"GitHub: File '{clean_filename}' found but SHA missing?")
                        except json.JSONDecodeError:
                            logger.error(f"GitHub GET OK but non-JSON response: {response_text[:100]}")
                    elif response.status == 404:
                        logger.info(f"GitHub: File '{clean_filename}' not found in branch '{clean_branch}'. Will create.")
                        sha = None
                    elif response.status == 401:
                        raise ConnectionRefusedError("GitHub Auth Error (401). Check token validity/permissions.")
                    elif response.status == 403:
                        try: error_msg = json.loads(response_text).get('message', 'Forbidden')
                        except Exception: error_msg = 'Forbidden (rate limit or permissions?)'
                        raise PermissionError(f"GitHub Access Error (403): {error_msg}")
                    else:
                        logger.warning(f"Unexpected status {response.status} checking GitHub file '{clean_filename}'.")

            except (asyncio.TimeoutError, aiohttp.ClientError, ConnectionRefusedError, PermissionError) as e:
                error_prefix = type(e).__name__
                logger.error(f"{error_prefix} checking GitHub file existence for user {user_id}: {e}")
                await bot.edit_message_text(
                    chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                    text=convert_to_premium_emoji(f"⚠️ GitHub upload failed: {error_prefix} checking repository: <code>{escape(str(e))}</code>"),
                    parse_mode=ParseMode.HTML
                )
                return False
            except Exception as e:
                logger.error(f"Unexpected error checking GitHub file existence: {e}", exc_info=True)
                await bot.edit_message_text(
                    chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                    text=convert_to_premium_emoji(f"⚠️ GitHub upload failed: Unexpected error checking repo status: {escape(str(e))}"),
                    parse_mode=ParseMode.HTML
                )
                return False

            action_verb = "Updating" if sha else "Creating"
            status_text = convert_to_premium_emoji(f"⚙️ GitHub Upload: {action_verb} <code>{escape(clean_filename)}</code> in branch <code>{escape(clean_branch)}</code>...")
            await bot.edit_message_text(
                chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                text=status_text, parse_mode=ParseMode.HTML
            )

            commit_message = f"Auto-{action_verb.lower()} {clean_filename} via bot ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')})"
            payload = {
                "message": commit_message,
                "content": content_b64,
                "branch": clean_branch
            }
            if sha:
                payload["sha"] = sha

            try:
                async with session.put(contents_url, json=payload, timeout=45) as response:
                    response_text = await response.text()
                    response_data = None
                    try: response_data = json.loads(response_text)
                    except json.JSONDecodeError: logger.warning(f"GitHub PUT non-JSON response ({response.status}): {response_text[:100]}")

                    upload_duration = time.time() - upload_start_time

                    if response.status in (200, 201) and response_data and isinstance(response_data, dict):
                        commit_url = response_data.get('commit', {}).get('html_url', '')
                        file_url = response_data.get('content', {}).get('html_url', '')
                        action_done = "updated" if response.status == 200 else "created"

                        success_msg_parts = [
                            f"✅ Tokens successfully {action_done} on GitHub! ({format_time(upload_duration)})\n",
                            f"Repo: <code>{escape(clean_repo_name)}</code>",
                            f"File: <code>{escape(clean_filename)}</code>",
                            f"Branch: <code>{escape(clean_branch)}</code>"
                        ]
                        links = []
                        if file_url and isinstance(file_url, str) and file_url.startswith("http"):
                            links.append(f"<a href='{file_url}'>View File</a>")
                        if commit_url and isinstance(commit_url, str) and commit_url.startswith("http"):
                            links.append(f"<a href='{commit_url}'>View Commit</a>")
                        if links: success_msg_parts.append(" | ".join(links))

                        await bot.edit_message_text(
                            chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                            text=convert_to_premium_emoji("\n".join(success_msg_parts)), parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True
                        )
                        logger.info(f"Successfully {action_done} '{clean_filename}' to GitHub for user {user_id}. Duration: {upload_duration:.2f}s")
                        upload_success = True

                        current_github_configs = load_github_configs()
                        user_id_str = str(user_id)
                        user_multi_configs = get_user_github_configs(user_id_str)
                        repo_key = clean_repo_name
                        _user_cfgs_for_update = current_github_configs.get(user_id_str, {})
                        if isinstance(_user_cfgs_for_update, dict):
                            _matches = find_github_config_keys(_user_cfgs_for_update, clean_repo_name, clean_filename) or find_github_config_keys(_user_cfgs_for_update, clean_repo_name)
                            if _matches:
                                repo_key = _matches[0]
                        if user_id_str in current_github_configs and repo_key in current_github_configs.get(user_id_str, {}):
                            current_github_configs[user_id_str][repo_key]['last_upload'] = datetime.now(timezone.utc).isoformat()
                            if not save_github_configs(current_github_configs):
                                logger.error(f"Failed to save updated 'last_upload' timestamp for user {user_id_str} repo {repo_key}.")
                        else:
                            logger.warning(f"Could not find config for user {user_id_str} repo {repo_key} when trying to update 'last_upload'.")

                    else:
                        error_msg_detail = f'Status {response.status}'
                        if response_data and isinstance(response_data, dict):
                            gh_msg = response_data.get('message', error_msg_detail)
                            doc_url = response_data.get('documentation_url')
                            error_msg_detail = f"{gh_msg}" + (f" (Docs: {doc_url})" if doc_url else "")
                        elif response_text:
                            error_msg_detail = response_text[:150]

                        final_error_message = convert_to_premium_emoji(f"⚠️ GitHub upload failed for <code>{escape(clean_repo_name)}</code>.\nStatus: {response.status}\nError: <code>{escape(error_msg_detail)}</code>")
                        logger.error(f"Failed GitHub upload for user {user_id}. Status: {response.status}. Error: {error_msg_detail}")
                        await bot.edit_message_text(
                            chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                            text=final_error_message, parse_mode=ParseMode.HTML
                        )
                        upload_success = False

            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                error_prefix = type(e).__name__
                logger.error(f"{error_prefix} during GitHub PUT for user {user_id}: {e}")
                await bot.edit_message_text(
                    chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                    text=convert_to_premium_emoji(f"⚠️ GitHub upload failed: {error_prefix} during upload: {escape(str(e))}"),
                    parse_mode=ParseMode.HTML
                )
                upload_success = False
            except Exception as e:
                logger.error(f"Unexpected error during GitHub PUT for user {user_id}: {e}", exc_info=True)
                await bot.edit_message_text(
                    chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                    text=convert_to_premium_emoji(f"⚠️ Unexpected error during GitHub upload: {escape(str(e))}"),
                    parse_mode=ParseMode.HTML
                )
                upload_success = False

    except Exception as e:
        logger.error(f"General GitHub background upload error for user {user_id}: {e}", exc_info=True)
        if status_msg_obj:
            try:
                await bot.edit_message_text(
                    chat_id=notify_chat_id, message_id=status_msg_obj.message_id,
                    text=convert_to_premium_emoji(f"⚠️ GitHub upload failed: An internal bot error occurred: {escape(str(e))}"),
                    parse_mode=ParseMode.HTML
                )
            except TelegramError:
                logger.error(f"Could not edit final error status for GitHub upload user {user_id}.")
                try:
                    await bot.send_message(notify_chat_id, convert_to_premium_emoji(f"⚠️ GitHub upload failed due to an internal error: {escape(str(e))}"), parse_mode=ParseMode.HTML)
                except Exception:
                    logger.critical(f"Failed even to send a final error message for GitHub upload user {user_id}.")
        else:
            logger.critical(f"Cannot update GitHub status_msg as it failed initially. General error: {e}")
            try:
                await bot.send_message(notify_chat_id, convert_to_premium_emoji(f"⚠️ GitHub upload failed due to an internal error: {escape(str(e))}"), parse_mode=ParseMode.HTML)
            except Exception:
                logger.error("Failed even to send a final error message for GitHub upload after initial status failure.")
        upload_success = False

    return upload_success

# --- Direct GitHub Configuration Command ---

async def set_github_direct(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message: return
    user_id = user.id
    add_known_user(user.id)
    context.user_data.pop('pending_schedule', None)
    context.user_data.pop('waiting_for_json', None)

    if not is_user_vip(user.id):
        await message.reply_text(
            convert_to_premium_emoji("❌ GitHub configuration is only available for VIP users. Use /vipshop to upgrade."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    args = context.args
    usage_text = convert_to_premium_emoji(
        "⚙️ <b>GitHub Configuration Usage:</b>\n\n"
        "Provide all details in <b>one</b> command:\n"
        "<code>/setgithub &lt;TOKEN&gt; &lt;owner/repo&gt; &lt;branch&gt; &lt;filename.json&gt;</code>\n\n"
        "<b>Example:</b>\n"
        "<code>/setgithub ghp_YourToken YourUser/MyRepo main jwt_token.json</code>\n\n"
        "✅ You can run this command multiple times to add configs for different repos!\n"
        "Each <code>owner/repo</code> + filename is a separate config. The same repo with different files (e.g. <code>ind_token.json</code> and <code>bd_token.json</code>) is supported.\n\n"
        "⚠️ <b>Security:</b> Your token will be visible in the command. The bot will attempt to delete it."
    )

    if len(args) != 4:
        await message.reply_text(
            convert_to_premium_emoji(f"❌ Incorrect number of arguments. Expected 4, got {len(args)}.\n\n{usage_text}"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=main_reply_markup
        )
        return

    github_token, github_repo_raw, github_branch_raw, github_filename_raw = args
    user_id_str = str(user.id)

    validation_errors = []
    if not github_token or len(github_token) < 10:
        validation_errors.append("GitHub Token seems missing or too short.")

    github_repo = github_repo_raw.strip()
    if not github_repo or '/' not in github_repo or len(github_repo.split('/')) != 2 or not all(p.strip() for p in github_repo.split('/')) or github_repo.startswith('/') or github_repo.endswith('/') or ' ' in github_repo:
        validation_errors.append("Invalid Repository format. Use `owner/repository_name`.")

    github_branch = github_branch_raw.strip()
    if not github_branch or ' ' in github_branch or github_branch.startswith('/') or github_branch.endswith('/'):
        validation_errors.append("Invalid Branch name (no spaces or leading/trailing slashes).")

    github_filename = github_filename_raw.strip()
    if not github_filename or not github_filename.lower().endswith('.json') or github_filename.startswith('/') or ' ' in github_filename:
        validation_errors.append("Invalid Filename. Must end with `.json`, contain no spaces, and not start with `/`.")

    if validation_errors:
        safe_errors = [escape(e) for e in validation_errors]
        error_message = "❌ Configuration validation failed:\n" + "\n".join(f"- {e}" for e in safe_errors)
        error_message += f"\n\n{usage_text}"
        await message.reply_text(
            convert_to_premium_emoji(error_message),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=main_reply_markup
        )
        return

    config_data = {
        'github_token': github_token,
        'github_repo': github_repo,
        'github_branch': github_branch,
        'github_filename': github_filename,
        'last_upload': None,
        'config_set_on': datetime.now(timezone.utc).isoformat()
    }
    logger.info(f"Valid GitHub config via /setgithub from VIP user {user_id_str} for repo '{github_repo}'.")

    github_configs = load_github_configs()
    _ = get_user_github_configs(user_id_str)  # trigger migration if needed
    github_configs = load_github_configs()

    if user_id_str not in github_configs or not isinstance(github_configs.get(user_id_str), dict):
        github_configs[user_id_str] = {}

    config_key, is_update = make_github_config_key(github_configs.get(user_id_str, {}), github_repo, github_filename)
    github_configs[user_id_str][config_key] = config_data

    if save_github_configs(github_configs):
        logger.info(f"Saved GitHub config for user {user_id_str}, repo '{github_repo}'.")

        masked_token = "****"
        if len(github_token) > 8:
            masked_token = github_token[:4] + "****" + github_token[-4:]

        safe_repo = escape(config_data['github_repo'])
        safe_branch = escape(config_data['github_branch'])
        safe_filename = escape(config_data['github_filename'])
        safe_masked_token = escape(masked_token)
        action_word = "Updated" if is_update else "Added"

        total_configs = len(github_configs.get(user_id_str, {}))

        confirmation_message = convert_to_premium_emoji(
            f"✅ <b>GitHub Configuration {action_word} Successfully!</b>\n\n"
            f"• Repo: <code>{safe_repo}</code>\n"
            f"• Branch: <code>{safe_branch}</code>\n"
            f"• Filename: <code>{safe_filename}</code>\n"
            f"• Token: <code>{safe_masked_token}</code> (Masked)\n\n"
            f"📊 <b>Total GitHub Configs:</b> <code>{total_configs}</code>\n"
            "Auto-upload to all configured repos is enabled.\n\n"
            "⏳ <b>Attempting to delete your command message for security...</b>"
        )
        confirm_msg_obj = await message.reply_text(
            confirmation_message,
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )

        try:
            await message.delete()
            logger.info(f"Deleted user's /setgithub command message for user {user_id_str}")
            await context.bot.edit_message_text(
                chat_id=confirm_msg_obj.chat_id, message_id=confirm_msg_obj.message_id,
                text=confirmation_message.replace("⏳ <b>Attempting to delete your command message for security...</b>",
                                                   "✅ <b>Your command message containing the token has been deleted.</b>"),
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            logger.warning(f"Could not delete user's /setgithub command message for {user_id_str}: {e}")
            try:
                await context.bot.edit_message_text(
                    chat_id=confirm_msg_obj.chat_id, message_id=confirm_msg_obj.message_id,
                    text=confirmation_message.replace("⏳ <b>Attempting to delete your command message for security...</b>",
                                                       "⚠️ <b>Could not automatically delete your command message! Please delete it manually NOW.</b>"),
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError as edit_err:
                logger.error(f"Failed to edit confirmation message to warn about manual deletion: {edit_err}")
                await message.reply_text(convert_to_premium_emoji("⚠️ <b>IMPORTANT: Could not automatically delete your command message! Please delete it manually NOW.</b>"), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)

    else:
        logger.error(f"Failed to save GitHub configuration file for user {user_id_str}")
        await message.reply_text(
            convert_to_premium_emoji("❌ <b>Error:</b> Could not save the GitHub configuration due to a file system error. Please try again."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )


async def my_github_config(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message: return
    user_id = user.id
    add_known_user(user.id)
    context.user_data.pop('pending_schedule', None)
    context.user_data.pop('waiting_for_json', None)

    if not is_user_vip(user.id):
        await message.reply_text(
            convert_to_premium_emoji("ℹ️ GitHub auto-upload configuration is a VIP feature. Use /vipshop to upgrade."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    user_id_str = str(user.id)
    user_gh_configs = get_user_github_configs(user_id_str)

    if not user_gh_configs:
        await message.reply_text(
            convert_to_premium_emoji(
                "ℹ️ You have no GitHub configurations set up yet.\n\n"
                "Use <code>/setgithub &lt;TOKEN&gt; &lt;owner/repo&gt; &lt;branch&gt; &lt;filename.json&gt;</code> to add one.\n"
                "You can add multiple configs (one per repo)!"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    message_parts = [f"🔧 <b>Your GitHub Auto-Upload Configs ({len(user_gh_configs)} total):</b>\n"]

    for idx, (repo_key, config) in enumerate(user_gh_configs.items(), 1):
        if not isinstance(config, dict):
            message_parts.append(f"\n❌ Config #{idx} — <b>Invalid/Corrupted data</b>")
            continue

        token = config.get('github_token', 'Not Set')
        masked_token = "Not Set"
        if isinstance(token, str) and token != 'Not Set':
            masked_token = token[:4] + "****" + token[-4:] if len(token) > 8 else "****"

        safe_repo = escape(config.get('github_repo', 'Not Set'))
        safe_branch = escape(config.get('github_branch', 'Not Set'))
        safe_filename = escape(config.get('github_filename', 'Not Set'))
        safe_masked_token = escape(masked_token)

        message_parts.append(f"\n📋 <b>Config #{idx}</b>")
        message_parts.append(f"• Repo: <code>{safe_repo}</code>")
        message_parts.append(f"• Branch: <code>{safe_branch}</code>")
        message_parts.append(f"• Filename: <code>{safe_filename}</code>")
        message_parts.append(f"• Token: <code>{safe_masked_token}</code> (Masked)")

        last_upload_iso = config.get('last_upload')
        if last_upload_iso:
            try:
                last_upload_dt = datetime.fromisoformat(last_upload_iso.replace('Z', '+00:00'))
                last_upload_str = format_ist(last_upload_dt)
                message_parts.append(f"• Last Upload: <code>{last_upload_str}</code>")
            except (ValueError, TypeError):
                message_parts.append(f"• Last Upload: <code>Invalid Date</code>")
        else:
            message_parts.append("• Last Upload: <code>Never</code>")

        config_set_on_iso = config.get('config_set_on')
        if config_set_on_iso:
            try:
                config_set_dt = datetime.fromisoformat(config_set_on_iso.replace('Z', '+00:00'))
                config_set_str = format_ist(config_set_dt)
                message_parts.append(f"• Config Set On: <code>{config_set_str}</code>")
            except (ValueError, TypeError):
                pass

    message_parts.append(f"\n💡 Use <code>/setgithub &lt;TOKEN&gt; ...</code> to add more configs.")
    message_parts.append(f"Use <code>/removegithub &lt;owner/repo&gt; [filename.json]</code> to remove a specific config.")

    final_msg = "\n".join(message_parts)
    if len(final_msg) > 4096:
        split_point = final_msg.rfind('\n', 0, 4080)
        final_msg = final_msg[:split_point or 4080] + "\n\n...(truncated)"

    await message.reply_text(
        convert_to_premium_emoji(final_msg),
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_markup,
        disable_web_page_preview=True
    )


async def remove_github_config(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message: return
    user_id = user.id
    add_known_user(user.id)
    context.user_data.pop('pending_schedule', None)
    context.user_data.pop('waiting_for_json', None)

    if not is_user_vip(user.id):
        await message.reply_text(
            convert_to_premium_emoji("❌ GitHub configuration management is a VIP feature."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    args = context.args
    usage_text = convert_to_premium_emoji(
        "Usage: <code>/removegithub &lt;owner/repo&gt; [filename.json]</code>\n"
        "(e.g., <code>/removegithub MyUser/MyRepo</code> or <code>/removegithub MyUser/MyRepo bd_token.json</code>)\n"
        "Add the filename when the same repo has more than one config."
    )

    if len(args) not in (1, 2):
        await message.reply_text(
            convert_to_premium_emoji(f"❌ Incorrect number of arguments.\n\n{usage_text}"),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    repo_to_remove = args[0].strip()
    filename_to_remove = args[1].strip() if len(args) == 2 else None
    user_id_str = str(user.id)

    user_gh_configs = get_user_github_configs(user_id_str)

    matching_keys = find_github_config_keys(user_gh_configs, repo_to_remove, filename_to_remove)
    if not matching_keys and filename_to_remove is None and repo_to_remove in user_gh_configs:
        matching_keys = [repo_to_remove]  # exact stored key such as owner/repo#file.json

    if not matching_keys:
        await message.reply_text(
            convert_to_premium_emoji(f"ℹ️ No GitHub config found for repo <code>{escape(repo_to_remove)}</code>"
            f"{' / <code>' + escape(filename_to_remove) + '</code>' if filename_to_remove else ''}.\n\n"
            f"Use <code>/mygithub</code> to see your configured repos."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    if len(matching_keys) > 1:
        options_text = "\n".join(
            f"{n}. <code>/removegithub {escape(repo_to_remove)} {escape(str(user_gh_configs[k].get('github_filename', '')))}</code>"
            for n, k in enumerate(matching_keys, 1)
        )
        await message.reply_text(
            convert_to_premium_emoji(f"ℹ️ <code>{escape(repo_to_remove)}</code> has more than one config. Tell me which file to remove:\n{options_text}"),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    key_to_remove = matching_keys[0]
    removed_cfg = user_gh_configs.get(key_to_remove, {})
    removed_filename = removed_cfg.get('github_filename') if isinstance(removed_cfg, dict) else None
    display_removed = f"{get_config_repo_name(key_to_remove, removed_cfg)}" + (f" ({removed_filename})" if removed_filename else "")

    github_configs = load_github_configs()
    del github_configs[user_id_str][key_to_remove]

    if not github_configs.get(user_id_str):
        del github_configs[user_id_str]

    if save_github_configs(github_configs):
        remaining = len(github_configs.get(user_id_str, {}))
        await message.reply_text(
            convert_to_premium_emoji(f"✅ GitHub config for <code>{escape(display_removed)}</code> removed successfully.\n"
            f"Remaining configs: <code>{remaining}</code>"),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        logger.info(f"User {user_id} removed GitHub config '{key_to_remove}'.")
    else:
        await message.reply_text(
            convert_to_premium_emoji(f"❌ Error saving config after removing <code>{escape(display_removed)}</code>. Please try again."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )

# --- Scheduled File Commands ---

async def set_scheduled_file_start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message: return
    user_id = user.id
    add_known_user(user.id)
    context.user_data.pop('waiting_for_json', None)

    if not is_user_vip(user.id):
        await message.reply_text(
            convert_to_premium_emoji("❌ File scheduling is a VIP feature. Use /vipshop to upgrade."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    args = context.args
    usage_text = (
        "⚙️ <b>Schedule File for Auto-Processing</b>\n\n"
        "<b>Usage:</b> <code>/setfile &lt;Interval or Time&gt; &lt;ScheduleName.json&gt; [owner/repo] [TokenFile.json]</code>\n\n"
        "<b>Interval:</b> <code>30m</code>, <code>6h</code>, <code>1d</code> - repeats every interval (min 5m).\n"
        f"<b>Time:</b> <code>02:30</code> or <code>2:30am</code> - runs <b>every day</b> at that time ({escape(DEFAULT_SCHEDULE_TZ)}).\n"
        "<b>ScheduleName:</b> A name for this schedule, ending in <code>.json</code>.\n"
        "<b>owner/repo:</b> (optional) upload only to this GitHub config. Without it, the tokens go to all your configs.\n"
        "<b>TokenFile.json:</b> (optional) file name to update on GitHub. If left out: <code>ind_ac.json</code> uses <code>ind_token.json</code>, "
        "<code>bd_ac.json</code> uses <code>bd_token.json</code>, any other name uses the filename saved in the GitHub config.\n\n"
        "<b>Examples:</b>\n"
        "<code>/setfile 02:00 ind_ac.json MyUser/Repo1</code>\n"
        "<code>/setfile 02:30 bd_ac.json MyUser/Repo1</code>\n"
        "<code>/setfile 02:10 ind_ac.json MyUser/Repo2</code>\n"
        "<code>/setfile 12h my_main_accounts.json</code>\n\n"
        "After using the command, send the corresponding JSON file."
    )

    async def reject(text: str) -> None:
        await message.reply_text(
            convert_to_premium_emoji(f"{text}\n\n{usage_text}"),
            parse_mode=ParseMode.HTML, reply_markup=main_reply_markup,
            disable_web_page_preview=True
        )

    if len(args) < 2 or len(args) > 4:
        await reject(f"❌ Incorrect number of arguments. Expected 2 to 4, got {len(args)}.")
        return

    when_str, user_filename = args[0], args[1]
    extra_args = args[2:]

    daily_time = parse_daily_time(when_str)
    interval_seconds = None
    if daily_time is None:
        interval_seconds = parse_interval(when_str)
        min_interval_seconds = 5 * 60
        if interval_seconds is None:
            await reject(f"❌ Invalid interval or time: <code>{escape(when_str)}</code>. Use <code>30m</code>, <code>6h</code>, <code>1d</code> or a time like <code>02:30</code>.")
            return
        if interval_seconds < min_interval_seconds:
            await reject(f"❌ Interval is too short. Minimum interval is {format_time(min_interval_seconds)} (<code>{min_interval_seconds // 60}m</code>).")
            return

    if not user_filename.lower().endswith('.json'):
        await reject(f"❌ Schedule name must end with <code>.json</code>. You provided: <code>{escape(user_filename)}</code>.")
        return

    sanitized_name = sanitize_filename(user_filename)
    if not sanitized_name or sanitized_name == '.json':
        await message.reply_text(
            convert_to_premium_emoji(f"❌ Invalid schedule name after sanitization: <code>{escape(user_filename)}</code> became <code>{escape(sanitized_name)}</code>.\nChoose a more descriptive name."),
            parse_mode=ParseMode.HTML, reply_markup=main_reply_markup
        )
        return

    github_repo_arg = None
    target_filename_arg = None
    for extra in extra_args:
        extra = extra.strip()
        if extra.lower().endswith('.json'):
            if target_filename_arg:
                await reject("❌ Only one token file name can be given.")
                return
            target_filename_arg = extra
        elif '/' in extra:
            if github_repo_arg:
                await reject("❌ Only one <code>owner/repo</code> can be given.")
                return
            github_repo_arg = extra
        else:
            await reject(f"❌ Could not understand <code>{escape(extra)}</code>. Use <code>owner/repo</code> or a file name ending in <code>.json</code>.")
            return

    bound_repo = None
    if github_repo_arg:
        user_gh_configs = get_user_github_configs(str(user_id))
        bound_repo = next(
            (get_config_repo_name(repo_key, cfg) for repo_key, cfg in user_gh_configs.items()
             if get_config_repo_name(repo_key, cfg).lower() == github_repo_arg.lower() or repo_key.lower() == github_repo_arg.lower()),
            None
        )
        if not bound_repo:
            await message.reply_text(
                convert_to_premium_emoji(
                    f"❌ No GitHub config found for <code>{escape(github_repo_arg)}</code>.\n"
                    "Add it first with <code>/setgithub</code> (see <code>/mygithub</code> for your configs), then run <code>/setfile</code> again."
                ),
                parse_mode=ParseMode.HTML, reply_markup=main_reply_markup
            )
            return

    if target_filename_arg:
        if target_filename_arg.startswith('/') or ' ' in target_filename_arg:
            await reject(f"❌ Invalid token file name <code>{escape(target_filename_arg)}</code> (no spaces, no leading slash).")
            return
        target_filename = target_filename_arg
    else:
        target_filename = derive_token_filename(sanitized_name)  # None -> use the filename saved in the GitHub config

    schedule_key = make_schedule_key(sanitized_name, bound_repo)
    daily_time_str = f"{daily_time[0]:02d}:{daily_time[1]:02d}" if daily_time else None

    context.user_data['pending_schedule'] = {
        'interval_seconds': 86400 if daily_time else interval_seconds,
        'daily_time': daily_time_str,
        'schedule_tz': DEFAULT_SCHEDULE_TZ if daily_time else None,
        'schedule_name': sanitized_name,
        'schedule_key': schedule_key,
        'user_filename': user_filename,
        'github_repo': bound_repo,
        'github_target_filename': target_filename
    }

    if daily_time:
        timing_text = f"Daily at {daily_time_str} ({escape(DEFAULT_SCHEDULE_TZ)})"
    else:
        timing_text = f"Interval: {escape(when_str)} = {format_time(interval_seconds)}"
    upload_text = (
        f"GitHub: <code>{escape(bound_repo) if bound_repo else 'all your configs'}</code> / "
        f"<code>{escape(target_filename) if target_filename else 'filename saved in the config'}</code>"
    )

    logger.info(f"User {user_id} initiated scheduling for '{schedule_key}' ({'daily ' + daily_time_str if daily_time else str(interval_seconds) + 's'}).")
    await message.reply_text(
        convert_to_premium_emoji(
            f"✅ Okay, schedule details accepted for <code>'{escape(user_filename)}'</code> "
            f"({timing_text}).\n"
            f"📤 {upload_text}\n\n"
            f"📎 <b>Now, please send the JSON file</b> you want to associate with this schedule.\n\n"
            f"Use /cancel to abort."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
        disable_web_page_preview=True
    )


async def handle_scheduled_file_upload(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not message.document:
        logger.warning(f"handle_scheduled_file_upload called without user/message/document for user {user.id if user else 'Unknown'}")
        if context.user_data.get('pending_schedule'):
            await message.reply_text(convert_to_premium_emoji("⚠️ Please send the JSON <b>file</b> to schedule, not text. Or use /cancel."), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)
        return

    user_id = user.id
    pending_schedule = context.user_data['pending_schedule']
    schedule_name = pending_schedule.get('schedule_key') or pending_schedule['schedule_name']
    user_filename = pending_schedule['user_filename']
    interval_seconds = pending_schedule.get('interval_seconds')
    daily_time = pending_schedule.get('daily_time')
    schedule_tz = pending_schedule.get('schedule_tz')
    bound_repo = pending_schedule.get('github_repo')
    target_filename = pending_schedule.get('github_target_filename')
    storage_name = schedule_storage_name(schedule_name)

    document = message.document
    original_telegram_filename = document.file_name or f"file_{document.file_id}.json"

    is_json_mime = document.mime_type and document.mime_type.lower() == 'application/json'
    has_json_extension = original_telegram_filename and original_telegram_filename.lower().endswith('.json')
    if not is_json_mime and not has_json_extension:
        await message.reply_text(convert_to_premium_emoji(f"❌ The file you sent (<code>{escape(original_telegram_filename)}</code>) doesn't seem to be a JSON file. Schedule cancelled."), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)
        context.user_data.pop('pending_schedule', None)
        return

    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await message.reply_text(
            convert_to_premium_emoji(f"⚠️ File is too large ({document.file_size / 1024 / 1024:.2f} MB). Max: {MAX_FILE_SIZE / 1024 / 1024:.1f} MB. Schedule cancelled."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        context.user_data.pop('pending_schedule', None)
        return

    temp_download_path = os.path.join(TEMP_DIR, f'schedule_down_{user_id}_{storage_name}_{int(time.time())}.json')
    persistent_file_path = os.path.join(SCHEDULED_FILES_DATA_DIR, f"{user_id}_{storage_name}")
    progress_msg = None

    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(SCHEDULED_FILES_DATA_DIR, exist_ok=True)

        progress_msg = await message.reply_text(convert_to_premium_emoji(f"⏳ Downloading <code>{escape(original_telegram_filename)}</code> for schedule <code>'{escape(user_filename)}'</code>..."), parse_mode=ParseMode.HTML)

        bot_file = await context.bot.get_file(document.file_id)
        await bot_file.download_to_drive(temp_download_path)
        logger.info(f"Downloaded file for schedule '{schedule_name}' (user {user_id}) to temp path: {temp_download_path}")

        actual_size = os.path.getsize(temp_download_path)
        if actual_size > MAX_FILE_SIZE:
            raise ValueError(f"Downloaded file size ({actual_size / 1024 / 1024:.2f} MB) exceeds limit.")

        try:
            with open(temp_download_path, 'r', encoding='utf-8') as f_check:
                content = json.load(f_check)
                if not isinstance(content, list):
                    raise ValueError("JSON content must be an array (list).")
            logger.info(f"JSON syntax and structure validation passed for scheduled file '{schedule_name}' (user {user_id}).")
        except json.JSONDecodeError as json_err:
            error_line_info = ""
            if hasattr(json_err, 'lineno') and hasattr(json_err, 'colno'):
                error_line_info = f" near line {json_err.lineno}, column {json_err.colno}"
            raise ValueError(f"Invalid JSON format in the uploaded file{error_line_info}. Error: {json_err.msg}")
        except ValueError as val_err:
            raise val_err
        except Exception as read_err:
            raise ValueError(f"Could not read or validate the downloaded file: {read_err}")

        shutil.move(temp_download_path, persistent_file_path)
        logger.info(f"Stored file for schedule '{schedule_name}' (user {user_id}) persistently at: {persistent_file_path}")

        schedules = load_scheduled_files()
        user_id_str = str(user_id)
        now_utc = datetime.now(timezone.utc)
        if daily_time:
            daily_hour, daily_minute = parse_daily_time(daily_time)
            next_run_time = next_daily_run_utc(daily_hour, daily_minute, schedule_tz, now_utc)
        else:
            next_run_time = now_utc + timedelta(seconds=interval_seconds)

        if user_id_str not in schedules:
            schedules[user_id_str] = {}

        schedules[user_id_str][schedule_name] = {
            'interval_seconds': interval_seconds,
            'daily_time': daily_time,
            'schedule_tz': schedule_tz,
            'github_repo': bound_repo,
            'github_target_filename': target_filename,
            'telegram_file_id': document.file_id,
            'stored_file_path': persistent_file_path,
            'last_run_time_iso': None,
            'next_run_time_iso': next_run_time.isoformat(),
            'added_on_iso': now_utc.isoformat(),
            'original_telegram_filename': original_telegram_filename,
            'user_schedule_name': user_filename
        }

        if save_scheduled_files(schedules):
            logger.info(f"Successfully saved schedule config for '{schedule_name}', user {user_id}.")
            if daily_time:
                timing_line = f"🔄 <b>Runs:</b> every day at <code>{daily_time}</code> ({escape(schedule_tz or DEFAULT_SCHEDULE_TZ)})"
                next_run_text = format_ist(next_run_time)
                repeat_text = "every day"
            else:
                timing_line = f"🔄 <b>Interval:</b> {format_time(interval_seconds)}"
                next_run_text = format_ist(next_run_time)
                repeat_text = f"every {format_time(interval_seconds)}"
            repo_text = escape(bound_repo) if bound_repo else "all your configured GitHub repos"
            file_text = escape(target_filename) if target_filename else "the filename saved in the GitHub config"
            confirmation_text = convert_to_premium_emoji(
                f"✅ <b>File Schedule Set Successfully!</b>\n\n"
                f"🏷️ <b>Schedule Name:</b> <code>{escape(user_filename)}</code>\n"
                f"📄 <b>Associated File:</b> <code>{escape(original_telegram_filename)}</code>\n"
                f"{timing_line}\n"
                f"📤 <b>GitHub:</b> <code>{repo_text}</code> → <code>{file_text}</code>\n"
                f"⏰ <b>Next Run:</b> <code>{next_run_text}</code> (approximately)\n\n"
                f"The bot will now automatically process this file and update the token file on GitHub {repeat_text}.\n"
                f"Use /scheduledfiles to view or /removefile to stop."
            )
            await context.bot.edit_message_text(
                chat_id=progress_msg.chat_id, message_id=progress_msg.message_id,
                text=confirmation_text, parse_mode=ParseMode.HTML
            )
            context.user_data.pop('pending_schedule', None)
        else:
            logger.error(f"Failed to save schedule config file after setting '{schedule_name}' for user {user_id}.")
            if os.path.exists(persistent_file_path):
                try: os.remove(persistent_file_path)
                except OSError as del_err: logger.error(f"Failed cleanup: Could not delete stored file {persistent_file_path}: {del_err}")
            raise IOError("Failed to save the updated schedule configuration file.")

    except (ValueError, IOError, OSError, TelegramError) as e:
        logger.error(f"Error setting up schedule '{schedule_name}' for user {user_id}: {e}", exc_info=False)
        error_text = convert_to_premium_emoji(f"❌ Error setting up schedule <code>'{escape(user_filename)}'</code>:\n<code>{escape(str(e))}</code>\n\nPlease try again or use /cancel.")
        if progress_msg:
            await context.bot.edit_message_text(
                chat_id=progress_msg.chat_id, message_id=progress_msg.message_id,
                text=error_text, parse_mode=ParseMode.HTML
            )
        else:
            await message.reply_text(error_text, reply_markup=main_reply_markup, parse_mode=ParseMode.HTML)
        if os.path.exists(persistent_file_path):
            try: os.remove(persistent_file_path)
            except OSError: logger.warning(f"Could not clean up stored schedule file after error: {persistent_file_path}")
        context.user_data.pop('pending_schedule', None)
    except Exception as e:
        logger.error(f"Unexpected error setting up schedule '{schedule_name}' for user {user_id}: {e}", exc_info=True)
        error_text = convert_to_premium_emoji(f"❌ An unexpected error occurred while setting up the schedule <code>'{escape(user_filename)}'</code>. Schedule cancelled.")
        if progress_msg:
            try:
                await context.bot.edit_message_text(
                    chat_id=progress_msg.chat_id, message_id=progress_msg.message_id,
                    text=error_text, parse_mode=ParseMode.HTML
                )
            except TelegramError as edit_err:
                logger.error(f"Failed to edit progress message in general exception block: {edit_err}")
                await message.reply_text(error_text, reply_markup=main_reply_markup, parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(error_text, reply_markup=main_reply_markup, parse_mode=ParseMode.HTML)
        if os.path.exists(persistent_file_path):
            try: os.remove(persistent_file_path)
            except OSError: pass
        context.user_data.pop('pending_schedule', None)
    finally:
        if os.path.exists(temp_download_path):
            try: os.remove(temp_download_path)
            except OSError as e: logger.warning(f"Could not remove temp schedule download file {temp_download_path}: {e}")


async def remove_scheduled_file(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message: return
    user_id = user.id
    add_known_user(user.id)
    context.user_data.pop('pending_schedule', None)
    context.user_data.pop('waiting_for_json', None)

    if not is_user_vip(user.id):
        await message.reply_text(
            convert_to_premium_emoji("❌ File scheduling management is a VIP feature."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    args = context.args
    usage_text = convert_to_premium_emoji(
        "Usage: <code>/removefile &lt;ScheduleName.json&gt; [owner/repo]</code> (Use the name you provided during <code>/setfile</code>. "
        "Add <code>owner/repo</code> when the same name is scheduled for more than one GitHub repo.)"
    )

    if len(args) not in (1, 2):
        await message.reply_text(
            convert_to_premium_emoji(f"❌ Incorrect number of arguments.\n\n{usage_text}"),
            parse_mode=ParseMode.HTML, reply_markup=main_reply_markup
        )
        return

    user_filename_to_remove = args[0]
    repo_arg_to_remove = args[1].strip() if len(args) == 2 else None
    sanitized_name_to_remove = sanitize_filename(user_filename_to_remove)

    schedules = load_scheduled_files()
    user_id_str = str(user_id)
    user_schedules_now = schedules.get(user_id_str, {})

    if repo_arg_to_remove:
        wanted_key = make_schedule_key(sanitized_name_to_remove, repo_arg_to_remove).lower()
        matching_keys = [k for k in user_schedules_now if k.lower() == wanted_key]
    elif sanitized_name_to_remove in user_schedules_now:
        matching_keys = [sanitized_name_to_remove]
    else:
        matching_keys = [k for k in user_schedules_now if k.startswith(sanitized_name_to_remove + '@')]

    if len(matching_keys) > 1:
        options_text = "\n".join(f"{n}. <code>/removefile {escape(sanitized_name_to_remove)} {escape(k.split('@', 1)[1])}</code>" for n, k in enumerate(matching_keys, 1))
        await message.reply_text(
            convert_to_premium_emoji(f"ℹ️ <code>'{escape(user_filename_to_remove)}'</code> is scheduled for more than one GitHub repo. Tell me which one:\n{options_text}"),
            parse_mode=ParseMode.HTML, reply_markup=main_reply_markup
        )
        return

    if not matching_keys:
        await message.reply_text(
            convert_to_premium_emoji(f"ℹ️ No schedule found with the name <code>'{escape(user_filename_to_remove)}'</code>. "
            f"Use /scheduledfiles to see your active schedules."),
            parse_mode=ParseMode.HTML, reply_markup=main_reply_markup
        )
        return

    sanitized_name_to_remove = matching_keys[0]  # the stored schedule key (name or name@owner/repo)
    schedule_info = schedules[user_id_str][sanitized_name_to_remove]
    stored_file_path = schedule_info.get('stored_file_path')
    display_name = schedule_info.get('user_schedule_name', sanitized_name_to_remove)

    del schedules[user_id_str][sanitized_name_to_remove]
    if not schedules[user_id_str]:
        del schedules[user_id_str]

    config_save_success = save_scheduled_files(schedules)
    file_delete_success = False
    file_delete_error = None

    if stored_file_path and os.path.exists(stored_file_path):
        try:
            os.remove(stored_file_path)
            file_delete_success = True
            logger.info(f"Deleted stored file for schedule '{sanitized_name_to_remove}' user {user_id}: {stored_file_path}")
        except OSError as e:
            file_delete_error = str(e)
            logger.error(f"Failed to delete stored file {stored_file_path}: {e}")

    response_parts = []
    if config_save_success:
        response_parts.append(f"✅ Schedule <code>'{escape(display_name)}'</code> removed successfully.")
        logger.info(f"Removed schedule '{sanitized_name_to_remove}' for user {user_id}.")
    else:
        response_parts.append(f"⚠️ Failed to save the configuration after removing schedule <code>'{escape(display_name)}'</code>. It might reappear temporarily.")

    if stored_file_path:
        if file_delete_success:
            response_parts.append("✅ Associated stored file deleted.")
        elif file_delete_error:
            response_parts.append(f"⚠️ Could not delete the associated stored file: {escape(file_delete_error)}")
        elif not os.path.exists(stored_file_path):
            response_parts.append("ℹ️ Associated stored file was already missing or path was invalid.")
    else:
        response_parts.append("ℹ️ No stored file path found in config for this schedule.")

    await message.reply_text(convert_to_premium_emoji("\n".join(response_parts)), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)


async def list_scheduled_files(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message: return
    user_id = user.id
    add_known_user(user.id)
    context.user_data.pop('pending_schedule', None)
    context.user_data.pop('waiting_for_json', None)

    if not is_user_vip(user.id):
        await message.reply_text(
            convert_to_premium_emoji("ℹ️ File scheduling is a VIP feature. Use /vipshop to upgrade."),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_markup
        )
        return

    schedules = load_scheduled_files()
    user_id_str = str(user.id)
    user_schedules = schedules.get(user_id_str, {})

    if not user_schedules:
        await message.reply_text(
            convert_to_premium_emoji("ℹ️ You have no files currently scheduled for automatic processing.\n\n"
            "Use <code>/setfile &lt;Interval or Time&gt; &lt;ScheduleName.json&gt; [owner/repo] [TokenFile.json]</code> to set one up."),
            parse_mode=ParseMode.HTML, reply_markup=main_reply_markup
        )
        return

    message_parts = [f"⚙️ <b>Your Scheduled Files for Auto-Processing ({len(user_schedules)} total):</b>\n"]
    now_utc = datetime.now(timezone.utc)

    sorted_schedule_items = sorted(
        user_schedules.items(),
        key=lambda item: (str(item[1].get('user_schedule_name', item[0])), item[0])
    )

    for schedule_number, (schedule_name, details) in enumerate(sorted_schedule_items, 1):
        if not isinstance(details, dict): continue

        user_display_name = details.get('user_schedule_name', schedule_name)
        interval_s = details.get('interval_seconds')
        next_run_iso = details.get('next_run_time_iso')
        last_run_iso = details.get('last_run_time_iso')
        original_tg_file = details.get('original_telegram_filename', 'N/A')

        message_parts.append(f"\n📋 <b>Schedule #{schedule_number}</b>")
        message_parts.append(f"🏷️ <b>Name:</b> <code>{escape(user_display_name)}</code>")
        message_parts.append(f"   📄 <b>Source File:</b> <code>{escape(original_tg_file)}</code>")
        gh_bound_repo = details.get('github_repo')
        gh_target_file = details.get('github_target_filename')
        message_parts.append(
            f"   📤 <b>GitHub:</b> <code>{escape(gh_bound_repo) if gh_bound_repo else 'all configs'}</code> → "
            f"<code>{escape(gh_target_file) if gh_target_file else 'config filename'}</code>"
        )
        if details.get('daily_time'):
            message_parts.append(f"   🔄 <b>Runs:</b> every day at <code>{escape(str(details['daily_time']))}</code> ({escape(str(details.get('schedule_tz') or DEFAULT_SCHEDULE_TZ))})")
        elif interval_s and isinstance(interval_s, int):
            message_parts.append(f"   🔄 <b>Interval:</b> {format_time(interval_s)}")
        else:
            message_parts.append(f"   🔄 <b>Interval:</b> <code>Error: Invalid/Not Set</code>")

        if next_run_iso:
            try:
                next_run_dt = datetime.fromisoformat(next_run_iso.replace('Z', '+00:00'))
                if details.get('daily_time'):
                    next_run_formatted = format_ist(next_run_dt, with_seconds=False)
                else:
                    next_run_formatted = format_ist(next_run_dt, with_seconds=False)
                time_until_next = next_run_dt - now_utc
                if time_until_next.total_seconds() > 0:
                    remaining_str = format_time(time_until_next.total_seconds())
                    message_parts.append(f"   ⏰ <b>Next Run:</b> {next_run_formatted} (<code>{remaining_str}</code>)")
                else:
                    message_parts.append(f"   ⏰ <b>Next Run:</b> {next_run_formatted} (<code>Due now or overdue</code>)")
            except (ValueError, TypeError):
                message_parts.append(f"   ⏰ <b>Next Run:</b> <code>Error: Invalid Date ({escape(str(next_run_iso)[:19])})</code>")
        else:
            message_parts.append(f"   ⏰ <b>Next Run:</b> <code>Not Scheduled Yet / Error</code>")

        if last_run_iso:
            try:
                last_run_dt = datetime.fromisoformat(last_run_iso.replace('Z', '+00:00'))
                last_run_formatted = format_ist(last_run_dt, with_seconds=False)
                message_parts.append(f"   ⏱️ <b>Last Run:</b> {last_run_formatted}")
            except (ValueError, TypeError):
                message_parts.append(f"   ⏱️ <b>Last Run:</b> <code>Invalid Date</code>")
        else:
            message_parts.append(f"   ⏱️ <b>Last Run:</b> <code>Never</code>")

    message_parts.append("\nUse <code>/removefile &lt;ScheduleName.json&gt; [owner/repo]</code> to stop a schedule.")

    final_message = "\n".join(message_parts)
    if len(final_message) > 4096:
        await message.reply_text(convert_to_premium_emoji("⚠️ Your list of scheduled files is too long to display fully. Showing the first part:"), parse_mode=ParseMode.HTML)
        safe_truncate_point = final_message[:4050].rfind('\n')
        if safe_truncate_point == -1: safe_truncate_point = 4050
        await message.reply_text(convert_to_premium_emoji(final_message[:safe_truncate_point]+"..."), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)
    else:
        await message.reply_text(convert_to_premium_emoji(final_message), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)

# --- Admin Commands ---

async def vip_management(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not ADMIN_ID or user.id != ADMIN_ID:
        logger.warning(f"Unauthorized access attempt to /vip by user {user.id if user else 'Unknown'}")
        if message: await message.reply_text("You are not authorized to use this command.", reply_markup=main_reply_markup)
        return
    if message.chat.type != 'private':
        await message.reply_text("Admin commands must be used in a private chat with the bot.")
        return

    args = context.args
    command_usage = convert_to_premium_emoji(
        "👑 <b>Admin VIP Management</b>\n\n"
        "<b>Usage:</b>\n"
        "<code>/vip add &lt;user_id&gt; &lt;days&gt;</code> - Add/extend VIP\n"
        "<code>/vip remove &lt;user_id&gt;</code> - Remove VIP, ALL GitHub configs &amp; scheduled files\n"
        "<code>/vip list</code> - List VIPs (active &amp; expired)\n\n"
        "<b>Example:</b> <code>/vip add 123456789 30</code>"
    )

    if not args:
        await message.reply_text(command_usage, parse_mode=ParseMode.HTML)
        return

    action = args[0].lower()
    vip_data = load_vip_data()

    if action == 'add':
        if len(args) != 3:
            return await message.reply_text(convert_to_premium_emoji(f"⚠️ Incorrect arguments for 'add'.\n\n{command_usage}"), parse_mode=ParseMode.HTML)

        try:
            target_user_id_str, days_str = args[1], args[2]
            if not target_user_id_str.isdigit() or not days_str.isdigit():
                return await message.reply_text(convert_to_premium_emoji("⚠️ Invalid User ID or Days. Both must be numbers."), parse_mode=ParseMode.HTML)

            target_user_id = int(target_user_id_str)
            days_to_add = int(days_str)

            if days_to_add <= 0:
                return await message.reply_text(convert_to_premium_emoji("⚠️ Number of days must be positive."), parse_mode=ParseMode.HTML)

            now_utc = datetime.now(timezone.utc)
            start_date_for_calc = now_utc

            user_vip_info = vip_data.get(target_user_id_str, {})
            if not isinstance(user_vip_info, dict): user_vip_info = {}

            is_extending = False
            if target_user_id_str in vip_data:
                try:
                    current_expiry_iso = user_vip_info.get('expiry')
                    if current_expiry_iso:
                        current_expiry_dt = datetime.fromisoformat(current_expiry_iso.replace('Z', '+00:00'))
                        if current_expiry_dt > now_utc:
                            start_date_for_calc = current_expiry_dt
                            is_extending = True
                            logger.info(f"Extending existing VIP for {target_user_id} from {current_expiry_dt.isoformat()}")
                except (ValueError, TypeError, KeyError) as e:
                    logger.warning(f"Invalid expiry format for user {target_user_id_str}: {e}. Starting new period from now.")
                    user_vip_info = {}

            new_expiry_date = start_date_for_calc + timedelta(days=days_to_add)

            user_vip_info.update({
                'expiry': new_expiry_date.isoformat(),
                'added_by': user.id,
                'added_on': user_vip_info.get('added_on', now_utc.isoformat()),
                'last_update': now_utc.isoformat()
            })
            vip_data[target_user_id_str] = user_vip_info

            if save_vip_data(vip_data):
                expiry_formatted_display = format_ist(new_expiry_date)
                action_word = "Extended" if is_extending else "Added"
                response_msg = convert_to_premium_emoji(f"✅ VIP {action_word} for User ID <code>{target_user_id}</code>.\nDuration Added: {days_to_add} days\nNew Expiry: <code>{expiry_formatted_display}</code>")
                logger.info(f"Admin {user.id} {action_word.lower()} VIP for {target_user_id} to {expiry_formatted_display}")
                await message.reply_text(response_msg, parse_mode=ParseMode.HTML)

                try:
                    target_user_info_str = f"User ID <code>{target_user_id}</code>"
                    try:
                        chat_info = await context.bot.get_chat(target_user_id)
                        name_parts = []
                        if chat_info.username: name_parts.append(f"@{escape(chat_info.username)}")
                        elif chat_info.first_name: name_parts.append(escape(chat_info.first_name))
                        if name_parts:
                            target_user_info_str = f"{' '.join(name_parts)} (<code>{target_user_id}</code>)"
                    except TelegramError as chat_err:
                        logger.warning(f"Could not get chat info for {target_user_id}: {chat_err}")

                    admin_name = escape(user.first_name) or f"Admin (<code>{user.id}</code>)"
                    added_on_str = "Unknown"
                    added_on_iso = user_vip_info.get('added_on')
                    if added_on_iso:
                        try:
                            added_on_dt = datetime.fromisoformat(added_on_iso.replace('Z', '+00:00'))
                            added_on_str = format_ist(added_on_dt)
                        except (ValueError, TypeError): added_on_str = f"Invalid ({escape(added_on_iso[:19])})"

                    vip_dm_message = convert_to_premium_emoji(
                        f"🎉 Congratulations! Your VIP status has been {'updated' if is_extending else 'activated'}!\n\n"
                        f"📊 <b>Status:</b> Active VIP ✔️\n"
                        f"🆔 <b>User:</b> {target_user_info_str}\n"
                        f"📅 <b>Subscription Start:</b> <code>{added_on_str}</code>\n"
                        f"📅 <b>Expires:</b> <code>{expiry_formatted_display}</code>\n"
                        f"👤 <b>Updated by:</b> {admin_name}\n\n"
                        "Enjoy your premium features, including GitHub auto-upload to multiple repos and file scheduling!"
                    )

                    await context.bot.send_message(target_user_id, vip_dm_message, parse_mode=ParseMode.HTML)
                    await message.reply_text(convert_to_premium_emoji(f"✅ User <code>{target_user_id}</code> notified of the update."), parse_mode=ParseMode.HTML, disable_notification=True)

                except Forbidden:
                    logger.warning(f"Could not notify user {target_user_id} about VIP update (Forbidden).")
                    await message.reply_text(convert_to_premium_emoji(f"⚠️ Could not notify user <code>{target_user_id}</code> (Bot blocked or user left)."), parse_mode=ParseMode.HTML, disable_notification=True)
                except BadRequest as notify_err:
                    logger.warning(f"Could not notify user {target_user_id} about VIP update (BadRequest): {notify_err}")
                    await message.reply_text(convert_to_premium_emoji(f"⚠️ Could not notify user <code>{target_user_id}</code>: {escape(str(notify_err))}"), parse_mode=ParseMode.HTML, disable_notification=True)
                except TelegramError as notify_err:
                    logger.warning(f"Could not notify user {target_user_id} about VIP update (TelegramError): {notify_err}")
                    await message.reply_text(convert_to_premium_emoji(f"⚠️ Could not notify user <code>{target_user_id}</code>: {escape(str(notify_err))}"), parse_mode=ParseMode.HTML, disable_notification=True)
                except Exception as notify_err:
                    logger.error(f"Unexpected error notifying user {target_user_id} about VIP update: {notify_err}", exc_info=True)
                    await message.reply_text(convert_to_premium_emoji(f"⚠️ Unexpected error creating/sending notification to user <code>{target_user_id}</code>."), parse_mode=ParseMode.HTML, disable_notification=True)

            else:
                logger.error(f"Failed to save VIP data file after attempting to add/extend for user {target_user_id_str}")
                await message.reply_text(convert_to_premium_emoji("❌ <b>Error:</b> Could not save updated VIP data to file. The change was not applied."), parse_mode=ParseMode.HTML)

        except ValueError:
            await message.reply_text(convert_to_premium_emoji("⚠️ Invalid number format for User ID or Days."), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Error processing '/vip add' command: {e}", exc_info=True)
            await message.reply_text(convert_to_premium_emoji(f"An unexpected error occurred during VIP addition: {escape(str(e))}"), parse_mode=ParseMode.HTML)

    elif action == 'remove':
        if len(args) != 2:
            return await message.reply_text(convert_to_premium_emoji(f"⚠️ Incorrect arguments for 'remove'.\n\n{command_usage}"), parse_mode=ParseMode.HTML)

        target_user_id_str = args[1]
        if not target_user_id_str.isdigit():
            return await message.reply_text(convert_to_premium_emoji("⚠️ Invalid User ID format. Must be a number."), parse_mode=ParseMode.HTML)

        target_user_id = int(target_user_id_str)
        removed_vip, removed_github, removed_schedules = False, False, False
        schedule_files_deleted_count = 0
        vip_save_error, github_save_error, schedule_save_error = False, False, False
        file_delete_errors = []
        response_parts = []

        was_vip = target_user_id_str in vip_data
        if was_vip:
            del vip_data[target_user_id_str]
            if save_vip_data(vip_data):
                removed_vip = True
                response_parts.append(f"✅ Successfully removed VIP status for <code>{target_user_id_str}</code>.")
                logger.info(f"Admin {user.id} removed VIP for {target_user_id_str}.")
            else:
                vip_save_error = True
                response_parts.append(f"❌ Error saving VIP data after attempting removal for <code>{target_user_id_str}</code>.")
                logger.error(f"Failed to save VIP data after removing {target_user_id_str}.")
                vip_data = load_vip_data()
        else:
            response_parts.append(f"ℹ️ User <code>{target_user_id_str}</code> was not found in the VIP list.")

        github_configs = load_github_configs()
        was_github_config = target_user_id_str in github_configs
        if was_github_config:
            user_configs_snapshot = github_configs.get(target_user_id_str, {})
            config_count = len(user_configs_snapshot) if isinstance(user_configs_snapshot, dict) else 1
            del github_configs[target_user_id_str]
            if save_github_configs(github_configs):
                removed_github = True
                response_parts.append(f"✅ Successfully removed {config_count} GitHub config(s) for <code>{target_user_id_str}</code>.")
                logger.info(f"Removed {config_count} GitHub config(s) for {target_user_id_str} during VIP removal.")
            else:
                github_save_error = True
                response_parts.append(f"❌ Error saving GitHub config data after attempting removal for <code>{target_user_id_str}</code>.")
                logger.error(f"Failed to save GitHub config data after removing for {target_user_id_str}.")

        schedules_data = load_scheduled_files()
        user_schedules = schedules_data.get(target_user_id_str, {})
        if user_schedules:
            schedule_names_to_remove = list(user_schedules.keys())
            paths_to_delete = [info.get('stored_file_path') for info in user_schedules.values() if info.get('stored_file_path')]

            del schedules_data[target_user_id_str]
            if save_scheduled_files(schedules_data):
                removed_schedules = True
                response_parts.append(f"✅ Successfully removed {len(schedule_names_to_remove)} scheduled file configuration(s) for <code>{target_user_id_str}</code>.")
                logger.info(f"Removed {len(schedule_names_to_remove)} schedule configs for {target_user_id_str} during VIP removal.")

                for file_path in paths_to_delete:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            schedule_files_deleted_count += 1
                            logger.info(f"Deleted stored schedule file: {file_path}")
                        except OSError as e:
                            file_delete_errors.append(os.path.basename(file_path))
                            logger.error(f"Error deleting stored schedule file {file_path}: {e}")
                if paths_to_delete:
                    if not file_delete_errors:
                        response_parts.append(f"✅ Deleted {schedule_files_deleted_count} associated stored file(s).")
                    else:
                        safe_failed_files = escape(", ".join(file_delete_errors))
                        response_parts.append(f"⚠️ Deleted {schedule_files_deleted_count} stored file(s), but failed to delete: <code>{safe_failed_files}</code>")

            else:
                schedule_save_error = True
                response_parts.append(f"❌ Error saving schedule config data after attempting removal for <code>{target_user_id_str}</code>.")
                logger.error(f"Failed to save schedule config data after removing for {target_user_id_str}.")

        await message.reply_text(convert_to_premium_emoji("\n".join(response_parts) if response_parts else "No action taken or user not found."), parse_mode=ParseMode.HTML)

        if removed_vip:
            try:
                notify_message = "ℹ️ Your VIP status has been removed by an admin."
                removed_features = []
                if removed_github: removed_features.append("GitHub upload configs (all repos)")
                if removed_schedules: removed_features.append("scheduled file processing")
                if removed_features:
                    notify_message += f"\nAssociated features removed: {', '.join(removed_features)}."

                await context.bot.send_message(chat_id=target_user_id, text=convert_to_premium_emoji(notify_message), parse_mode=ParseMode.HTML)
                await message.reply_text(convert_to_premium_emoji(f"✅ User <code>{target_user_id}</code> notified of removal."), parse_mode=ParseMode.HTML, disable_notification=True)
            except Forbidden:
                logger.warning(f"Could not notify user {target_user_id} about VIP removal (Forbidden).")
                await message.reply_text(convert_to_premium_emoji(f"⚠️ Could not notify user <code>{target_user_id}</code> of removal (Bot blocked or user left)."), parse_mode=ParseMode.HTML, disable_notification=True)
            except BadRequest as notify_err:
                logger.warning(f"Could not notify user {target_user_id} about VIP removal (BadRequest): {notify_err}")
                await message.reply_text(convert_to_premium_emoji(f"⚠️ Could not notify user <code>{target_user_id}</code> of removal: {escape(str(notify_err))}"), parse_mode=ParseMode.HTML, disable_notification=True)
            except TelegramError as notify_err:
                logger.warning(f"Could not notify user {target_user_id} about VIP removal (TelegramError): {notify_err}")
                await message.reply_text(convert_to_premium_emoji(f"⚠️ Could not notify user <code>{target_user_id}</code> of removal: {escape(str(notify_err))}"), parse_mode=ParseMode.HTML, disable_notification=True)
            except Exception as notify_err:
                logger.error(f"Unexpected error notifying user {target_user_id} of removal: {notify_err}", exc_info=True)
                await message.reply_text(convert_to_premium_emoji(f"⚠️ Unexpected error notifying user <code>{target_user_id}</code> of removal."), parse_mode=ParseMode.HTML, disable_notification=True)

    elif action == 'list':
        active_vips, inactive_vips, invalid_entries = [], [], []
        active_vip_expiry_ts = {}  # entry text -> expiry timestamp (used for sorting)
        now_utc = datetime.now(timezone.utc)

        if ADMIN_ID and ADMIN_ID != 0:
            active_vips.append(f"✅ ID: <code>{ADMIN_ID}</code> | 👑 Bot Owner — Permanent VIP")

        for uid_str, data in vip_data.items():
            safe_uid_str = escape(uid_str)
            if not isinstance(data, dict):
                invalid_entries.append(f"ID: <code>{safe_uid_str}</code> | Invalid data format (not a dictionary)")
                continue

            try:
                expiry_iso = data.get('expiry')
                if not expiry_iso:
                    invalid_entries.append(f"ID: <code>{safe_uid_str}</code> | Missing 'expiry' date field")
                    continue

                expiry_dt = datetime.fromisoformat(expiry_iso.replace('Z', '+00:00'))
                expiry_fmt = format_ist(expiry_dt, with_seconds=False)

                if expiry_dt > now_utc:
                    rem_delta = expiry_dt - now_utc
                    days = rem_delta.days
                    hours, remainder = divmod(rem_delta.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    rem_str = ""
                    if days > 0: rem_str += f"{days}d "
                    if hours > 0: rem_str += f"{hours}h "
                    if days <= 0 and hours <= 0 and minutes > 0: rem_str += f"{minutes}m"
                    if not rem_str.strip(): rem_str = "< 1m"
                    rem_str = rem_str.strip()
                    _active_entry = f"✅ ID: <code>{safe_uid_str}</code> | Expires: {escape(expiry_fmt)} | Rem: <code>{escape(rem_str)}</code>"
                    active_vips.append(_active_entry)
                    active_vip_expiry_ts[_active_entry] = expiry_dt.timestamp()
                else:
                    inactive_vips.append(f"❌ ID: <code>{safe_uid_str}</code> | Expired: {escape(expiry_fmt)}")

            except (ValueError, TypeError):
                safe_iso_snippet = escape(str(expiry_iso)[:25])
                invalid_entries.append(f"ID: <code>{safe_uid_str}</code> | Invalid date format stored ('{safe_iso_snippet}...')")
            except Exception as e:
                invalid_entries.append(f"ID: <code>{safe_uid_str}</code> | Error processing entry: {escape(str(e))}")

        message_parts = [f"🌟 <b>VIP User List</b> ({len(active_vips)} Active)\n"]

        if active_vips:
            active_vips_filtered = [v for v in active_vips if "Bot Owner" not in v]
            try:
                active_vips_filtered.sort(key=lambda x: active_vip_expiry_ts.get(x, 0))
            except Exception:
                pass
            owner_entries = [v for v in active_vips if "Bot Owner" in v]
            message_parts.append(f"<b>Active VIPs ({len(active_vips)}):</b>")
            # Numbered 1, 2, 3... (owner first, then by nearest expiry)
            for _n, _entry in enumerate(owner_entries + active_vips_filtered, 1):
                message_parts.append(f"<b>{_n}.</b> {_entry}")
        else:
            message_parts.append("No active VIP users found.")

        if inactive_vips:
            inactive_vips.sort(key=lambda x: int(re.search(r'ID: <code>(\d+)</code>', x).group(1)) if re.search(r'ID: <code>(\d+)</code>', x) else 0)
            message_parts.append(f"\n<b>Expired VIPs ({len(inactive_vips)}):</b>")
            for _n, _entry in enumerate(inactive_vips, 1):
                message_parts.append(f"<b>{_n}.</b> {_entry}")

        if invalid_entries:
            invalid_entries.sort(key=lambda x: int(re.search(r'ID: <code>(\d+)</code>', x).group(1)) if re.search(r'ID: <code>(\d+)</code>', x) else 0)
            message_parts.append(f"\n<b>Invalid/Error Entries ({len(invalid_entries)}):</b>")
            for _n, _entry in enumerate(invalid_entries, 1):
                message_parts.append(f"<b>{_n}.</b> {_entry}")

        message_parts.append(f"\nTotal entries in VIP file: {len(vip_data)}")

        final_message = "\n".join(message_parts)
        if len(final_message) > 4096:
            logger.warning("VIP list message exceeds 4096 characters. Sending truncated message.")
            split_point = final_message.rfind('\n', 0, 4080)
            if split_point == -1: split_point = 4080
            await message.reply_text(convert_to_premium_emoji(final_message[:split_point] + "\n\n...(list truncated due to length)"), parse_mode=ParseMode.HTML)
        else:
            await message.reply_text(convert_to_premium_emoji(final_message), parse_mode=ParseMode.HTML)

    else:
        await message.reply_text(convert_to_premium_emoji(f"⚠️ Invalid action '{escape(action)}'.\n\n{command_usage}"), parse_mode=ParseMode.HTML)


async def broadcast(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message

    if not user or not message or not ADMIN_ID or user.id != ADMIN_ID:
        logger.warning(f"Unauthorized access attempt to /broadcast by user {user.id if user else 'Unknown'}")
        if message: await message.reply_text("You are not authorized to use this command.")
        return
    if message.chat.type != 'private':
        await message.reply_text("Broadcast must be initiated from a private chat with the bot.")
        return

    message_to_send = ""
    parse_mode_to_use = None

    replied_message = message.reply_to_message
    if replied_message:
        message_to_send = replied_message.text or replied_message.caption or ""
        if replied_message.text_html or replied_message.caption_html:
            parse_mode_to_use = ParseMode.HTML
            message_to_send = replied_message.text_html or replied_message.caption_html
            logger.debug("Using HTML parse mode for broadcast from replied message.")
        elif replied_message.text_markdown_v2 or replied_message.caption_markdown_v2:
            parse_mode_to_use = ParseMode.MARKDOWN_V2
            message_to_send = replied_message.text_markdown_v2 or replied_message.caption_markdown_v2
            logger.debug("Using MarkdownV2 parse mode for broadcast from replied message.")
    else:
        if message.text:
            text_content = message.text
            command_pattern = rf"^\s*/broadcast(?:@{context.bot.username})?\s+"
            message_to_send = re.sub(command_pattern, '', text_content, count=1, flags=re.IGNORECASE | re.DOTALL).strip()

            if message.entities:
                if any(e.type in ['bold', 'italic', 'code', 'pre', 'text_link', 'strikethrough', 'underline', 'spoiler'] for e in message.entities):
                    parse_mode_to_use = ParseMode.MARKDOWN_V2
                    message_to_send = message.text_markdown_v2
                    logger.debug(f"Detected MarkdownV2 entities, using MarkdownV2 for broadcast.")

    if not message_to_send:
        return await message.reply_text(
            "Usage: <code>/broadcast &lt;Your message here&gt;</code>\n"
            "Or reply to the message you want to broadcast with just <code>/broadcast</code>.\n"
            "(You should use MarkdownV2 or HTML formatting in your message).",
            parse_mode=ParseMode.HTML
        )

    if len(message_to_send) > 4096:
        return await message.reply_text(convert_to_premium_emoji("❌ Message too long (max 4096 chars). Please shorten it."), parse_mode=ParseMode.HTML)

    known_users = load_known_users()
    if not known_users:
        return await message.reply_text(convert_to_premium_emoji("ℹ️ No known users found in the database to broadcast to."), parse_mode=ParseMode.HTML)

    total_users = len(known_users)

    logger.info(f"Admin {user.id} initiated broadcast to {total_users} users. ParseMode: {parse_mode_to_use}.")
    status_message_obj = None
    try:
        status_message_obj = await message.reply_text(convert_to_premium_emoji(f"📣 Broadcasting to {total_users} users... Starting now."), parse_mode=ParseMode.HTML)
    except TelegramError as e:
        logger.error(f"Failed to send initial broadcast status message: {e}")
        await message.reply_text(convert_to_premium_emoji(f"⚠️ Failed to start broadcast status tracking. Attempting to send anyway..."), parse_mode=ParseMode.HTML)
        status_message_obj = None

    success, fail, blocked, other_fail = 0, 0, 0, 0
    start_time = time.time()
    update_interval_count = max(10, min(100, total_users // 20 if total_users >= 200 else 10))
    last_status_update_time = time.time()

    user_list = list(known_users)
    users_to_remove = set()

    for i, user_id_to_send in enumerate(user_list):
        if user_id_to_send == ADMIN_ID:
            logger.debug("Skipping broadcast to admin self.")
            continue

        sent = False
        try:
            await context.bot.send_message(
                chat_id=user_id_to_send,
                text=message_to_send,
                parse_mode=parse_mode_to_use,
                disable_web_page_preview=True
            )
            success += 1
            sent = True
            logger.debug(f"Broadcast: Sent to {user_id_to_send}")
        except Forbidden:
            blocked += 1
            users_to_remove.add(user_id_to_send)
            logger.debug(f"Broadcast: User {user_id_to_send} blocked the bot or is deactivated.")
        except BadRequest as e:
            error_str = str(e).lower()
            if any(sub in error_str for sub in ["chat not found", "user is deactivated", "bot was kicked", "user not found", "peer_id_invalid", "bot_blocked_by_user", "group chat was deactivated"]):
                blocked += 1
                users_to_remove.add(user_id_to_send)
                logger.debug(f"Broadcast: User {user_id_to_send} unreachable ({error_str}). Marking for removal.")
            elif "can't parse entities" in error_str:
                other_fail += 1
                logger.warning(f"Broadcast parse error for {user_id_to_send}: {e}.")
            else:
                other_fail += 1
                logger.warning(f"Broadcast BadRequest for {user_id_to_send}: {e}")
        except TelegramError as e:
            logger.warning(f"Broadcast TelegramError for {user_id_to_send}: {e}")
            other_fail += 1
        except Exception as e:
            logger.error(f"Broadcast unexpected error for {user_id_to_send}: {e}", exc_info=True)
            other_fail += 1

        if not sent:
            fail += 1

        current_time = time.time()
        processed_users = i + 1
        should_update_count = (update_interval_count > 0 and processed_users % update_interval_count == 0)
        should_update_time = (current_time - last_status_update_time > 10)
        is_last_user = (processed_users == total_users)

        if status_message_obj and (should_update_count or should_update_time or is_last_user):
            try:
                status_text = convert_to_premium_emoji(f"📣 Broadcasting... {processed_users}/{total_users}\n"
                               f"✅ Sent: {success} | ❌ Failed: {fail} "
                               f"(🚫Blocked/Gone: {blocked}, ❓Other: {other_fail})")
                await context.bot.edit_message_text(
                    chat_id=status_message_obj.chat_id, message_id=status_message_obj.message_id,
                    text=status_text,
                    parse_mode=ParseMode.HTML
                )
                last_status_update_time = current_time
            except TelegramError as edit_err:
                if "Message is not modified" not in str(edit_err):
                    logger.warning(f"Broadcast status update failed: {edit_err}")
                last_status_update_time = current_time

        await asyncio.sleep(0.05)

    end_time = time.time()
    duration = format_time(end_time - start_time)
    removed_count = len(users_to_remove)
    save_status = "N/A"

    if users_to_remove:
        logger.info(f"Broadcast complete. Attempting to remove {removed_count} blocked/unreachable users from known list.")
        current_known_users = load_known_users()
        cleaned_users = current_known_users - users_to_remove
        if save_known_users(cleaned_users):
            final_user_count = len(cleaned_users)
            save_status = f"✅ Removed {removed_count} inactive users ({final_user_count} remain)"
            logger.info(f"Saved cleaned known_users file after broadcast. {final_user_count} users remain.")
        else:
            save_status = f"❌ Save FAILED! ({removed_count} were marked for removal)"
            logger.error("Failed saving cleaned known_users file after broadcast.")
    else:
        save_status = f"✅ 0 users marked for removal ({total_users} remain)."

    logger.info(f"Broadcast finished. Sent: {success}, Failed: {fail}. Cleanup: {save_status}. Duration: {duration}")

    final_text = convert_to_premium_emoji(
        f"🏁 Broadcast Complete!\n\n"
        f"✅ Messages Sent: {success}\n"
        f"❌ Send Failures: {fail}\n"
        f"   - 🚫 Blocked/Gone: {blocked}\n"
        f"   - ❓ Other Errors: {other_fail}\n"
        f"👥 Total Users Attempted: {total_users}\n"
        f"🧹 User List Cleanup: {save_status}\n"
        f"⏱️ Duration: {duration}"
    )

    if status_message_obj:
        try:
            await context.bot.edit_message_text(
                chat_id=status_message_obj.chat_id, message_id=status_message_obj.message_id,
                text=final_text,
                parse_mode=ParseMode.HTML
            )
        except TelegramError as e:
            logger.warning(f"Failed to edit final broadcast status: {e}")
            await message.reply_text(final_text, parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(final_text, parse_mode=ParseMode.HTML)

# --- Message Forwarding ---
async def forward_to_admin(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message

    if not ADMIN_ID or ADMIN_ID == 0: return
    if not user or not message: return
    if message.chat.type != 'private': return
    if user.id == ADMIN_ID: return

    if context.user_data.get('pending_schedule'):
        await message.reply_text(convert_to_premium_emoji("⏳ I'm currently waiting for you to send the JSON <b>file</b> for your schedule. Please send the file or use /cancel."), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)
        return
    if context.user_data.get('waiting_for_json'):
        await message.reply_text(convert_to_premium_emoji("⏳ I'm currently waiting for you to send the JSON <b>file</b> for manual processing. Please send the file or use /cancel."), parse_mode=ParseMode.HTML, reply_markup=main_reply_markup)
        return

    add_known_user(user.id)

    try:
        user_info = convert_to_premium_emoji(f"⬆️ Forwarded message from: ID <code>{user.id}</code>")
        details = []
        if user.username: details.append(f"@{escape(user.username)}")
        if user.first_name: details.append(escape(user.first_name))
        if user.last_name: details.append(escape(user.last_name))
        if details: user_info += f" ({' '.join(details)})"

        await context.bot.send_message(ADMIN_ID, user_info, parse_mode=ParseMode.HTML)
        await context.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )
        logger.info(f"Forwarded message ID {message.message_id} from user {user.id} to admin {ADMIN_ID}")

    except Forbidden:
        logger.error(f"Failed to forward message to admin {ADMIN_ID}: Bot might be blocked by admin.")
    except TelegramError as e:
        logger.error(f"Failed to forward message from user {user.id} to admin {ADMIN_ID} (TelegramError): {e}")
    except Exception as e:
        logger.error(f"Unexpected error forwarding message from user {user.id} to admin {ADMIN_ID}: {e}", exc_info=True)

# --- Global Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)

    user_notified = False
    if isinstance(update, Update) and update.effective_chat:
        chat_id_for_notify = update.effective_chat.id
        cleaned = False
        if context.user_data.pop('pending_schedule', None):
            logger.info(f"Cleared 'pending_schedule' state for chat {chat_id_for_notify} due to error.")
            cleaned = True
        if context.user_data.pop('waiting_for_json', None):
            logger.info(f"Cleared 'waiting_for_json' state for chat {chat_id_for_notify} due to error.")
            cleaned = True

        if cleaned:
            try:
                await context.bot.send_message(
                    chat_id=chat_id_for_notify,
                    text=convert_to_premium_emoji("⚠️ An internal error occurred. Any pending action (like file scheduling or waiting for a file) has been cancelled. Please try again."),
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_reply_markup
                )
                user_notified = True
            except Exception as notify_err:
                logger.error(f"Failed to notify user {chat_id_for_notify} about state cleanup after error: {notify_err}")

    if not ADMIN_ID or ADMIN_ID == 0:
        if not user_notified:
            logger.warning("Admin ID not set or invalid, cannot send error notification.")
        return

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    try:
        update_str = "Update data unavailable."
        if isinstance(update, Update):
            try:
                update_data = update.to_dict()
                if 'message' in update_data and update_data['message']:
                    msg = update_data['message']
                    user_id_from_update = msg.get('from_user', {}).get('id', 'unknown')
                    update_data_simple = {'update_id': update_data.get('update_id'), 'message': {'message_id': msg.get('message_id'), 'chat_id': msg.get('chat',{}).get('id'), 'from_user_id': user_id_from_update, 'type': msg.get('document',{}).get('mime_type') or ('text' if 'text' in msg else 'other')}}
                    update_str = json.dumps(update_data_simple, indent=1, ensure_ascii=False, default=str)
                else:
                    update_str = json.dumps(update_data, indent=1, ensure_ascii=False, default=str, skipkeys=True)
            except Exception as json_err:
                logger.error(f"Could not serialize update object to JSON: {json_err}")
                update_str = str(update)
        elif update: update_str = str(update)

        context_str = "Context data unavailable or complex."
        try:
            from pprint import pformat
            chat_data_str = pformat(context.chat_data, width=80, depth=2)[:300] + ('...' if len(pformat(context.chat_data)) > 300 else '')
            user_data_str = pformat(context.user_data, width=80, depth=2)[:300] + ('...' if len(pformat(context.user_data)) > 300 else '')
            bot_data_str = pformat(context.bot_data, width=80, depth=2)[:200] + ('...' if len(pformat(context.bot_data)) > 200 else '')
            context_info = {
                "args": str(context.args) if hasattr(context, 'args') else 'N/A',
                "user_data": user_data_str,
                "chat_data": chat_data_str,
                "bot_data": bot_data_str,
            }
            context_str = json.dumps(context_info, indent=1, default=str)
        except Exception as ctx_err:
            context_str = f"Error getting context data: {ctx_err}"

        max_len_tb = 3000
        max_len_update = 500
        max_len_context = 300

        error_escaped = escape(str(context.error))
        error_type_escaped = escape(type(context.error).__name__)
        update_short = escape(update_str[:max_len_update] + ('...' if len(update_str) > max_len_update else ''))
        context_short = escape(context_str[:max_len_context] + ('...' if len(context_str) > max_len_context else ''))
        tb_short = escape(tb_string[-max_len_tb:])

        error_message = (
            convert_to_premium_emoji("⚠️ <b>Bot Error Encountered</b> ⚠️") + "\n\n" +
            f"<b>Error:</b>\n<pre>{error_escaped}</pre>\n"
            f"<b>Type:</b> <pre>{error_type_escaped}</pre>\n\n"
            f"<b>Update (limited):</b>\n<pre>{update_short}</pre>\n\n"
            f"<b>Context (limited):</b>\n<pre>{context_short}</pre>\n\n"
            f"<b>Traceback (end, limited):</b>\n<pre>{tb_short}</pre>"
        )

        max_msg_len = 4096
        if len(error_message) > max_msg_len:
            logger.warning(f"Error message length ({len(error_message)}) exceeds limit. Sending in parts.")
            for i in range(0, len(error_message), max_msg_len):
                chunk = error_message[i:i + max_msg_len]
                await context.bot.send_message(ADMIN_ID, chunk, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.5)
        else:
            await context.bot.send_message(ADMIN_ID, error_message, parse_mode=ParseMode.HTML)

    except Forbidden:
        logger.critical(f"CRITICAL: Cannot send error notification to admin {ADMIN_ID}. Bot might be blocked by the admin.")
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to send detailed error notification to admin: {e}", exc_info=True)
        try:
            fallback_msg = f"Bot encountered a critical error. Check logs immediately!\nError type: {type(context.error).__name__}\nError: {context.error}"
            await context.bot.send_message(ADMIN_ID, fallback_msg[:4090])
        except Exception as fallback_err:
            logger.critical(f"CRITICAL: Failed even the simplest error notification to admin. Fallback error: {fallback_err}")

# --- Background Task for Scheduled Processing ---

# Schedules that are running right now, as (user_id_str, schedule_key). Stops one schedule from starting twice.
RUNNING_SCHEDULES: set = set()
# Strong references to the running job tasks so they are not garbage collected mid-run.
BACKGROUND_SCHEDULE_TASKS: set = set()


def advance_schedule_after_run(user_id_str: str, schedule_key: str) -> None:
    """Records the run and moves next_run_time_iso to the next daily time / interval. Synchronous on purpose (no await between load and save)."""
    current_schedules = load_scheduled_files()
    info = current_schedules.get(user_id_str, {}).get(schedule_key)
    if not isinstance(info, dict):
        logger.info(f"Scheduler: Schedule '{schedule_key}' for user {user_id_str} was removed before run time update.")
        return

    last_run_time = datetime.now(timezone.utc)
    next_run_time = compute_next_run_time(info, last_run_time)
    if not next_run_time:
        logger.error(f"Scheduler: Cannot update next run for '{schedule_key}' (User {user_id_str}) - missing or invalid interval/daily time.")
        return

    info['last_run_time_iso'] = last_run_time.isoformat()
    info['next_run_time_iso'] = next_run_time.isoformat()
    if save_scheduled_files(current_schedules):
        logger.info(f"Scheduler: Updated next run time for '{schedule_key}' (User {user_id_str}) to {next_run_time.isoformat()}")
    else:
        logger.error("Scheduler: CRITICAL - Failed to save updated schedule run times!")


async def run_schedule_job(bot, user_id: int, schedule_key: str, schedule_info: dict, user_gh_configs: dict) -> None:
    """Runs one due schedule on its own, so a long job never delays the other schedules."""
    run_id = (str(user_id), schedule_key)
    advance = True
    try:
        await process_single_schedule(bot, user_id, schedule_key, schedule_info, user_gh_configs)
    except asyncio.CancelledError:
        advance = False  # bot is shutting down mid-run: keep the old time so the run is repeated after restart
        raise
    except Exception as job_err:
        logger.error(f"Scheduler: Error result returned from scheduled task '{schedule_key}' (User {user_id}): {job_err}", exc_info=True)
    finally:
        try:
            if advance:
                advance_schedule_after_run(str(user_id), schedule_key)
        except Exception as update_err:
            logger.error(f"Scheduler: Error updating schedule info for User {user_id}, Schedule {schedule_key}: {update_err}", exc_info=True)
        finally:
            RUNNING_SCHEDULES.discard(run_id)


async def run_scheduled_file_processor(application: Application) -> None:
    bot = application.bot
    logger.info(f"Background scheduler started. Check interval: {AUTO_PROCESS_CHECK_INTERVAL}s")
    await asyncio.sleep(15)

    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            logger.debug(f"Scheduler check running at {now_utc.isoformat()}")
            schedules = load_scheduled_files()
            if not schedules:
                logger.debug("Scheduler: No schedules found.")
                await asyncio.sleep(AUTO_PROCESS_CHECK_INTERVAL)
                continue

            started_jobs = 0

            for user_id_str in list(schedules.keys()):
                user_schedules = schedules.get(user_id_str)
                if not isinstance(user_schedules, dict):
                    logger.warning(f"Scheduler: Invalid schedule data format for user {user_id_str}. Skipping.")
                    continue

                try:
                    user_id = int(user_id_str)
                except ValueError:
                    logger.warning(f"Scheduler: Invalid user ID key '{user_id_str}' in schedules. Skipping.")
                    continue

                if not is_user_vip(user_id):
                    logger.info(f"Scheduler: User {user_id} is no longer VIP. Skipping their schedules.")
                    continue

                user_gh_configs = get_user_github_configs(user_id_str)

                for schedule_name in list(user_schedules.keys()):
                    schedule_info = user_schedules.get(schedule_name)
                    if not isinstance(schedule_info, dict):
                        logger.warning(f"Scheduler: Invalid schedule entry '{schedule_name}' for user {user_id}. Skipping.")
                        continue

                    next_run_iso = schedule_info.get('next_run_time_iso')
                    stored_file_path = schedule_info.get('stored_file_path')
                    interval_seconds = schedule_info.get('interval_seconds')

                    if not next_run_iso or not stored_file_path or not interval_seconds:
                        logger.warning(f"Scheduler: Skipping invalid schedule '{schedule_name}' for user {user_id} (missing essential info).")
                        continue

                    try:
                        next_run_dt = datetime.fromisoformat(next_run_iso.replace('Z', '+00:00'))
                    except (ValueError, TypeError):
                        logger.warning(f"Scheduler: Skipping schedule '{schedule_name}' for user {user_id} due to invalid next_run_time_iso: {next_run_iso}")
                        continue

                    run_id = (user_id_str, schedule_name)
                    if next_run_dt <= now_utc and run_id not in RUNNING_SCHEDULES:
                        logger.info(f"Scheduler: Schedule '{schedule_name}' for user {user_id} is due. Starting job.")
                        if os.path.exists(stored_file_path):
                            RUNNING_SCHEDULES.add(run_id)
                            job_task = asyncio.create_task(
                                run_schedule_job(bot, user_id, schedule_name, schedule_info, user_gh_configs)
                            )
                            BACKGROUND_SCHEDULE_TASKS.add(job_task)
                            job_task.add_done_callback(BACKGROUND_SCHEDULE_TASKS.discard)
                            started_jobs += 1
                        else:
                            logger.error(f"Scheduler: Stored file missing for due schedule '{schedule_name}' user {user_id} at path {stored_file_path}.")
                            try:
                                await bot.send_message(
                                    user_id,
                                    convert_to_premium_emoji(f"⚠️ Error: Could not run scheduled task <code>'{escape(schedule_info.get('user_schedule_name', schedule_name))}'</code>. The associated data file seems to be missing. Please use <code>/setfile</code> again."),
                                    parse_mode=ParseMode.HTML
                                )
                            except Exception as notify_err:
                                logger.error(f"Scheduler: Failed to notify user {user_id} about missing schedule file: {notify_err}")

            if started_jobs:
                logger.info(f"Scheduler: Started {started_jobs} due schedule(s).")
            else:
                logger.debug("Scheduler: No schedules were due this cycle.")

        except Exception as loop_err:
            logger.critical(f"Scheduler: Unhandled exception in main processing loop: {loop_err}", exc_info=True)
            await asyncio.sleep(60)

        await asyncio.sleep(AUTO_PROCESS_CHECK_INTERVAL)


async def process_single_schedule(
    bot,
    user_id: int,
    schedule_name: str,
    schedule_info: dict,
    github_configs_for_user: dict | None
) -> tuple[str, str, bool]:
    user_id_str = str(user_id)
    stored_file_path = schedule_info.get('stored_file_path')
    user_display_name = schedule_info.get('user_schedule_name', schedule_name)
    bound_repo = schedule_info.get('github_repo')
    target_filename_override = schedule_info.get('github_target_filename')
    if bound_repo:
        user_display_name = f"{user_display_name} [{bound_repo}]"
    github_upload_status = False

    log_prefix = f"AutoProcess User {user_id} Schedule '{schedule_name}':"
    logger.info(f"{log_prefix} Starting.")

    notify_parts = [convert_to_premium_emoji(f"⚙️ Auto-processing started for schedule <code>'{escape(user_display_name)}'</code>...")]
    status_msg_obj = None
    try:
        status_msg_obj = await bot.send_message(user_id, notify_parts[0], parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"{log_prefix} Failed to send initial status DM: {e}")

    accounts_data = []
    run_timestamp = int(time.time())
    temp_results_dir = os.path.join(TEMP_DIR, f"auto_{user_id}_{schedule_storage_name(schedule_name)}_{run_timestamp}")
    cleanup_paths_auto = [temp_results_dir]
    jwt_token_path_for_upload = None

    try:
        if not stored_file_path or not os.path.exists(stored_file_path):
            raise FileNotFoundError(f"Stored file path missing or file not found: {stored_file_path}")

        await update_schedule_status(bot, status_msg_obj, notify_parts, "Reading stored file...")

        with open(stored_file_path, 'r', encoding='utf-8') as f:
            try:
                accounts_data = json.load(f)
                if not isinstance(accounts_data, list):
                    raise ValueError("Scheduled file content must be a JSON list `[...]`.")
                if accounts_data and not all(isinstance(item, dict) for item in accounts_data):
                    first_bad = next((x for x in accounts_data if not isinstance(x, dict)), None)
                    raise ValueError(f"All items in the list must be JSON objects (`{{...}}`). Found: {type(first_bad)}")
                logger.info(f"{log_prefix} Read {len(accounts_data)} entries from {stored_file_path}")
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON format in stored file: {e.msg} (Line: {e.lineno}, Col: {e.colno})")
            except ValueError as ve:
                raise ve
            except Exception as read_err:
                raise IOError(f"Could not read stored file: {read_err}")

        total_count = len(accounts_data)
        if total_count == 0:
            logger.info(f"{log_prefix} Stored file is empty. No processing needed.")
            await update_schedule_status(bot, status_msg_obj, notify_parts, "✅ Finished: Stored file was empty.")
            return user_id_str, schedule_name, True

        await update_schedule_status(bot, status_msg_obj, notify_parts, f"Processing {total_count} entries via API (ordered)...")

        start_time = time.time()
        successful_tokens = []
        working_accounts = []
        lost_accounts = []
        errors_summary = defaultdict(int)

        # count = accounts finished, success/failed = live counters shown to the user
        progress_auto = {"count": 0, "success": 0, "failed": 0}
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async def tracked_auto_task(account_item):
            result = await process_account(session_auto, account_item, semaphore)
            progress_auto["count"] += 1
            if is_failed_result(result):
                progress_auto["failed"] += 1
            else:
                progress_auto["success"] += 1
            return result

        async with aiohttp.ClientSession() as session_auto:
            auto_tasks = [tracked_auto_task(account) for account in accounts_data]

            async def auto_progress_updater():
                # Refreshes the status message every PROGRESS_UPDATE_SECONDS (default 5s).
                while True:
                    await asyncio.sleep(PROGRESS_UPDATE_SECONDS)
                    await update_schedule_status(
                        bot, status_msg_obj, notify_parts,
                        build_auto_progress_text(
                            "⚡ <b>Processing accounts...</b>",
                            progress_auto["count"], total_count,
                            progress_auto["success"], progress_auto["failed"], start_time
                        )
                    )

            await update_schedule_status(
                bot, status_msg_obj, notify_parts,
                build_auto_progress_text("⚡ <b>Processing accounts...</b>", 0, total_count, 0, 0, start_time)
            )
            auto_prog_task = asyncio.create_task(auto_progress_updater())
            ordered_results = await asyncio.gather(*auto_tasks, return_exceptions=True)
            auto_prog_task.cancel()
            try:
                await auto_prog_task
            except asyncio.CancelledError:
                pass

            # Final update of this phase: always show the full 100% (count == total) with the final success/failed numbers
            await update_schedule_status(
                bot, status_msg_obj, notify_parts,
                build_auto_progress_text(
                    "🏁 <b>First pass complete</b>",
                    total_count, total_count,
                    progress_auto["success"], progress_auto["failed"], start_time
                )
            )

            # --- AUTO RETRY LOGIC START ---
            ordered_results = list(ordered_results)
            for retry_round in range(1, MAX_RETRY_ROUNDS + 1):
                failed_idx = [i for i, r in enumerate(ordered_results) if is_failed_result(r)]
                if not failed_idx:
                    break

                logger.info(f"{log_prefix} {len(failed_idx)} account(s) failed. Retry round {retry_round}/{MAX_RETRY_ROUNDS} in {RETRY_DELAY_SECONDS}s.")
                await update_schedule_status(
                    bot, status_msg_obj, notify_parts,
                    build_auto_progress_text(
                        f"🔄 <b>{len(failed_idx)} failed</b> - Retry {retry_round}/{MAX_RETRY_ROUNDS} in {RETRY_DELAY_SECONDS}s...",
                        total_count, total_count,
                        progress_auto["success"], progress_auto["failed"], start_time
                    )
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)

                retry_done = {"count": 0}

                async def auto_retry_task(acc, retry_done=retry_done):
                    res = await process_account(session_auto, acc, semaphore)
                    retry_done["count"] += 1
                    # A recovered account moves from "failed" to "successful" in the live counters
                    if not is_failed_result(res):
                        progress_auto["success"] += 1
                        progress_auto["failed"] -= 1
                    return res

                async def auto_retry_progress_updater(retry_done=retry_done, retry_round=retry_round, retry_total=len(failed_idx)):
                    while True:
                        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)
                        await update_schedule_status(
                            bot, status_msg_obj, notify_parts,
                            build_auto_progress_text(
                                f"🔄 <b>Retry {retry_round}/{MAX_RETRY_ROUNDS}</b> ({retry_done['count']}/{retry_total} retried)",
                                total_count, total_count,
                                progress_auto["success"], progress_auto["failed"], start_time
                            )
                        )

                await update_schedule_status(
                    bot, status_msg_obj, notify_parts,
                    build_auto_progress_text(
                        f"🔄 <b>Retry {retry_round}/{MAX_RETRY_ROUNDS}</b> (0/{len(failed_idx)} retried)",
                        total_count, total_count,
                        progress_auto["success"], progress_auto["failed"], start_time
                    )
                )
                retry_prog_task_auto = asyncio.create_task(auto_retry_progress_updater())
                retry_results_auto = await asyncio.gather(
                    *[auto_retry_task(accounts_data[i]) for i in failed_idx],
                    return_exceptions=True
                )
                retry_prog_task_auto.cancel()
                try:
                    await retry_prog_task_auto
                except asyncio.CancelledError:
                    pass
                for i, res in zip(failed_idx, retry_results_auto):
                    ordered_results[i] = res

                still_failed = sum(1 for i in failed_idx if is_failed_result(ordered_results[i]))
                logger.info(f"{log_prefix} Retry round {retry_round} done. Recovered: {len(failed_idx) - still_failed}, still failing: {still_failed}")
                await update_schedule_status(
                    bot, status_msg_obj, notify_parts,
                    build_auto_progress_text(
                        f"🏁 <b>Retry {retry_round}/{MAX_RETRY_ROUNDS} complete</b> (♻️ recovered {len(failed_idx) - still_failed}/{len(failed_idx)})",
                        total_count, total_count,
                        progress_auto["success"], progress_auto["failed"], start_time
                    )
                )
            # --- AUTO RETRY LOGIC END ---

        for i, result in enumerate(ordered_results):
            if isinstance(result, Exception):
                error_msg = f"Internal task error: {result}"
                logger.error(f"{log_prefix} Error in task for account index {i}: {result}", exc_info=True)
                lost_entry = {**accounts_data[i], "error_reason": error_msg} if i < len(accounts_data) else {"error_reason": error_msg}
                lost_accounts.append(lost_entry)
                errors_summary["Internal task error"] += 1
                continue

            token, region, working_acc, lost_acc, error_reason = result
            if token and working_acc:
                successful_tokens.append({"token": token, "region": region})
                working_accounts.append(working_acc)
            elif lost_acc:
                lost_accounts.append(lost_acc)
                reason = lost_acc.get("error_reason", "Unknown")
                errors_summary[reason.split(':')[0].strip()] += 1
            else:
                lost_accounts.append({"error_reason": "Unexpected process_account result"})
                errors_summary["Processing function error"] += 1

        processing_time = time.time() - start_time
        logger.info(f"{log_prefix} API processing finished in {processing_time:.2f}s. Success: {len(successful_tokens)}, Failed: {len(lost_accounts)}")
        notify_parts.append(convert_to_premium_emoji(
            f"📊 <b>Final API Results:</b> ✅ {len(successful_tokens)} successful | ❌ {len(lost_accounts)} failed | 📁 {total_count} total ({format_time(processing_time)})"
        ))
        if errors_summary:
            top_errors = sorted(errors_summary.items(), key=lambda item: item[1], reverse=True)
            error_snippets = []
            for err_msg, count in top_errors[:2]:
                error_snippets.append(f"<code>{escape(err_msg)}</code> ({count})")
            notify_parts.append(convert_to_premium_emoji(f"   ⚠️ (Top errors: {'; '.join(error_snippets)})"))
        # Placeholder line: the next status updates replace THIS line, so the results above are never overwritten
        notify_parts.append(convert_to_premium_emoji("⏳ Finalizing..."))

        if successful_tokens:
            await update_schedule_status(bot, status_msg_obj, notify_parts, "Preparing token file for upload...")
            os.makedirs(temp_results_dir, exist_ok=True)
            jwt_token_path_for_upload = os.path.join(temp_results_dir, 'jwt_token_auto.json')
            tokens_only_list = [{"token": entry["token"]} for entry in successful_tokens if entry.get("token")]
            if tokens_only_list:
                if not save_json_data(jwt_token_path_for_upload, tokens_only_list):
                    jwt_token_path_for_upload = None
                    raise IOError("Failed to save temporary token file for upload.")
                logger.info(f"{log_prefix} Saved {len(tokens_only_list)} tokens (ordered) to {jwt_token_path_for_upload}")
            else:
                jwt_token_path_for_upload = None
                logger.info(f"{log_prefix} No valid tokens found to save for upload.")
                notify_parts.append(convert_to_premium_emoji("ℹ️ No valid tokens generated, skipping GitHub upload."))
                github_upload_status = True
        else:
            logger.info(f"{log_prefix} No successful tokens generated. Skipping GitHub upload prep.")
            notify_parts.append(convert_to_premium_emoji("ℹ️ No successful tokens, skipping GitHub upload."))
            github_upload_status = True

        missing_config_notice = None
        if bound_repo:
            selected_github_configs = pick_github_configs_for_repo(github_configs_for_user or {}, bound_repo, target_filename_override)
        else:
            selected_github_configs = github_configs_for_user or {}

        if jwt_token_path_for_upload and selected_github_configs:
            upload_target_text = f" ({target_filename_override})" if target_filename_override else ""
            await update_schedule_status(bot, status_msg_obj, notify_parts, f"Uploading{upload_target_text} to {len(selected_github_configs)} GitHub repo(s)...")
            all_upload_success = True
            for repo_key, config in selected_github_configs.items():
                if config and isinstance(config, dict):
                    upload_config = dict(config)
                    if target_filename_override:
                        upload_config['github_filename'] = target_filename_override
                    upload_success_single = await upload_to_github_background(
                        bot, user_id, jwt_token_path_for_upload, upload_config
                    )
                    if not upload_success_single:
                        all_upload_success = False
                    logger.info(f"{log_prefix} GitHub upload for repo '{repo_key}' as '{upload_config.get('github_filename')}': {'Success' if upload_success_single else 'Failed'}")
                else:
                    logger.warning(f"{log_prefix} Invalid GitHub config for repo key '{repo_key}'. Skipping.")
            github_upload_status = all_upload_success
        elif jwt_token_path_for_upload and bound_repo:
            logger.warning(f"{log_prefix} Tokens generated but GitHub config for '{bound_repo}' no longer exists. Skipping upload.")
            missing_config_notice = f"⚠️ Auto-processing completed, but GitHub upload was skipped: config for <code>{escape(bound_repo)}</code> not found. Add it again with /setgithub."
            github_upload_status = False
        elif jwt_token_path_for_upload:
            logger.info(f"{log_prefix} Tokens generated but no GitHub config set. Skipping upload.")
            notify_parts.append(convert_to_premium_emoji("ℹ️ GitHub upload skipped (no config). Use /mygithub &amp; /setgithub."))
            await update_schedule_status(bot, status_msg_obj, notify_parts, "Skipped GitHub upload (no config).")
            github_upload_status = True

        finished_at_text = format_ist(datetime.now(timezone.utc), with_seconds=True, show_utc=True)
        final_status_line = convert_to_premium_emoji(
            f"🏁 <b>Auto-processing completed (100%)</b>\n"
            f"✅ Successful: {len(successful_tokens)} | ❌ Failed: {len(lost_accounts)} | 📁 Total: {total_count}\n"
            f"⏰ Finished: {escape(finished_at_text)}"
        )
        if jwt_token_path_for_upload and not github_upload_status:
            final_status_line = missing_config_notice or convert_to_premium_emoji(
                f"⚠️ Auto-processing completed (100%), but one or more GitHub uploads failed.\n"
                f"✅ Successful: {len(successful_tokens)} | ❌ Failed: {len(lost_accounts)} | 📁 Total: {total_count}\n"
                f"⏰ Finished: {escape(finished_at_text)}"
            )

        await update_schedule_status(bot, status_msg_obj, notify_parts, final_status_line, is_final=True)
        logger.info(f"{log_prefix} Finished. Overall Success: {github_upload_status or (not jwt_token_path_for_upload)}")

        final_success_state = github_upload_status or (not jwt_token_path_for_upload)

    except Exception as e:
        logger.error(f"{log_prefix} FAILED: {e}", exc_info=True)
        final_success_state = False
        try:
            error_msg = convert_to_premium_emoji(f"❌ <b>FAILED:</b> Auto-processing for schedule <code>'{escape(user_display_name)}'</code> encountered an error:\n<code>{escape(str(e))}</code>")
            if status_msg_obj:
                await update_schedule_status(bot, status_msg_obj, notify_parts, error_msg, is_final=True)
            else:
                await bot.send_message(user_id, error_msg, parse_mode=ParseMode.HTML)
        except Exception as notify_err:
            logger.error(f"{log_prefix} Could not notify user about processing failure: {notify_err}")

    finally:
        for path in cleanup_paths_auto:
            if os.path.exists(path):
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                    logger.debug(f"{log_prefix} Cleaned up temp path: {path}")
                except OSError as e:
                    logger.warning(f"{log_prefix} Could not clean up temp path {path}: {e}")

    return user_id_str, schedule_name, final_success_state


async def update_schedule_status(bot, status_msg_obj, notify_parts: list, new_status: str, keep_last=True, is_final=False):
    if not status_msg_obj: return

    if keep_last and len(notify_parts) > 1:
        notify_parts[-1] = new_status
    else:
        notify_parts.append(new_status)

    max_lines = 12
    if len(notify_parts) > max_lines:
        notify_parts = [notify_parts[0]] + notify_parts[-(max_lines-1):]
        if notify_parts[1] != "...": notify_parts.insert(1, "...")

    message_text = "\n".join(notify_parts)

    try:
        await bot.edit_message_text(
            chat_id=status_msg_obj.chat_id,
            message_id=status_msg_obj.message_id,
            text=convert_to_premium_emoji(message_text[:4096]),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        if "Message is not modified" not in str(e) and "message to edit not found" not in str(e).lower():
            logger.warning(f"AutoProcess: Failed to edit status message {status_msg_obj.message_id}: {e}")
        if "message to edit not found" in str(e).lower():
            logger.warning(f"AutoProcess: Status message {status_msg_obj.message_id} was deleted by user?")
            status_msg_obj = None


# --- Main Application Setup ---

async def main() -> None:
    global ADMIN_ID, TOKEN

    print("\n--- Initializing Bot ---")

    if not TOKEN or TOKEN == "YOUR_FALLBACK_BOT_TOKEN":
        print("\n" + "="*60)
        print(" FATAL ERROR: TELEGRAM_BOT_TOKEN is missing or invalid.")
        print(" Please set the TELEGRAM_BOT_TOKEN environment variable.")
        print(" -> Exiting.")
        print("="*60 + "\n")
        exit(1)
    elif len(TOKEN.split(':')) != 2:
        print("\n" + "="*60)
        print(f" FATAL ERROR: TELEGRAM_BOT_TOKEN format looks incorrect. Should be 'ID:SECRET'.")
        print(" -> Exiting.")
        print("="*60 + "\n")
        exit(1)

    try:
        admin_id_env = os.getenv('ADMIN_ID')
        if admin_id_env and admin_id_env.isdigit():
            ADMIN_ID = int(admin_id_env)
            if ADMIN_ID == 0: print(" WARNING: ADMIN_ID is set to 0. Admin features disabled.")
            else: logger.info(f"Admin User ID configured: {ADMIN_ID} (Auto-VIP: Enabled)")
        else:
            if isinstance(ADMIN_ID, int) and ADMIN_ID != 0: print(f" WARNING: ADMIN_ID not set or invalid in environment. Using script default: {ADMIN_ID}")
            else:
                ADMIN_ID = 0
                print(" WARNING: ADMIN_ID not set/invalid/0. Setting to 0. Admin features disabled.")
    except Exception as e:
        ADMIN_ID = 0
        print(f" WARNING: Error processing ADMIN_ID from environment ({e}). Setting to 0. Admin features disabled.")

    if not API_BASE_URL: logger.warning("JWT_API_URL not set, using default new API.")
    else: logger.info(f"Using UID/Password API: {API_BASE_URL}")
    logger.info(f"Using Access Token API: {ACCESS_TOKEN_API_URL}")

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        os.makedirs(SCHEDULED_FILES_DATA_DIR, exist_ok=True)
        logger.info(f"Data Directory: {DATA_DIR}")
        logger.info(f"Temp Directory: {TEMP_DIR}")
        logger.info(f"Scheduled Files Storage: {SCHEDULED_FILES_DATA_DIR}")
    except OSError as e:
        print(f"\nFATAL ERROR: Cannot create required directories: {e}\n-> Exiting.")
        exit(1)

    app_builder = Application.builder().token(TOKEN)\
        .concurrent_updates(True) \
        .read_timeout(30) \
        .write_timeout(30) \
        .connect_timeout(30) \
        .pool_timeout(60) \
        .get_updates_read_timeout(40) \
        .get_updates_pool_timeout(70)

    application = app_builder.build()

    private_chat_filter = filters.ChatType.PRIVATE

    application.add_handler(CommandHandler("start", start, filters=private_chat_filter))
    application.add_handler(CommandHandler("help", help_command, filters=private_chat_filter))
    application.add_handler(MessageHandler(filters.Regex(f"^{re.escape(COMMAND_BUTTONS_LAYOUT[2][1])}$") & private_chat_filter, help_command))

    application.add_handler(CommandHandler("vipstatus", vip_status_command, filters=private_chat_filter))
    application.add_handler(MessageHandler(filters.Regex(f"^{re.escape(COMMAND_BUTTONS_LAYOUT[0][1])}$") & private_chat_filter, vip_status_command))
    application.add_handler(CommandHandler("vipshop", vip_shop_command, filters=private_chat_filter))
    application.add_handler(MessageHandler(filters.Regex(f"^{re.escape(COMMAND_BUTTONS_LAYOUT[1][0])}$") & private_chat_filter, vip_shop_command))

    application.add_handler(CommandHandler("setgithub", set_github_direct, filters=private_chat_filter))
    application.add_handler(CommandHandler("removegithub", remove_github_config, filters=private_chat_filter))
    application.add_handler(CommandHandler("mygithub", my_github_config, filters=private_chat_filter))
    application.add_handler(MessageHandler(filters.Regex(f"^{re.escape(COMMAND_BUTTONS_LAYOUT[1][1])}$") & private_chat_filter, my_github_config))

    application.add_handler(CommandHandler("setfile", set_scheduled_file_start, filters=private_chat_filter))
    application.add_handler(CommandHandler("removefile", remove_scheduled_file, filters=private_chat_filter))
    application.add_handler(CommandHandler("scheduledfiles", list_scheduled_files, filters=private_chat_filter))
    application.add_handler(MessageHandler(filters.Regex(f"^{re.escape(COMMAND_BUTTONS_LAYOUT[2][0])}$") & private_chat_filter, list_scheduled_files))

    application.add_handler(MessageHandler(filters.Text(COMMAND_BUTTONS_LAYOUT[0][0]) & private_chat_filter, handle_document))
    application.add_handler(MessageHandler(
        (filters.Document.MimeType('application/json') | filters.Document.FileExtension('json')) & private_chat_filter,
        handle_document
    ))

    application.add_handler(CommandHandler("cancel", cancel, filters=private_chat_filter))
    application.add_handler(MessageHandler(filters.Regex(f"^{re.escape(COMMAND_BUTTONS_LAYOUT[3][0])}$") & private_chat_filter, cancel))

    # Inline button callback handler
    application.add_handler(CallbackQueryHandler(inline_button_callback))

    if ADMIN_ID and ADMIN_ID != 0:
        admin_filter = filters.User(user_id=ADMIN_ID) & private_chat_filter
        application.add_handler(CommandHandler("vip", vip_management, filters=admin_filter))
        application.add_handler(CommandHandler("broadcast", broadcast, filters=admin_filter))
        logger.info(f"Admin commands enabled for ADMIN_ID: {ADMIN_ID}.")
    else:
        logger.warning("Admin commands are disabled as ADMIN_ID is not set, 0, or invalid.")

    if ADMIN_ID and ADMIN_ID != 0:
        known_button_texts_set = {btn for row in COMMAND_BUTTONS_LAYOUT for btn in row}
        forwarding_filters = (
            private_chat_filter &
            ~filters.User(user_id=ADMIN_ID) &
            ~filters.COMMAND &
            ~filters.Text(known_button_texts_set) &
            ~(filters.Document.MimeType('application/json') | filters.Document.FileExtension('json')) &
            (filters.TEXT | filters.PHOTO | filters.Sticker.ALL | filters.VIDEO | filters.VIDEO_NOTE | filters.VOICE | filters.AUDIO | filters.Document.ALL) &
            ~filters.UpdateType.EDITED_MESSAGE
        )
        application.add_handler(MessageHandler(forwarding_filters, forward_to_admin))
        logger.info("Message forwarding to admin enabled.")
    else:
        logger.warning("Message forwarding to admin is disabled as ADMIN_ID is not set or invalid.")

    application.add_error_handler(error_handler)

    logger.info("🤖 Bot is initializing and connecting to Telegram...")
    print("\n" + "="*60)
    print(" 🚀 Advanced JWT Token Bot with File Scheduling is starting...")

    try:
        await application.initialize()

        bot_info = await application.bot.get_me()
        print(f" ✔️ Bot Username: @{bot_info.username} (ID: {bot_info.id})")
        print(f" ✔️ Admin ID: {ADMIN_ID if (ADMIN_ID and ADMIN_ID != 0) else 'Not Set (Admin Features Disabled)'}")
        print(f" ✔️ Admin Auto-VIP: {'Enabled' if (ADMIN_ID and ADMIN_ID != 0) else 'Disabled'}")
        print(f" ✔️ New UID/Pass API: {API_BASE_URL}")
        print(f" ✔️ New Access Token API: {ACCESS_TOKEN_API_URL}")
        print(f" ✔️ Data Directory: {DATA_DIR}")
        print(f" ✔️ Scheduled File Check Interval: {AUTO_PROCESS_CHECK_INTERVAL}s")
        print(f" ✔️ Multi GitHub Config: Enabled (multiple repos per user)")
        print(f" ✔️ Order Preservation: Enabled (output = input order)")

        scheduler_task = asyncio.create_task(run_scheduled_file_processor(application))
        logger.info("Background scheduler task created.")

        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        print("\n Bot is now polling for updates. Press Ctrl+C to stop.")
        print("="*60 + "\n")

        await scheduler_task

    except (TelegramError, ConnectionError) as e:
        print("\n" + "="*60)
        print(f" FATAL ERROR: Could not connect to Telegram or initialize bot.")
        print(f" Error: {e}")
        print(" Please check your network connection and bot token.")
        print(" -> Exiting.")
        print("="*60 + "\n")
        logger.critical(f"Failed to initialize or start polling: {e}", exc_info=True)
        exit(1)
    except asyncio.CancelledError:
        logger.info("Main task or scheduler was cancelled.")
    except Exception as e:
        print("\n" + "="*60)
        print(f" FATAL ERROR: An unexpected error occurred during bot startup or main loop.")
        print(f" Error: {e}")
        print(" -> Exiting.")
        print("="*60 + "\n")
        logger.critical(f"Unhandled exception during startup/runtime: {e}", exc_info=True)
        exit(1)
    finally:
        if 'application' in locals() and application.running:
            logger.info("Attempting graceful shutdown...")
            await application.stop()
            await application.shutdown()
            logger.info("Application stopped.")
        if 'scheduler_task' in locals() and not scheduler_task.done():
            logger.info("Cancelling scheduler task...")
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                logger.info("Scheduler task cancelled successfully.")
            except Exception as task_err:
                logger.error(f"Error during scheduler task cancellation/await: {task_err}")
        logger.info("Shutdown complete.")


if __name__ == '__main__':
    keep_alive()

    RESTART_DELAY = int(os.getenv('RESTART_DELAY', 10))
    restart_count = 0

    while True:
        try:
            if not TOKEN or TOKEN == "YOUR_FALLBACK_BOT_TOKEN":
                print("FATAL: TELEGRAM_BOT_TOKEN is not set. Please configure it before running.")
                break
            else:
                if restart_count > 0:
                    print(f"\n♻️ Restarting bot (attempt #{restart_count})...")
                    logger.info(f"Auto-restart attempt #{restart_count} starting.")
                asyncio.run(main())
                print("\n⚠️ Bot main loop exited normally. Restarting to stay online...")
                logger.warning("main() returned; auto-restarting.")
        except KeyboardInterrupt:
            print("\n-- Bot stopping due to Ctrl+C --")
            logger.info("Bot stopped manually via KeyboardInterrupt.")
            break
        except SystemExit as se:
            print(f"\n⚠️ Bot exited (code {se.code}). Auto-restarting...")
            logger.error(f"SystemExit caught (code {se.code}); auto-restarting.")
        except Exception as e:
            print(f"\n💥 A critical unhandled exception occurred outside the main asyncio loop: {e}")
            logger.critical(f"Critical unhandled exception in __main__: {e}", exc_info=True)
            traceback.print_exc()

        restart_count += 1
        try:
            print(f"⏳ Waiting {RESTART_DELAY}s before restart...")
            time.sleep(RESTART_DELAY)
        except KeyboardInterrupt:
            print("\n-- Bot stopping due to Ctrl+C --")
            break