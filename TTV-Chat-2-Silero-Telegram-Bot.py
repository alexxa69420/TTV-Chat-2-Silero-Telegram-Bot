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
import random
import json
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

# =============================================================================
# 📌 БАЗОВЫЕ ПУТИ (Гарантирует создание файлов рядом со скриптом)
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
ENV_FILE = BASE_DIR / ".env"

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
                
        if "settings" in config:
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

# Секреты
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_NAME = str(BASE_DIR / "tts_session")
TWITCH_USERNAME = os.getenv("TWITCH_USERNAME")
TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL")

# Настройки
SETTINGS = CONFIG.get("settings", {})
TARGET_BOT_USERNAME = SETTINGS.get("target_bot", "silero_voice_bot")
RESPONSE_TIMEOUT = int(SETTINGS.get("response_timeout", 45))
AUDIO_VOLUME = float(SETTINGS.get("audio_volume", 1.0))
FLASK_HOST = SETTINGS.get("flask_host", "127.0.0.1")
FLASK_PORT = int(SETTINGS.get("flask_port", 8124))

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

synthesis_queue = asyncio.Queue()
synthesis_semaphore = asyncio.Semaphore(1)

# Два события: одно для ожидания ответа бота, другое для завершения воспроизведения
bot_response_event = asyncio.Event()
playback_done_event = asyncio.Event()

# Хранилище ID последних удалённых сообщений
deleted_messages = deque(maxlen=1000)

voice_bag =[]
voice_bag_lock = threading.Lock()
mystem = None
_config_mtime = CONFIG_FILE.stat().st_mtime if CONFIG_FILE.exists() else 0

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
    return p.split(':', 1) if ':' in p else (p, p)

def get_next_voice_prefix():
    global voice_bag, voice_bag_lock
    with voice_bag_lock:
        if not voice_bag:
            voice_bag =[parse_voice_prefix(p)[0] for p in VOICE_PREFIXES]
            random.shuffle(voice_bag)
            logger.info(f"🔀 Мешок: {voice_bag}")
        return voice_bag.pop()

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
        
    prefix = get_next_voice_prefix()
    final = f"{prefix} {text}"
    logger.info(f"🎤[{username}] → '{final}'")
    return final

# =============================================================================
# 📺 TWITCH IRC
# =============================================================================
async def connect_twitch():
    global twitch_reader, twitch_writer, twitch_connected
    if not all([TWITCH_USERNAME, TWITCH_TOKEN, TWITCH_CHANNEL]):
        logger.error("❌ Twitch настройки пустые")
        return
    try:
        logger.info(f"🔌 Twitch: #{TWITCH_CHANNEL}")
        ssl_ctx = ssl.create_default_context()
        twitch_reader, twitch_writer = await asyncio.open_connection(
            'irc.chat.twitch.tv', 6697, ssl=ssl_ctx)
            
        auth = f"PASS {TWITCH_TOKEN}\r\nNICK {TWITCH_USERNAME}\r\n"
        twitch_writer.write(auth.encode())
        
        req = "CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"
        twitch_writer.write(req.encode())
        
        join = f"JOIN #{TWITCH_CHANNEL}\r\n"
        twitch_writer.write(join.encode())
        
        await twitch_writer.drain()
        twitch_connected = True
        logger.info("✅ Вход в Twitch")
        asyncio.create_task(twitch_listener())
    except Exception as e:
        logger.error(f"❌ Twitch connect: {e}")
        twitch_connected = False

async def twitch_listener():
    global twitch_reader, twitch_writer, twitch_connected
    while twitch_connected and twitch_reader:
        try:
            data = await twitch_reader.read(4096)
            if not data:
                logger.warning("⚠️ Twitch разорван")
                twitch_connected = False
                break
            for msg in data.decode('utf-8', errors='ignore').split('\r\n'):
                if not msg:
                    continue
                if msg.startswith("PING"):
                    twitch_writer.write("PONG :tmi.twitch.tv\r\n".encode())
                    await twitch_writer.drain()
                    continue
                await parse_twitch_message(msg)
        except Exception as e:
            logger.error(f"❌ twitch_listener: {e}")
            await asyncio.sleep(5)
            if not twitch_connected:
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
            
        user = parts[1].split('!')[0].lower()
        msg = parts[2].strip()
        
        if user == TWITCH_USERNAME.lower():
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

def setup_telegram_handlers(client):
    @client.on_message(filters.private & filters.user(TARGET_BOT_USERNAME) & filters.voice)
    async def handle_voice(client, message):
        logger.info("🎧 Голос от бота")
        bot_response_event.set() # Сигнализируем, что бот ответил
        try:
            with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
                path = await message.download(file_name=tmp.name)
            
            # Выполняем синхронную обработку и блокирующее аудио в фоновом пуле потоков
            def process_and_play():
                try:
                    audio = AudioSegment.from_ogg(path)
                    wav = path.replace('.ogg', '.wav')
                    audio.export(wav, format='wav')
                    play_audio(wav)
                    try:
                        os.unlink(path)
                        os.unlink(wav)
                    except:
                        pass
                except Exception as e:
                    logger.error(f"❌ Ошибка аудио (конвертация/воспроизведение): {e}")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, process_and_play)
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки файла: {e}")
        finally:
            playback_done_event.set() # Сигнализируем, что воспроизведение завершилось
            logger.info("🔓 Освобождено (голос проигран)")

    @client.on_message(filters.private & filters.user(TARGET_BOT_USERNAME) & filters.text)
    async def handle_text_refusal(client, message):
        logger.info(f"📝 Текст от бота (отказ): '{message.text[:50]}...'")
        bot_response_event.set()
        playback_done_event.set() # Отказ, поэтому просто снимаем блок
        logger.info("🔓 Освобождено (текст)")

def play_audio(path):
    logger.info(f"🔊 Play: {path}")
    if USE_PYGAME:
        pygame.mixer.init()
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(min(max(AUDIO_VOLUME, 0.0), 2.0))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        pygame.mixer.quit()
    elif USE_PLAYSOUND:
        playsound(path)
    else:
        logger.warning("⚠️ Нет аудио-библиотек!")

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
    logger.info("🚀 Запуск TTS Bot")
    init_mystem()
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
        if pyrogram_client:
            await pyrogram_client.stop()
        if twitch_writer:
            twitch_writer.close()
        logger.info("✅ Готово")

if __name__ == "__main__":
    if USE_PYGAME:
        logger.info("🎵 Audio: pygame")
    elif USE_PLAYSOUND:
        logger.info("🎵 Audio: playsound")
    else:
        logger.warning("⚠️ Нет аудио-библиотек!")
    asyncio.run(main())
# --- END OF FILE app.py ---
