class FakeRepo:
    def __init__(self):
        self.storage = {}

    def get_voice(self, username):
        return self.storage.get(username)

    def set_voice(self, username, voice):
        self.storage[username] = voice
        return True

    def reset_voice(self, username):
        self.storage.pop(username, None)
        return True


def test_normalize_twitch_channel(bot_module):
    assert bot_module._normalize_twitch_channel("#MyChannel") == "mychannel"
    assert bot_module._normalize_twitch_channel("twitch") == "twitch"
    assert bot_module._normalize_twitch_channel("  ") is None


def test_normalize_twitch_login(bot_module):
    assert bot_module._normalize_twitch_login("  User_Name  ") == "user_name"
    assert bot_module._normalize_twitch_login("") is None


def test_parse_voice_command(bot_module):
    assert bot_module.parse_voice_command("!voice") == ("help", None)
    assert bot_module.parse_voice_command("!voice list") == ("list", None)
    assert bot_module.parse_voice_command("!voice set ARTHAS") == ("set", "arthas")
    assert bot_module.parse_voice_command("hello") is None


def test_get_allowed_voice_prefixes_deduplicates(isolated_bot_state):
    bot = isolated_bot_state
    bot.VOICE_PREFIXES = ["arthas", "arthas:WarCraft", " bandit ", "BANDIT", "", 123]
    assert bot.get_allowed_voice_prefixes() == ["arthas", "bandit"]


def test_get_default_voice_fallback_when_invalid(isolated_bot_state):
    bot = isolated_bot_state
    bot.VOICE_PREFIXES = ["bandit", "arthas"]
    bot.SETTINGS = {"default_voice": "unknown"}
    assert bot.get_default_voice() == "bandit"


def test_filter_message_rules(isolated_bot_state):
    bot = isolated_bot_state
    bot.BLACKLIST_USERS = {"nightbot"}
    bot.BLACKLIST_PHRASES = ["https://", "!discord"]
    assert bot.filter_message("viewer", "обычный текст") is True
    assert bot.filter_message("nightbot", "обычный текст") is False
    assert bot.filter_message("viewer", "!команда") is False
    assert bot.filter_message("viewer", "ссылка https://site") is False


def test_replace_numbers_smart_keeps_v_prefix(isolated_bot_state):
    bot = isolated_bot_state
    converted = bot.replace_numbers_smart("у меня 12 и v2")
    assert "двенадцать" in converted
    assert "v2" in converted


def test_process_chat_message_formats_with_voice(isolated_bot_state, monkeypatch):
    bot = isolated_bot_state
    bot.BLACKLIST_USERS = set()
    bot.BLACKLIST_PHRASES = []
    bot.mystem = None

    def fake_get_voice_for_user(_):
        return "arthas", "db"

    monkeypatch.setattr(bot, "get_voice_for_user", fake_get_voice_for_user)
    result = bot.process_chat_message("viewer", "123")
    assert result.startswith("arthas ")
    assert "сто" in result


def test_handle_voice_command_set_and_current(isolated_bot_state):
    bot = isolated_bot_state
    bot.VOICE_PREFIXES = ["bandit", "arthas"]
    bot.SETTINGS = {"default_voice": "bandit"}
    bot.BLACKLIST_USERS = set()
    bot.user_voice_repo = FakeRepo()

    handled, response = bot.handle_voice_command("viewer", "!voice set arthas")
    assert handled is True
    assert "voice set to 'arthas'" in response

    handled, response = bot.handle_voice_command("viewer", "!voice current")
    assert handled is True
    assert "your voice is 'arthas'" in response
