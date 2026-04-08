import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def bot_module():
    root = Path(__file__).resolve().parents[1]
    script_path = root / "TTV-Chat-2-Silero-Telegram-Bot.py"
    spec = importlib.util.spec_from_file_location("tts_bot_module", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def isolated_bot_state(bot_module):
    snapshot = {
        "VOICE_PREFIXES": list(bot_module.VOICE_PREFIXES),
        "SETTINGS": dict(bot_module.SETTINGS),
        "BLACKLIST_USERS": set(bot_module.BLACKLIST_USERS),
        "BLACKLIST_PHRASES": list(bot_module.BLACKLIST_PHRASES),
        "_last_invalid_default_voice": bot_module._last_invalid_default_voice,
        "mystem": bot_module.mystem,
        "user_voice_repo": bot_module.user_voice_repo,
    }
    try:
        yield bot_module
    finally:
        bot_module.VOICE_PREFIXES = snapshot["VOICE_PREFIXES"]
        bot_module.SETTINGS = snapshot["SETTINGS"]
        bot_module.BLACKLIST_USERS = snapshot["BLACKLIST_USERS"]
        bot_module.BLACKLIST_PHRASES = snapshot["BLACKLIST_PHRASES"]
        bot_module._last_invalid_default_voice = snapshot["_last_invalid_default_voice"]
        bot_module.mystem = snapshot["mystem"]
        bot_module.user_voice_repo = snapshot["user_voice_repo"]
