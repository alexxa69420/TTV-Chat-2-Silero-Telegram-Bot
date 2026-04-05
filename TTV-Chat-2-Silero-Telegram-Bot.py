# --- START OF FILE app.py ---
import os
import threading
import asyncio
import urllib.parse
import logging
import base64
import tempfile
import re
import ssl
import time
import json
import sqlite3
import shutil
from collections import deque
from datetime import datetime
from io import BytesIO
from pathlib import Path

# Audio playback
try:
    import pygame
    USE_PYGAME = True
except ImportError:
    USE_PYGAME = False
    try:
        from playsound import playsound
        USE_PLAYSOUND = True
    except ImportError:
        USE_PLAYSOUND = False

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pydub import AudioSegment
from num2words import num2words
from pymystem3 import Mystem


def has_ffmpeg_tools():
    return bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))

# =============================================================================
# 📌 БАЗОВЫЕ ПУТИ (Гарантирует создание файлов рядом со скриптом)
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
ENV_FILE = BASE_DIR / ".env"
USER_PREFS_DB_FILE = BASE_DIR / "user_prefs.db"

# =============================================================================
# 🎯 ШАГ 1: РАННИЙ ЛОГГЕР (до загрузки конфига!)
# =============================================================================
def setup_early_logger():
    early_logger = logging.getLogger("early")
    if not early_logger.handlers:
        early_logger.setLevel(logging.INFO)
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter('%(message)s'))
        early_logger.addHandler(h)
    return early_logger

_early_log = setup_early_logger()

# =============================================================================
# 📄 КОНФИГ: загрузка из config.json с авто-созданием дефолтов
# =============================================================================
def load_config():
    defaults = {
        "voices":["bandit", "arthas", "rexxar","geralt",],
        "blacklist_users":["streamelements", "nightbot", "jeetbot", "cassette_player69419", "cassette_player69420", "alexxa69419"],
        "blacklist_phrases": ["!command", "!discord", "https://"],
        "settings": {
            "target_bot": "silero_voice_bot",
            "response_timeout": 4,
            "audio_volume": 0.7,
            "default_voice": "bandit",
            "flask_host": "127.0.0.1",
            "flask_port": 8124,
            "only_highlighted": False,
            "ignore_english": True,
            "max_message_length": 350
        }
    }
    
    if not CONFIG_FILE.exists():
        _early_log.warning(f"⚠️ {CONFIG_FILE.name} не найден. Создаю с дефолтами.")
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(defaults, f, indent=4, ensure_ascii=False)
        return defaults.copy()
    
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        needs_save = False
        
        # Валидация voices
        if "voices" not in config or not isinstance(config["voices"], list) or not config["voices"]:
            _early_log.warning("⚠️ Нет валидного 'voices'. Восстанавливаю дефолты.")
            config["voices"] = defaults["voices"]
            needs_save = True
        
        # Merge с дефолтами
        for key in defaults:
            if key not in config:
                config[key] = defaults[key]
                needs_save = True
                
        if "settings" not in config or not isinstance(config["settings"], dict):
            _early_log.warning("⚠️ Нет валидного 'settings'. Восстанавливаю дефолты.")
            config["settings"] = defaults["settings"].copy()
            needs_save = True
        else:
            for k, v in defaults["settings"].items():
                if k not in config["settings"]:
                    config["settings"][k] = v
                    needs_save = True
        
        if needs_save:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            _early_log.info(f"💾 В {CONFIG_FILE.name} дописаны недостающие параметры.")
        
        _early_log.info(f"✅ Конфиг загружен из {CONFIG_FILE.name}")
        return config
        
    except json.JSONDecodeError as e:
        _early_log.error(f"❌ JSON ошибка: {e}")
        _early_log.warning("🔄 Восстанавливаю дефолты...")
        backup = CONFIG_FILE.with_suffix(".json.broken")
        if CONFIG_FILE.exists():
            CONFIG_FILE.rename(backup)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(defaults, f, indent=4, ensure_ascii=False)
        return defaults.copy()
        
    except Exception as e:
        _early_log.error(f"❌ Ошибка: {e}")
        _early_log.warning("🔄 Возвращаю дефолты...")
        return defaults.copy()

# 🔹 Загружаем конфиг
CONFIG = load_config()

# =============================================================================
# 🎯 ШАГ 2: ПОЛНЫЙ ЛОГГЕР
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(BASE_DIR / 'tts_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# ⚙️ ПРИМЕНЕНИЕ НАСТРОЕК
# =============================================================================
load_dotenv(ENV_FILE)

def _normalize_twitch_channel(value):
    """Twitch IRC шлёт #канал в нижнем регистре; несовпадение регистра давало 0 сообщений в логах."""
    if not value:
        return None
    s = str(value).strip()
    if s.startswith("#"):
        s = s[1:]
    return s.lower() or None


def _normalize_twitch_login(value):
    if not value:
        return None
    return str(value).strip().lower() or None


# Секреты
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_NAME = str(BASE_DIR / "tts_session")
TWITCH_USERNAME = _normalize_twitch_login(os.getenv("TWITCH_USERNAME"))
TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")
TWITCH_CHANNEL = _normalize_twitch_channel(os.getenv("TWITCH_CHANNEL"))

if TWITCH_CHANNEL:
    logger.info(f"📋 Twitch IRC: канал #{TWITCH_CHANNEL} (имя канала приведено к нижнему регистру для IRC)")

# Настройки
SETTINGS = CONFIG.get("settings", {})
TARGET_BOT_USERNAME = SETTINGS.get("target_bot", "silero_voice_bot")
RESPONSE_TIMEOUT = int(SETTINGS.get("response_timeout", 45))
AUDIO_VOLUME = float(SETTINGS.get("audio_volume", 1.0))
FLASK_HOST = SETTINGS.get("flask_host", "127.0.0.1")
FLASK_PORT = int(SETTINGS.get("flask_port", 8124))
HAS_FFMPEG = has_ffmpeg_tools()

# Голоса и блеклисты
VOICE_PREFIXES = CONFIG["voices"]
BLACKLIST_USERS = set(u.lower() for u in CONFIG.get("blacklist_users",[]))
BLACKLIST_PHRASES = CONFIG.get("blacklist_phrases",[])

# =============================================================================
# 🌐 ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
# =============================================================================
pyrogram_client = None
TARGET_BOT_ID = None
twitch_reader = None
twitch_writer = None
twitch_connected = False
_twitch_lock = asyncio.Lock()
_bot_shutting_down = False
# Строки, которые мы сами отправили в чат (чтобы не озвучивать ответы бота при том же NICK, что и стример)
_twitch_recent_outbox = deque()
_TWITCH_OUTBOX_TTL_SEC = 25.0
_TWITCH_OUTBOX_MAX = 48


def _prune_twitch_outbox(now):
    while _twitch_recent_outbox and now - _twitch_recent_outbox[0][0] > _TWITCH_OUTBOX_TTL_SEC:
        _twitch_recent_outbox.popleft()


def _register_twitch_outgoing(text):
    now = time.time()
    _prune_twitch_outbox(now)
    _twitch_recent_outbox.append((now, text.strip()))
    while len(_twitch_recent_outbox) > _TWITCH_OUTBOX_MAX:
        _twitch_recent_outbox.popleft()


def _is_echo_of_our_chat_reply(message_text):
    now = time.time()
    _prune_twitch_outbox(now)
    m = message_text.strip()
    for _, sent in _twitch_recent_outbox:
        if sent == m:
            return True
    return False


synthesis_queue = asyncio.Queue()
synthesis_semaphore = asyncio.Semaphore(1)
_audio_play_lock = threading.Lock()

# Два события: одно для ожидания ответа бота, другое для завершения воспроизведения
bot_response_event = asyncio.Event()
playback_done_event = asyncio.Event()

# Хранилище ID последних удалённых сообщений
deleted_messages = deque(maxlen=1000)
mystem = None
_config_mtime = CONFIG_FILE.stat().st_mtime if CONFIG_FILE.exists() else 0
user_voice_repo = None
_last_invalid_default_voice = None


class UserVoiceRepository:
    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)

    def _init_db(self):
        try:
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_voice_preferences (
                        username TEXT PRIMARY KEY,
                        voice TEXT NOT NULL,
                        updated_at INTEGER NOT NULL
                    )
                    """
                )
                conn.commit()
            logger.info("✅ База user voice preferences готова")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации БД voice preferences: {e}")

    def get_voice(self, username):
        try:
            with self._lock:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT voice FROM user_voice_preferences WHERE username = ?",
                        (username,),
                    ).fetchone()
            if not row:
                return None
            return str(row[0]).strip().lower()
        except Exception as e:
            logger.error(f"❌ Ошибка чтения voice preference: {e}")
            return None

    def set_voice(self, username, voice):
        try:
            now = int(time.time())
            with self._lock:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO user_voice_preferences (username, voice, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(username) DO UPDATE SET
                            voice=excluded.voice,
                            updated_at=excluded.updated_at
                        """,
                        (username, voice, now),
                    )
                    conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения voice preference: {e}")
            return False

    def reset_voice(self, username):
        try:
            with self._lock:
                with self._connect() as conn:
                    conn.execute(
                        "DELETE FROM user_voice_preferences WHERE username = ?",
                        (username,),
                    )
                    conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка сброса voice preference: {e}")
            return False

# =============================================================================
# 🔧 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================
def init_config():
    global API_ID, API_HASH, TWITCH_USERNAME, TWITCH_TOKEN, TWITCH_CHANNEL
    missing =[]
    if not API_ID or not API_HASH:
        missing.append("Telegram API")
    if not TWITCH_USERNAME or not TWITCH_TOKEN or not TWITCH_CHANNEL:
        missing.append("Twitch IRC")
    
    if missing:
        print(f"\n⚠️ Не найдено в .env: {', '.join(missing)}")
        if not API_ID or not API_HASH:
            API_ID = input("API_ID: ").strip()
            API_HASH = input("API_HASH: ").strip()
        if not TWITCH_USERNAME or not TWITCH_TOKEN or not TWITCH_CHANNEL:
            TWITCH_USERNAME = input("Twitch Username: ").strip().lower()
            TWITCH_TOKEN = input("Twitch OAuth Token: ").strip()
            if not TWITCH_TOKEN.startswith("oauth:"):
                TWITCH_TOKEN = f"oauth:{TWITCH_TOKEN}"
            ch = input("Channel: ").strip().lower()
            TWITCH_CHANNEL = ch.split("twitch.tv/")[-1].split("/")[0] if "twitch.tv/" in ch else ch
        
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(f"API_ID={API_ID}\nAPI_HASH={API_HASH}\n")
            f.write(f"TWITCH_USERNAME={TWITCH_USERNAME}\nTWITCH_TOKEN={TWITCH_TOKEN}\nTWITCH_CHANNEL={TWITCH_CHANNEL}\n")
        print(f"✅ Сохранено в {ENV_FILE.name}")
    return int(API_ID) if isinstance(API_ID, str) else API_ID

def init_mystem():
    global mystem
    try:
        mystem = Mystem(grammar_info=True, entire_input=False)
        mystem.analyze("тест")
        logger.info("✅ Mystem готов")
        return True
    except Exception as e:
        logger.error(f"❌ Mystem: {e}")
        mystem = None
        return False

def parse_voice_prefix(p):
    if not isinstance(p, str):
        return ("", "")
    return p.split(':', 1) if ':' in p else (p, p)

def normalize_username(username):
    return str(username).strip().lower()


def get_allowed_voice_prefixes():
    voices = []
    seen = set()
    for raw in VOICE_PREFIXES:
        prefix, _ = parse_voice_prefix(raw)
        voice = prefix.strip().lower()
        if voice and voice not in seen:
            seen.add(voice)
            voices.append(voice)
    return voices


def get_default_voice():
    global _last_invalid_default_voice
    allowed = get_allowed_voice_prefixes()
    if not allowed:
        return "bandit"
    configured = str(SETTINGS.get("default_voice", "")).strip().lower()
    if configured:
        if configured in allowed:
            _last_invalid_default_voice = None
            return configured
        if configured != _last_invalid_default_voice:
            logger.warning(
                f"⚠️ default_voice='{configured}' отсутствует в voices. Использую '{allowed[0]}'"
            )
            _last_invalid_default_voice = configured
    return allowed[0]


def get_voice_for_user(username):
    user = normalize_username(username)
    allowed = set(get_allowed_voice_prefixes())
    default_voice = get_default_voice()
    if not allowed:
        return default_voice, "default"
    if not user_voice_repo:
        return default_voice, "default"
    stored_voice = user_voice_repo.get_voice(user)
    if not stored_voice:
        return default_voice, "default"
    if stored_voice in allowed:
        return stored_voice, "db"
    logger.warning(
        f"⚠️ У пользователя '{user}' невалидный голос '{stored_voice}' в БД. Использую default."
    )
    return default_voice, "default"


def parse_voice_command(message):
    msg = str(message).strip()
    if not msg:
        return None
    parts = msg.split()
    if not parts or parts[0].lower() != "!voice":
        return None
    if len(parts) == 1:
        return ("help", None)

    action = parts[1].lower()
    if action == "set":
        value = parts[2].strip().lower() if len(parts) > 2 else ""
        return ("set", value)
    if action in {"list", "current", "reset"}:
        return (action, None)
    return ("help", None)


def handle_voice_command(username, message):
    parsed = parse_voice_command(message)
    if not parsed:
        return False, None

    action, value = parsed
    user = normalize_username(username)
    if user in BLACKLIST_USERS:
        return True, None
    allowed = get_allowed_voice_prefixes()
    allowed_set = set(allowed)
    default_voice = get_default_voice()

    if not allowed:
        return True, f"@{user} no voices configured right now"

    if action == "list":
        return True, f"available voices: {', '.join(allowed)}"

    if action == "current":
        current, source = get_voice_for_user(user)
        if source == "db":
            return True, f"@{user} your voice is '{current}'"
        return True, f"@{user} your voice is '{current}' (default)"

    if action == "set":
        if not value:
            return True, f"@{user} usage: !voice set <voice>. try !voice list"
        if value not in allowed_set:
            return True, f"@{user} unknown voice '{value}'. try !voice list"
        if not user_voice_repo:
            return True, f"@{user} preferences storage unavailable, using default '{default_voice}'"
        ok = user_voice_repo.set_voice(user, value)
        if ok:
            logger.info(f"🎙️ Пользователь '{user}' выбрал голос '{value}'")
            return True, f"@{user} voice set to '{value}'"
        return True, f"@{user} failed to save voice, using default '{default_voice}'"

    if action == "reset":
        if not user_voice_repo:
            return True, f"@{user} preferences storage unavailable, using default '{default_voice}'"
        ok = user_voice_repo.reset_voice(user)
        if ok:
            logger.info(f"🎙️ Пользователь '{user}' сбросил голос на default")
            return True, f"@{user} voice reset to default '{default_voice}'"
        return True, f"@{user} failed to reset voice, still using current/default value"

    return True, f"@{user} commands: !voice list | !voice current | !voice set <voice> | !voice reset"


def init_user_voice_repo():
    global user_voice_repo
    try:
        user_voice_repo = UserVoiceRepository(USER_PREFS_DB_FILE)
    except Exception as e:
        user_voice_repo = None
        logger.error(f"❌ Не удалось инициализировать user voice repo: {e}")

def check_config_reload():
    global CONFIG, VOICE_PREFIXES, BLACKLIST_USERS, BLACKLIST_PHRASES, SETTINGS, _config_mtime
    global TARGET_BOT_USERNAME, RESPONSE_TIMEOUT, AUDIO_VOLUME, FLASK_HOST, FLASK_PORT
    
    if not CONFIG_FILE.exists():
        return
    current_mtime = CONFIG_FILE.stat().st_mtime
    if current_mtime > _config_mtime:
        logger.info("🔄 config.json изменён, загружаем...")
        try:
            CONFIG = load_config()
            VOICE_PREFIXES = CONFIG["voices"]
            BLACKLIST_USERS = set(u.lower() for u in CONFIG.get("blacklist_users",[]))
            BLACKLIST_PHRASES = CONFIG.get("blacklist_phrases",[])
            SETTINGS = CONFIG.get("settings", {})
            
            TARGET_BOT_USERNAME = SETTINGS.get("target_bot", "silero_voice_bot")
            RESPONSE_TIMEOUT = int(SETTINGS.get("response_timeout", 45))
            AUDIO_VOLUME = float(SETTINGS.get("audio_volume", 1.0))
            FLASK_HOST = SETTINGS.get("flask_host", "127.0.0.1")
            FLASK_PORT = int(SETTINGS.get("flask_port", 8124))
            
            _config_mtime = CONFIG_FILE.stat().st_mtime
            logger.info("✅ Конфиг успешно обновлён в оперативной памяти")
        except Exception as e:
            logger.error(f"❌ Ошибка перезагрузки: {e}")

# =============================================================================
# 🚫 ФИЛЬТРАЦИЯ
# =============================================================================
def filter_message(username, message):
    msg = message.strip()
    
    max_len = SETTINGS.get("max_message_length", 250)
    if len(msg) > max_len:
        return False
        
    if msg.startswith(('/', '!')):
        return False
    if username.lower() in BLACKLIST_USERS:
        return False
    if any(ph.lower() in msg.lower() for ph in BLACKLIST_PHRASES):
        return False
    return bool(msg)

# =============================================================================
# 🔤 ОБРАБОТКА ТЕКСТА
# =============================================================================
def replace_numbers_smart(text):
    def replacer(m):
        num = m.group(0)
        idx = m.start()
        if idx > 0 and text[idx-1].lower() == 'v':
            return num
        try:
            return num2words(int(num), lang='ru')
        except:
            return num
    return re.sub(r'[-]?\d+', replacer, text)

def correct_gender_mystem(text):
    if not mystem:
        return text
    words = text.split()
    result =[]
    for i, word in enumerate(words):
        if word in['один', 'два'] and i+1 < len(words):
            nxt = re.sub(r'[^\w\s]+$', '', words[i+1]).strip()
            if nxt:
                try:
                    an = mystem.analyze(nxt)
                    if an and an[0].get('analysis'):
                        gr = an[0]['analysis'][0].get('gr', '')
                        if gr.startswith('S,'):
                            if 'жен' in gr:
                                result.append('одна' if word=='один' else 'две')
                                continue
                            elif 'сред' in gr and word=='один':
                                result.append('одно')
                                continue
                except:
                    pass
        result.append(word)
    return ' '.join(result)

def process_chat_message(username, raw):
    raw = re.sub(r'@[a-zA-Z0-9_]+', '', raw)
    if SETTINGS.get("ignore_english", True):
        raw = re.sub(r'\b[a-zA-Z][a-zA-Z0-9_]*', '', raw)
        raw = re.sub(r'[a-zA-Z]', '', raw)
        
    if not filter_message(username, raw):
        return None
        
    text = replace_numbers_smart(raw.strip())
    text = correct_gender_mystem(text)

    if not re.search(r'[а-яА-ЯёЁ0-9a-zA-Z]', text):
        return None

    prefix, source = get_voice_for_user(username)
    final = f"{prefix} {text}"
    logger.info(f"🎤[{username}] ({source}) → '{final}'")
    return final

# =============================================================================
# 📺 TWITCH IRC
# =============================================================================
async def _twitch_close_transport():
    global twitch_reader, twitch_writer
    r, w = twitch_reader, twitch_writer
    twitch_reader, twitch_writer = None, None
    if w is not None and not w.is_closing():
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass
    if r is not None:
        try:
            r.feed_eof()
        except Exception:
            pass


async def connect_twitch():
    global twitch_reader, twitch_writer, twitch_connected
    if not all([TWITCH_USERNAME, TWITCH_TOKEN, TWITCH_CHANNEL]):
        logger.error("❌ Twitch настройки пустые")
        return
    async with _twitch_lock:
        if twitch_connected and twitch_reader and twitch_writer:
            return
        await _twitch_close_transport()
        twitch_connected = False
        try:
            logger.info(f"🔌 Twitch: #{TWITCH_CHANNEL}")
            ssl_ctx = ssl.create_default_context()
            twitch_reader, twitch_writer = await asyncio.open_connection(
                "irc.chat.twitch.tv", 6697, ssl=ssl_ctx
            )
            twitch_writer.write(f"PASS {TWITCH_TOKEN}\r\n".encode())
            twitch_writer.write(f"NICK {TWITCH_USERNAME}\r\n".encode())
            twitch_writer.write("CAP REQ :twitch.tv/tags twitch.tv/commands\r\n".encode())
            twitch_writer.write(f"JOIN #{TWITCH_CHANNEL}\r\n".encode())
            await twitch_writer.drain()
            twitch_connected = True
            logger.info("✅ Вход в Twitch")
            asyncio.create_task(twitch_listener())
        except Exception as e:
            logger.error(f"❌ Twitch connect: {e}", exc_info=True)
            twitch_connected = False


async def twitch_listener():
    global twitch_connected
    try:
        while twitch_connected and twitch_reader:
            try:
                data = await twitch_reader.read(4096)
                if not data:
                    logger.warning("⚠️ Twitch: сервер закрыл соединение (0 байт)")
                    break
                for msg in data.decode("utf-8", errors="ignore").split("\r\n"):
                    if not msg:
                        continue
                    if msg.startswith("PING"):
                        if twitch_writer and not twitch_writer.is_closing():
                            twitch_writer.write(b"PONG :tmi.twitch.tv\r\n")
                            await twitch_writer.drain()
                        continue
                    if "PRIVMSG" not in msg:
                        continue
                    # IRC всегда даёт #channel в lower case; сравниваем без учёта регистра
                    if f"#{TWITCH_CHANNEL}".lower() not in msg.lower():
                        continue
                    await parse_twitch_message(msg)
            except Exception as e:
                logger.error(f"❌ twitch_listener: {e}", exc_info=True)
                break
    finally:
        twitch_connected = False
        await _twitch_close_transport()
        if _bot_shutting_down:
            return
        logger.warning("🔄 Twitch: переподключение через 5 с…")
        await asyncio.sleep(5)
        asyncio.create_task(connect_twitch())

async def parse_twitch_message(raw):
    try:
        tags_dict = {}
        msg_str = raw
        
        if msg_str.startswith('@'):
            parts = msg_str.split(' ', 1)
            if len(parts) > 1:
                tags_part = parts[0][1:]
                msg_str = parts[1]
                for tag in tags_part.split(';'):
                    if '=' in tag:
                        k, v = tag.split('=', 1)
                        tags_dict[k] = v

        if " CLEARMSG " in msg_str:
            target_id = tags_dict.get('target-msg-id')
            if target_id:
                deleted_messages.append(target_id)
                logger.info(f"🗑️ Сообщение удалено в чате (ID: {target_id[:8]}...)")
            return

        if " PRIVMSG " not in msg_str:
            return
            
        parts = msg_str.split(':', 2)
        if len(parts) < 3:
            return

        user = parts[1].split("!")[0].lower()
        msg = parts[2].strip()
        # Раньше отбрасывали все сообщения с NICK бота — стример с тем же логином никогда не попадал в TTS.
        # Игнорируем только эхо наших же ответов в чат (!voice и т.д.).
        if TWITCH_USERNAME and user == TWITCH_USERNAME and _is_echo_of_our_chat_reply(msg):
            return
        handled, command_response = handle_voice_command(user, msg)
        if handled:
            if command_response:
                await send_twitch_chat_message(command_response)
            return
            
        if SETTINGS.get("only_highlighted", False):
            is_highlighted = (tags_dict.get('msg-id') == 'highlighted-message' or 'custom-reward-id' in tags_dict)
            if not is_highlighted:
                return

        msg_id = tags_dict.get('id', '')
        
        processed = process_chat_message(user, msg)
        if processed:
            await synthesis_queue.put((msg_id, processed))
            
    except Exception as e:
        logger.error(f"❌ parse_twitch: {e}")


async def send_twitch_chat_message(text):
    global twitch_writer, twitch_connected
    if not twitch_connected or not twitch_writer:
        return False
    try:
        twitch_writer.write(f"PRIVMSG #{TWITCH_CHANNEL} :{text}\r\n".encode("utf-8"))
        await twitch_writer.drain()
        _register_twitch_outgoing(text)
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка отправки ответа в Twitch чат: {e}")
        return False

# =============================================================================
# 🤖 TELEGRAM / СИНТЕЗ
# =============================================================================
async def send_to_tts_bot(text):
    global pyrogram_client, TARGET_BOT_ID
    if not pyrogram_client or not TARGET_BOT_ID:
        logger.error("❌ Telegram не готов")
        return False
    try:
        await pyrogram_client.send_message(TARGET_BOT_ID, text)
        return True
    except FloodWait as e:
        logger.warning(f"⏳ FloodWait: {e.value}с")
        await asyncio.sleep(e.value)
        return await send_to_tts_bot(text)
    except Exception as e:
        logger.error(f"❌ Отправка: {e}")
        return False

def _silero_sends_audio_document(_, __, m):
    if not getattr(m, "document", None) or not m.document.mime_type:
        return False
    return m.document.mime_type.lower().startswith("audio/")


def setup_telegram_handlers(client):
    # voice / audio / video_note (кружок) / document audio — иначе ответ не ловится и срабатывает таймаут
    _sound_from_bot = filters.private & filters.user(TARGET_BOT_USERNAME) & (
        filters.voice
        | filters.audio
        | filters.video_note
        | (filters.document & filters.create(_silero_sends_audio_document))
    )

    @client.on_message(_sound_from_bot)
    async def handle_voice(client, message):
        if message.voice:
            kind = "voice"
        elif message.audio:
            kind = "audio"
        elif message.video_note:
            kind = "video_note"
        else:
            kind = "document"
        logger.info(f"🎧 Звук от бота ({kind}), id={message.id}")
        bot_response_event.set()
        path = wav = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
                raw_path = tmp.name
            path = await client.download_media(message, file_name=raw_path)
            if not path:
                logger.error("❌ download_media вернул пустой путь")
                return
            if not HAS_FFMPEG:
                if kind == "video_note":
                    logger.error(
                        "❌ Получен video_note, но ffmpeg/ffprobe не найдены. "
                        "Выключи /videonotes в @silero_voice_bot или установи ffmpeg."
                    )
                    return
                # Для voice/audio (ogg/mp3) можем играть напрямую через pygame/playsound без pydub/ffmpeg
                logger.warning("⚠️ ffmpeg не найден: воспроизвожу напрямую без конвертации")
                await asyncio.to_thread(play_audio_direct, path)
                return

            audio = AudioSegment.from_file(path)
            wav = str(path) + ".wav"
            audio.export(wav, format="wav")
            logger.info("🔓 Ответ Silero получен, воспроизведение…")
            await asyncio.to_thread(
                play_audio,
                wav,
                audio.frame_rate,
                audio.channels,
                audio.sample_width,
            )
        except Exception as e:
            logger.error(f"❌ Аудио: {e}", exc_info=True)
        finally:
            playback_done_event.set()
            for p in (path, wav):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            logger.info("🔓 Готово (голос)")

    @client.on_message(filters.private & filters.user(TARGET_BOT_USERNAME) & filters.text)
    async def handle_text_refusal(client, message):
        body = (message.text or "").strip()
        logger.warning(
            "📝 Silero вернул текст вместо голоса — озвучки не будет. "
            "Часто нужно подписаться на канал из сообщения ниже и снова запросить TTS.\n%s",
            body,
        )
        bot_response_event.set()
        playback_done_event.set()
        logger.info("🔓 Освобождено (текст)")

def _pygame_sample_size(sample_width_bytes):
    if sample_width_bytes == 1:
        return -8
    if sample_width_bytes == 2:
        return -16
    if sample_width_bytes == 4:
        return -32
    return -16


def play_audio(path, sample_rate=44100, channels=2, sample_width=2):
    logger.info(f"🔊 Play: {path} ({sample_rate} Hz, {channels} ch)")
    if USE_PYGAME:
        with _audio_play_lock:
            _play_audio_pyg(path, sample_rate, channels, sample_width)
    elif USE_PLAYSOUND:
        with _audio_play_lock:
            _play_audio_playsound(path)
    else:
        logger.warning("⚠️ Нет аудио-библиотек!")


def play_audio_direct(path):
    logger.info(f"🔊 Play direct: {path}")
    if USE_PYGAME:
        with _audio_play_lock:
            _play_audio_file_pyg(path)
    elif USE_PLAYSOUND:
        with _audio_play_lock:
            _play_audio_playsound(path)
    else:
        logger.warning("⚠️ Нет аудио-библиотек!")


def _play_audio_file_pyg(path):
    try:
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        pygame.mixer.init()
        if pygame.mixer.get_init() is None:
            logger.error("❌ pygame.mixer не инициализирован (нет аудиоустройства?)")
        vol = min(max(float(AUDIO_VOLUME), 0.0), 2.0)
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(min(vol, 1.0))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    except Exception as e:
        logger.error(f"❌ Прямое воспроизведение pygame: {e}", exc_info=True)
    finally:
        try:
            pygame.mixer.quit()
        except Exception:
            pass


def _play_audio_pyg(path, sample_rate, channels, sample_width):
    try:
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        size = _pygame_sample_size(sample_width)
        ch = int(channels)
        if ch not in (1, 2):
            ch = 2
        pygame.mixer.init(
            frequency=int(sample_rate),
            size=size,
            channels=ch,
            buffer=2048,
        )
        if pygame.mixer.get_init() is None:
            logger.error("❌ pygame.mixer не инициализирован (нет аудиоустройства?)")
        pygame.mixer.music.load(path)
        vol = min(max(float(AUDIO_VOLUME), 0.0), 2.0)
        pygame.mixer.music.set_volume(min(vol, 1.0))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    except Exception as e:
        logger.error(f"❌ Воспроизведение pygame: {e}", exc_info=True)
    finally:
        try:
            pygame.mixer.quit()
        except Exception:
            pass


def _play_audio_playsound(path):
    try:
        playsound(path)
    except Exception as e:
        logger.error(f"❌ Воспроизведение playsound: {e}", exc_info=True)

# =============================================================================
# ⚙️ ОЧЕРЕДЬ СИНТЕЗА
# =============================================================================
async def synthesis_worker():
    logger.info("🔄 Worker запущен")
    while True:
        try:
            queue_item = await synthesis_queue.get()
            try:
                if isinstance(queue_item, tuple):
                    msg_id, text = queue_item
                else:
                    msg_id, text = "", queue_item
                    
                # Проверяем до захвата семафора
                if msg_id and msg_id in deleted_messages:
                    logger.info(f"⏭️ Пропуск удалённого сообщения: '{text[:40]}...'")
                    continue
                    
                await synthesis_semaphore.acquire()
                try:
                    # Повторная проверка
                    if msg_id and msg_id in deleted_messages:
                        logger.info(f"⏭️ Пропуск (успели удалить): '{text[:40]}...'")
                        continue
                        
                    logger.info(f"⚙️ Синтез: {text[:60]}...")
                    
                    bot_response_event.clear()
                    playback_done_event.clear()
                    
                    sent = await send_to_tts_bot(text)
                    if not sent:
                        logger.error("❌ Не отправлено")
                        continue
                    
                    # 1. Ждем ТОЛЬКО получения ответа от бота (учитываем таймаут генерации)
                    try:
                        await asyncio.wait_for(bot_response_event.wait(), timeout=RESPONSE_TIMEOUT)
                    except asyncio.TimeoutError:
                        logger.warning("⏰ Таймаут ответа от бота (нет ответа)")
                        continue
                    
                    # 2. Ждем окончания самого воспроизведения (никаких таймаутов, ждем полного завершения звука)
                    await playback_done_event.wait()
                    
                finally:
                    # Гарантированно отпускаем семафор только тут (воркер сам управляет очередью)
                    synthesis_semaphore.release()
            finally:
                synthesis_queue.task_done()
                
        except Exception as e:
            logger.error(f"❌ Worker error: {e}", exc_info=True)
            await asyncio.sleep(2)

# =============================================================================
# 🚀 ЗАПУСК
# =============================================================================
async def init_telegram():
    global pyrogram_client, TARGET_BOT_ID
    api_id = init_config()
    logger.info("📱 Pyrogram init...")
    client = Client(SESSION_NAME, api_id=api_id, api_hash=API_HASH)
    setup_telegram_handlers(client)
    await client.start()
    me = await client.get_me()
    logger.info(f"✅ Telegram: @{me.username}")
    try:
        bot = await client.get_users(TARGET_BOT_USERNAME)
        TARGET_BOT_ID = bot.id
        logger.info(f"🤖 Бот ID: {TARGET_BOT_ID}")
    except Exception as e:
        logger.error(f"❌ Бот не найден: {e}")
    pyrogram_client = client
    return client

async def main():
    global _bot_shutting_down
    logger.info("🚀 Запуск TTS Bot")
    init_mystem()
    init_user_voice_repo()
    await init_telegram()
    asyncio.create_task(synthesis_worker())
    await connect_twitch()
    
    last_cfg = time.time()
    try:
        while True:
            await asyncio.sleep(30)
            if time.time() - last_cfg > 30:
                check_config_reload()
                last_cfg = time.time()
            if not twitch_connected and TWITCH_USERNAME:
                logger.info("🔄 Переподключение Twitch...")
                await connect_twitch()
    except KeyboardInterrupt:
        logger.info("👋 Завершение")
    finally:
        _bot_shutting_down = True
        if pyrogram_client:
            await pyrogram_client.stop()
        await _twitch_close_transport()
        logger.info("✅ Готово")

if __name__ == "__main__":
    if HAS_FFMPEG:
        logger.info("🎬 ffmpeg/ffprobe: OK")
    else:
        logger.warning(
            "⚠️ ffmpeg/ffprobe не найдены: video_note не поддерживается, "
            "voice/audio будут проигрываться напрямую"
        )
    if USE_PYGAME:
        logger.info("🎵 Audio: pygame")
    elif USE_PLAYSOUND:
        logger.info("🎵 Audio: playsound")
    else:
        logger.warning("⚠️ Нет аудио-библиотек!")
    asyncio.run(main())
# --- END OF FILE app.py ---
