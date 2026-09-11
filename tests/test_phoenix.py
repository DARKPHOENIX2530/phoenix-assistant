"""Offline unit tests for Phoenix.

No network, no API keys, no touching real user files: config I/O and notes
are redirected into a temp directory per test. Run with:

    python -m unittest discover -s tests -v
"""
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import assistant  # noqa: E402
import browser_profiles  # noqa: E402
import config     # noqa: E402
import json       # noqa: E402
import mind       # noqa: E402
import providers  # noqa: E402
import tools      # noqa: E402
import watchdog   # noqa: E402

_ORIG_NOTES_DIR = tools.NOTES_DIR
_ORIG_CONFIG_DIR = config.CONFIG_DIR
_ORIG_CONFIG_FILE = config.CONFIG_FILE


def mock_cfg():
    cfg = config.defaults()
    cfg["settings"]["provider"] = "mock"
    cfg["settings"]["voice_out"] = False
    return cfg


class IsolatedTest(unittest.TestCase):
    """Redirects notes + config writes into a fresh temp dir per test."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="phoenix_test_")
        tools.NOTES_DIR = os.path.join(self._tmp, "notes")
        os.makedirs(tools.NOTES_DIR, exist_ok=True)
        config.CONFIG_DIR = os.path.join(self._tmp, "config")
        config.CONFIG_FILE = os.path.join(config.CONFIG_DIR, "config.json")
        # mind.py derives its file from tools.NOTES_DIR at import time;
        # repoint it so every test is isolated from the real mind.json.
        mind.MIND_FILE = os.path.join(tools.NOTES_DIR, "mind.json")

    def tearDown(self):
        tools.NOTES_DIR = _ORIG_NOTES_DIR
        config.CONFIG_DIR = _ORIG_CONFIG_DIR
        config.CONFIG_FILE = _ORIG_CONFIG_FILE
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestConfig(IsolatedTest):
    def test_defaults_shape(self):
        cfg = config.defaults()
        for name in ("gemini", "groq", "openrouter", "github", "cerebras",
                     "ollama", "mock"):
            self.assertIn(name, cfg["providers"])
        self.assertIn(cfg["settings"]["provider"], ("gemini", "freellmapi"))
        self.assertTrue(cfg["providers"]["gemini"]["requires_key"])
        self.assertFalse(cfg["providers"]["mock"]["requires_key"])

    def test_save_load_roundtrip(self):
        cfg = config.defaults()
        cfg["providers"]["gemini"]["api_key"] = "secret-abc"
        cfg["settings"]["provider"] = "groq"
        config.save(cfg)

        loaded = config.load()
        self.assertEqual(loaded["settings"]["provider"], "groq")
        self.assertEqual(loaded["providers"]["gemini"]["api_key"], "secret-abc")
        # custom provider survives a round trip too
        cfg["providers"]["mylocal"] = {
            "type": "openai", "label": "mylocal",
            "model": "qwen2.5", "base_url": "http://127.0.0.1:8000/v1",
            "api_key": "", "requires_key": True}
        config.save(cfg)
        again = config.load()
        self.assertEqual(again["providers"]["mylocal"]["model"], "qwen2.5")


class TestTools(IsolatedTest):
    def test_notes_roundtrip(self):
        w = tools.manage_notes({"action": "write", "name": "todo",
                                "content": "buy milk"})
        self.assertIn("Saved", w)
        r = tools.manage_notes({"action": "read", "name": "todo"})
        self.assertIn("buy milk", r)
        listing = tools.manage_notes({"action": "list"})
        self.assertIn("todo", listing)
        a = tools.manage_notes({"action": "append", "name": "todo",
                                "content": "call mom"})
        self.assertIn("Saved", a)
        self.assertIn("call mom",
                      tools.manage_notes({"action": "read", "name": "todo"}))
        self.assertIn("Deleted", tools.manage_notes(
            {"action": "delete", "name": "todo"}))
        self.assertIn("No note",
                      tools.manage_notes({"action": "read", "name": "todo"}))

    def test_notes_names_cannot_escape(self):
        # A traversal-style name is neutralized, never written outside notes/
        out = tools.manage_notes(
            {"action": "write", "name": "../../evil", "content": "x"})
        self.assertIn("Saved", out)
        self.assertEqual(sorted(os.listdir(self._tmp)), ["notes"])
        self.assertTrue(os.path.exists(os.path.join(tools.NOTES_DIR,
                                                    "evil.md")))
        # A name that sanitizes to nothing is rejected outright
        with self.assertRaises(ValueError):
            tools.manage_notes({"action": "read", "name": "..."})

    def test_simple_tools(self):
        self.assertIn("on", tools.current_time())
        self.assertIn("OS", tools.system_info())
        self.assertIn("valid URL",
                      tools.open_url({"url": "not a url at all"}))
        self.assertIn("valid URL",
                      tools.open_url({"url": "example"}))  # no dot
        self.assertIn("Unknown app",
                      tools.open_app({"app": "some-made-up-app"}))

    def test_run_dispatch(self):
        # current_time dispatches cleanly; unknown tool names raise
        self.assertIn("on", tools.run("current_time", {}))
        with self.assertRaises(ValueError):
            tools.run("no_such_tool", {})

    def test_empty_search_is_safe(self):
        # empty query -> error text, no network involved
        out = tools.web_search({"query": "   "})
        self.assertIn("Empty search", out)


class TestProviders(IsolatedTest):
    def test_mock_provider_replies(self):
        spec = config.defaults()["providers"]["mock"]
        reply = providers.chat(spec, [{"role": "user", "content": "hello"}],
                               "sys")
        self.assertIsInstance(reply, str)
        self.assertTrue(reply.strip())

    def test_keyless_provider_fails_friendly(self):
        spec = config.defaults()["providers"]["gemini"]
        with self.assertRaises(providers.ProviderError) as ctx:
            providers.chat(spec, [{"role": "user", "content": "hi"}], "sys")
        self.assertIn("API key", str(ctx.exception))

    def test_bad_base_url_fails_friendly(self):
        spec = dict(config.defaults()["providers"]["gemini"])
        spec["api_key"] = "dummy"
        spec["base_url"] = ""  # blank base -> friendly error, no network
        with self.assertRaises(providers.ProviderError):
            providers.chat(spec, [{"role": "user", "content": "hi"}], "sys")


class TestAssistant(IsolatedTest):
    def make(self, provider="mock"):
        cfg = mock_cfg()
        cfg["settings"]["provider"] = provider
        return assistant.Phoenix(cfg)

    def test_mock_chat_flow(self):
        bot = self.make()
        reply = bot.handle("hello")
        self.assertTrue(reply.strip())
        self.assertEqual(bot.last_kind, "ai")
        self.assertEqual(len(bot.history), 2)  # user + assistant
        self.assertEqual(bot.history[-1]["role"], "assistant")

    def test_no_key_error_message(self):
        bot = self.make(provider="gemini")
        reply = bot.handle("hello")
        self.assertTrue(reply.startswith("!"))
        self.assertIn("API key", reply)
        self.assertEqual(bot.history, [])  # failed ask not kept

    def test_slash_commands(self):
        bot = self.make()
        self.assertIn("/setup", bot.handle("/help"))
        self.assertIn("PHOENIX COMMANDS", bot.handle("/help"))
        self.assertIn("on", bot.handle("/time"))
        self.assertIn("OS", bot.handle("/sys"))
        self.assertIn("Unknown app", bot.handle("/app wat"))
        self.assertIn("Saved", bot.handle("/remember eggs are cheap"))
        self.assertIn("eggs are cheap", bot.handle("/read memory"))
        self.assertIn("Deleted", bot.handle("/forget"))
        self.assertIsNone(bot.handle("/quit"))

    def test_provider_switch_and_model(self):
        bot = self.make()
        self.assertIn("Switched", bot.handle("/provider groq"))
        self.assertEqual(bot.cfg["settings"]["provider"], "groq")
        self.assertIn("set to", bot.handle("/model llama-3.3-70b-versatile"))
        self.assertEqual(
            bot.cfg["providers"]["groq"]["model"], "llama-3.3-70b-versatile")

    def test_history_trims(self):
        bot = self.make()
        bot.cfg["settings"]["max_history"] = 3  # keep 3 pairs = 6 msgs
        for i in range(12):
            bot.handle("message number %d" % i)
        self.assertLessEqual(len(bot.history), 6)
        self.assertEqual(bot.history[-1]["role"], "assistant")

    def test_save_transcript(self):
        bot = self.make()
        bot.handle("hello there")
        reply = bot.handle("/save")
        self.assertIn("saved to", reply.lower())
        path = reply.split("saved to ", 1)[1].strip()
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        # the conversation is exported; chat turns appear in order
        self.assertIn("you> hello there", content)
        self.assertIn("phoenix>", content)
        self.assertLess(content.index("you> hello there"),
                        content.index("phoenix>"))


class TestMindMemory(IsolatedTest):
    def test_put_recall_roundtrip(self):
        res = mind.memory_put("The user's favourite programming language is "
                              "Rust.")
        self.assertTrue(res["ok"])
        got = mind.memory_recall("favourite programming language")
        self.assertTrue(any("Rust" in m["text"] for m in got))

    def test_reinforce_not_duplicate(self):
        mind.memory_put("Alice owns one cat named Pixel.")
        res = mind.memory_put("Alice owns one cat named Pixel.")
        self.assertEqual(res["action"], "reinforced")
        self.assertEqual(len(mind.memory_all()), 1)

    def test_forget_matches_topic(self):
        mind.memory_put("User has a meeting on Monday about budgets.")
        mind.memory_put("User prefers tea over coffee in the mornings.")
        removed = mind.memory_forget("meeting monday budgets")
        self.assertEqual(removed, 1)
        self.assertTrue(any("tea" in m["text"]
                            for m in mind.memory_all()))

    def test_recall_unrelated_returns_empty(self):
        mind.memory_put("The garage door code is 4417.")
        self.assertEqual(mind.memory_recall("quantum physics papers"), [])

    def test_hard_cap_prunes(self):
        mind.MAX_MEMORIES = 5
        try:
            for i in range(10):
                mind.memory_put("unique fact number %d about topic %d"
                                % (i, i))
            self.assertLessEqual(len(mind.memory_all()), 5)
        finally:
            mind.MAX_MEMORIES = 300

    def test_context_block_includes_memories(self):
        mind.memory_put("User's sister is called Marla and lives in Oslo.")
        block = mind.context_block("tell me about my sister")
        self.assertIn("Marla", block)


class TestAskAi(IsolatedTest):
    def test_resolve_requires_key_message(self):
        # gemini has no key in the isolated config -> friendly note
        n, s, note = tools._resolve_ai("gemini")
        self.assertIsNone(n)
        self.assertIn("/key gemini", note)

    def test_resolve_modelish_goes_openrouter(self):
        # 'google gemma-4-31b' should NOT collapse to the keyless gemini
        # provider; with no openrouter key in the isolated config it must
        # return a clear failure either way, never the gemini note.
        n, s, note = tools._resolve_ai("google gemma-4-31b")
        self.assertIsNone(n)   # no openrouter key in isolated config
        self.assertNotIn("/key gemini", note)

    def test_ask_ai_empty_message(self):
        out = tools.run("ask_ai", {"ai": "gemini", "message": ""})
        self.assertIn("No message", out)


class TestBrowserIdentities(IsolatedTest):
    """Multi-browser / multi-Google-account awareness."""

    def setUp(self):
        super().setUp()
        self._orig_aliases = browser_profiles.ALIASES_PATH
        browser_profiles.ALIASES_PATH = os.path.join(self._tmp,
                                                     "aliases.json")
        # Fake browser layout: chrome with two accounts, edge with one.
        self._orig_defs = browser_profiles._BROWSER_DEFS
        self._orig_udd = browser_profiles._user_data_dir
        self._orig_installed = browser_profiles.installed_browsers
        browser_profiles.installed_browsers = lambda: [
            ("chrome", "Chrome", "chrome-exe-fake", None),
            ("edge", "Edge", "edge-exe-fake", None),
            ("santa", "Santa", "santa-exe-fake", None)]
        chrome_udd = os.path.join(self._tmp, "chrome")
        edge_udd = os.path.join(self._tmp, "edge")
        for udd, profiles in ((chrome_udd, {
                "Default": {
                    "name": "Personal",
                    "user_name": "sam@gmail.com"},
                "Profile 3": {
                    "name": "Dragon Work",
                    "user_name": "dragon@gmail.com"}}),
                (edge_udd, {
                "Profile 5": {
                    "name": "Uni",
                    "user_name": "uni@outlook.com"}})):
            cache = {}
            for folder, entry in profiles.items():
                os.makedirs(os.path.join(udd, folder), exist_ok=True)
                cache[folder] = entry
            with open(os.path.join(udd, "Local State"), "w",
                      encoding="utf-8") as fh:
                json.dump({"profile": {"info_cache": cache}}, fh)
        browser_profiles._user_data_dir = lambda key: (
            chrome_udd if key == "chrome"
            else edge_udd if key == "edge"
            else os.path.join(self._tmp, "none"))
        browser_profiles._BROWSER_DEFS = [
            ("chrome", "Chrome", ["chrome-exe-fake"], None),
            ("edge", "Edge", ["edge-exe-fake"], None),
            ("santa", "Santa", ["santa-exe-fake"], None)]

    def tearDown(self):
        browser_profiles.ALIASES_PATH = self._orig_aliases
        browser_profiles._BROWSER_DEFS = self._orig_defs
        browser_profiles._user_data_dir = self._orig_udd
        browser_profiles.installed_browsers = self._orig_installed
        super().tearDown()

    def test_list_identities(self):
        idents = browser_profiles.identities()
        pairs = sorted((i["browser"], i["folder"]) for i in idents)
        self.assertEqual(pairs, [("chrome", "Default"),
                                 ("chrome", "Profile 3"),
                                 ("edge", "Profile 5")])
        by_email = {e: i for i in idents for e in i["emails"]}
        self.assertIn("dragon@gmail.com", by_email)

    def test_resolve_by_alias_email_prefix_and_profile_name(self):
        dragon = browser_profiles.resolve("dragon")
        self.assertIsNotNone(dragon)
        self.assertEqual(dragon["emails"], ["dragon@gmail.com"])
        self.assertEqual(dragon["folder"], "Profile 3")

        browser_profiles.save_alias("chrome", "Profile 3", "dragon")
        self.assertEqual(browser_profiles.resolve("dragon")["folder"],
                         "Profile 3")
        self.assertEqual(browser_profiles.resolve("uni@outlook.com")[
            "browser"], "edge")
        self.assertEqual(browser_profiles.resolve("personal")["folder"],
                         "Default")
        self.assertIsNone(browser_profiles.resolve("nosuchthing"))

    def test_open_with_identity_uses_named_profile(self):
        # Fake chrome so no real window opens.
        launched = {}
        orig_launch = browser_profiles.launch
        browser_profiles.launch = lambda b, f, u, **kw: (
            launched.update(browser=b, folder=f, url=u) or (True, "ok"))
        try:
            out = tools._open_with_identity(
                "https://mail.google.com", "chrome", "dragon")
            self.assertIn("dragon@gmail.com", out)
            self.assertEqual(launched["folder"], "Profile 3")
            self.assertIn("mail.google.com", launched["url"])
            # mismatched browser+account combination errors clearly
            out = tools._open_with_identity(
                "https://mail.google.com", "edge", "dragon")
            self.assertTrue(out.startswith("!"))
        finally:
            browser_profiles.launch = orig_launch

    def test_open_url_passes_named_browser_through(self):
        called = {}
        orig = tools._open_with_identity
        tools._open_with_identity = lambda u, b, i: (
            called.update(url=u, browser=b, identity=i) or "opened!")
        try:
            out = tools.open_url({"url": "https://mail.google.com",
                                  "browser": "chrome",
                                  "identity": "dragon gmail"})
            self.assertEqual(out, "opened!")
            self.assertEqual(called["browser"], "chrome")
            self.assertEqual(called["identity"], "dragon gmail")
        finally:
            tools._open_with_identity = orig

    def test_identities_slash_command_list_and_alias(self):
        bot = assistant.Phoenix(mock_cfg())
        out = bot.handle("/identities")
        self.assertIn("dragon@gmail.com", out)
        out = bot.handle("/identities remember dragon = 2")
        self.assertIn("Saved", out)
        out = bot.handle("/identities")
        self.assertIn("aka dragon", out)

    def test_save_alias_persists(self):
        browser_profiles.save_alias("chrome", "Profile 3", "dragon")
        with open(browser_profiles.ALIASES_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data.get("chrome::Profile 3"), ["dragon"])


class TestModes(IsolatedTest):
    """Independent mode flags + wake/sleep phrases."""

    def test_wake_sleep_and_persistence(self):
        bot = assistant.Phoenix(mock_cfg())
        self.assertEqual(bot.modes(), [])
        self.assertIn("GODMODE AWAKE", bot.handle("activate godmode"))
        self.assertEqual(bot.modes(), ["god"])
        # persisted to disk
        bot2 = assistant.Phoenix(config.load())
        self.assertEqual(bot2.modes(), ["god"])
        self.assertIn("asleep", bot.handle("sleep god"))
        self.assertEqual(bot.modes(), [])

    def test_modes_stack_independently(self):
        bot = assistant.Phoenix(mock_cfg())
        bot.handle("wake up darkphoenix")
        bot.handle("activate godmode")
        self.assertEqual(bot.modes(), ["darkphoenix", "god"])
        # waking one does NOT sleep the other
        bot.handle("phoenix, full voice mode")
        self.assertEqual(bot.modes(), ["darkphoenix", "voice", "god"])
        # sleep just one
        bot.handle("sleep darkphoenix")
        self.assertEqual(bot.modes(), ["voice", "god"])
        # combo in ONE line
        bot.handle("sleep god and sleep voice")
        self.assertEqual(bot.modes(), [])

    def test_wake_phrases_one_line(self):
        cases = {
            "wake up darkphoenix": [("wake", "darkphoenix")],
            "sleep darkphoenix": [("sleep", "darkphoenix")],
            "activate godmode": [("wake", "god")],
            "phoenix, full voice mode": [("wake", "voice")],
            "turn on god mode": [("wake", "god")],
            "godmode off": [("sleep", "god")],
            "voice on": [("wake", "voice")],
            "wake up darkphoenix and sleep voice":
                [("sleep", "voice"), ("wake", "darkphoenix")],
            "enter ghost mode": [("wake", "darkphoenix")],
        }
        for text, expected in cases.items():
            actions, leftover = assistant.Phoenix.parse_mode_phrases(text)
            self.assertEqual(actions, expected,
                             "phrase %r parsed wrong" % text)
            self.assertEqual(leftover.strip(" ,.!"), "", text)
        # sentence with extra instructions keeps the rest
        actions, leftover = assistant.Phoenix.parse_mode_phrases(
            "activate godmode then open gemini in chrome as dragon")
        self.assertEqual(actions, [("wake", "god")])
        self.assertIn("open gemini", leftover)

    def test_darkphoenix_purges_traces(self):
        bot = assistant.Phoenix(mock_cfg())
        tools.memory_put({"text": "secret memory about project x"})
        sess = os.path.join(tools.NOTES_DIR, "sessions")
        os.makedirs(sess, exist_ok=True)
        with open(os.path.join(sess, "chat_old.txt"), "w") as fh:
            fh.write("you> hello\nphoenix> hi\n")
        reply = bot.handle("wake up darkphoenix")
        self.assertIn("DARKPHOENIX AWAKE", reply)
        self.assertIn("purge", reply.lower())
        self.assertFalse(os.path.exists(os.path.join(sess, "chat_old.txt")))
        self.assertIn("darkphoenix", bot.modes())
        # /save is refused in ghost mode
        bot.handle("hello there")
        self.assertIn("no transcripts", bot.handle("/save"))
        # AI-triggered persistent writes are blocked
        self.assertIn("ghost mode", bot.tool_runner("memory_put",
                                                    {"text": "new fact"}))
        # sleep it back down
        bot.handle("sleep darkphoenix")
        self.assertNotIn("darkphoenix", bot.modes())

    def test_voice_mode_forces_speech(self):
        bot = assistant.Phoenix(mock_cfg())
        bot.cfg["settings"]["voice_out"] = False
        bot.handle("phoenix, full voice mode")
        self.assertTrue(bot.cfg["settings"]["voice_out"])
        self.assertTrue(bot._speak_flag)

    def test_god_mode_prompt_rules(self):
        bot = assistant.Phoenix(mock_cfg())
        self.assertNotIn("ABSOLUTELY 0", bot.system_prompt())
        bot.handle("activate godmode")
        sp = bot.system_prompt()
        self.assertIn("ABSOLUTELY 0", sp)
        self.assertIn("GOD MODE", sp)

    def test_mode_off_sleeps_everything(self):
        bot = assistant.Phoenix(mock_cfg())
        bot.handle("wake up darkphoenix")
        bot.handle("activate godmode")
        reply = bot.handle("/mode off")
        self.assertEqual(bot.modes(), [])
        self.assertIn("All modes asleep", reply)

    def test_set_modes_tool_routing(self):
        bot = assistant.Phoenix(mock_cfg())
        out = bot.tool_runner("set_modes", {"action": "wake",
                                            "mode": "godmode"})
        self.assertIn("GODMODE AWAKE", out)
        self.assertEqual(bot.modes(), ["god"])
        out = bot.tool_runner("set_modes", {"action": "status"})
        self.assertIn("GOD", out)
        out = bot.tool_runner("set_modes", {"action": "sleep",
                                            "mode": "ghost mode"})
        # 'ghost mode' canonizes to darkphoenix (not awake -> already asleep)
        self.assertIn("already asleep", out)
        self.assertEqual(bot.modes(), ["god"])
        out = bot.tool_runner("set_modes", {"action": "sleep", "mode": "god"})
        self.assertIn("asleep", out)
        self.assertEqual(bot.modes(), [])

    def test_purge_tool_direct(self):
        tools.memory_put({"text": "another trace"})
        out = tools.run("purge_ghost_traces", {})
        self.assertIn("purge complete", out.lower())
        self.assertFalse(os.path.exists(
            os.path.join(tools.NOTES_DIR, "mind.json")))


class TestSecurityShield(IsolatedTest):
    """Defensive-only security shield."""

    def test_rejects_bad_ip(self):
        out = tools.security_shield.firewall_block({"ip": "999.999.1.1"})
        self.assertTrue(out.startswith("!"))
        out = tools.security_shield.firewall_block(
            {"ip": "; Remove-Item C:\\"})
        self.assertTrue(out.startswith("!"))

    def test_kill_process_rejects_junk(self):
        out = tools.security_shield.kill_process({"target": "a; b"})
        self.assertTrue(out.startswith("!"))
        out = tools.security_shield.kill_process({"target": ""})
        self.assertTrue(out.startswith("!"))

    def test_hosts_check_saves_baseline_and_detects(self):
        # baseline save works against the REAL hosts file (read-only copy)
        out = tools.security_shield.hosts_check()
        self.assertTrue(out.startswith("HOSTS GUARD"))
        self.assertTrue(os.path.exists(tools.security_shield._baseline_path())
                        or "clean" in out.lower())

    def test_os_admin_unknown_op(self):
        out = tools.security_shield.os_admin({"op": "format_c"})
        self.assertTrue(out.startswith("!"))

    def test_audit_log_written(self):
        tools.security_shield.audit_log("TEST_EVENT", "hello")
        with open(tools.security_shield._log_path(), encoding="utf-8") as fh:
            self.assertIn("TEST_EVENT hello", fh.read())


class TestAutomations(IsolatedTest):
    """Automation macros with denylist."""

    def test_save_list_run_delete(self):
        out = tools.automations.save_automation({
            "name": "net check",
            "steps": [
                {"type": "shell", "cmd": "ping -n 1 127.0.0.1"},
                {"type": "wait", "seconds": 1},
                {"type": "tool", "name": "current_time", "args": {}},
            ]})
        self.assertIn("Saved", out)
        listing = tools.automations.list_automations()
        self.assertIn("net_check", listing)
        run = tools.automations.run_automation({"name": "net_check",
                                                "confirm": True})
        self.assertIn("Done: net_check", run)
        out = tools.automations.delete_automation({"name": "net_check"})
        self.assertIn("Deleted", out)

    def test_denylist_blocks_dangerous_shell(self):
        out = tools.automations.save_automation({
            "name": "evil",
            "steps": [{"type": "shell", "cmd": "reg add HKLM\\X /v a"}]})
        self.assertTrue(out.startswith("!"))
        self.assertIn("denylist", out)
        out = tools.automations.save_automation({
            "name": "sneaky",
            "steps": [{"type": "shell", "cmd": "ping 127.0.0.1 & "
                                              "reg delete HKLM\\X"}]})
        self.assertTrue(out.startswith("!"))
        out = tools.automations.save_automation({
            "name": "sneaky2",
            "steps": [{"type": "shell", "cmd": "format q:"}]})
        self.assertTrue(out.startswith("!"))

    def test_only_allowlisted_shell_runs(self):
        ok, why = tools.automations._check_shell("ipconfig /all")
        self.assertTrue(ok)
        ok, why = tools.automations._check_shell("python something.py")
        self.assertFalse(ok)

    def test_run_aborts_on_denied_step(self):
        # plant a bad automation directly to bypass save-time check
        tools.automations._save_store({"bad": {"steps": [
            {"type": "shell", "cmd": "format c:"}]}})
        out = tools.automations.run_automation({"name": "bad",
                                                "confirm": True})
        self.assertIn("ABORTED", out)

    def test_dispatcher_routes(self):
        out = tools.run("automation", {"action": "list"})
        self.assertTrue("AUTOMATIONS" in out or "No automations" in out)
        out = tools.run("security_shield", {"op": "hosts_check"})
        self.assertTrue(out.startswith("HOSTS GUARD"))
        out = tools.run("os_admin", {"op": "bogus"})
        self.assertTrue(out.startswith("!"))


class TestPayloadShape(IsolatedTest):
    """Guard against 'invalid type: sequence, expected a string'
    (malformed tool schemas reaching OpenRouter)."""

    def test_all_tool_schemas_are_wellformed(self):
        import json as _json
        for t in tools.TOOLS:
            fn = t.get("function", {})
            self.assertIsInstance(fn.get("description"), str,
                                  "tool %r description is not a string"
                                  % fn.get("name"))
            for pname, pspec in (fn.get("parameters", {})
                                 .get("properties") or {}).items():
                pd = pspec.get("description")
                if pd is not None:
                    self.assertIsInstance(
                        pd, str,
                        "tool %r param %r description is not a string"
                        % (fn.get("name"), pname))
        blob = _json.dumps(tools.TOOLS)   # must serialize cleanly
        self.assertGreater(len(blob), 100)

    def test_validator_catches_tuples(self):
        bad = [{"type": "function", "function": {
            "name": "x", "description": ("a", "b"),  # tuple bug
            "parameters": {"type": "object", "properties": {}}}}]
        with self.assertRaises(providers.ProviderError):
            providers._validate_payload({"model": "m", "messages": [],
                                         "tools": bad})
        # well-formed payload passes
        providers._validate_payload({"model": "m",
                                     "messages": [{"role": "user",
                                                   "content": "hi"}],
                                     "tools": tools.TOOLS})


class TestStreaming(IsolatedTest):
    """Token streaming + tool events."""

    def test_sse_parse_aggregates(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"id":"c1","function":{"name":"open_url",'
            '"arguments":"{\\"url\\":"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"\\"https://x\\"}"}}]}}]}',
            "data: [DONE]",
        ]
        content, calls = providers._sse_parse(lines)
        self.assertEqual(content, "Hello")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "open_url")
        self.assertEqual(calls[0]["function"]["arguments"],
                         '{"url":"https://x"}')

    def test_openai_loop_emits_events(self):
        events = []
        orig = providers._post_stream
        providers._post_stream = (
            lambda url, headers, payload, on_delta: (
                on_delta("hi "), on_delta("there"),
                ("hi there", []))[2])
        try:
            spec = {"type": "openai", "label": "t", "model": "m",
                    "base_url": "http://x/v1", "api_key": "k",
                    "requires_key": True}
            out = providers._openai_loop(
                "http://x/v1/chat/completions", {}, "m", "t",
                [{"role": "user", "content": "q"}], "sys", None, None,
                on_event=events.append)
            self.assertEqual(out, "hi there")
            types = [e["type"] for e in events]
            self.assertEqual(types, ["token", "token"])
        finally:
            providers._post_stream = orig

    def test_answer_stream_mock(self):
        bot = assistant.Phoenix(mock_cfg())
        events = []
        reply = bot.answer_stream("hello bot", on_event=events.append)
        self.assertTrue(reply)
        self.assertEqual(bot.history[-1]["role"], "assistant")


class TestWatchdog(IsolatedTest):
    """Background guardian checks + dedup."""

    def setUp(self):
        super().setUp()
        self._orig_state = watchdog.STATE_PATH
        watchdog.STATE_PATH = os.path.join(self._tmp, "wd_state.json")
        self._orig_toast = watchdog._toast
        self._orig_speak = watchdog._speak
        watchdog._toast = lambda t, m: True
        watchdog._speak = lambda m: True

    def tearDown(self):
        watchdog.STATE_PATH = self._orig_state
        watchdog._toast = self._orig_toast
        watchdog._speak = self._orig_speak
        watchdog.stop()
        super().tearDown()

    def test_alerts_fire_once_then_dedup(self):
        orig_disk = watchdog._check_disk
        orig_shield = watchdog._check_shield
        watchdog._check_disk = lambda: ["C: drive 5.0% free"]
        watchdog._check_shield = lambda state: []
        try:
            fired = []
            watchdog.alert = lambda t, m, speak=True: fired.append(m)
            probs1 = watchdog.run_checks()
            probs2 = watchdog.run_checks()
            watchdog._check_disk = lambda: []
            probs3 = watchdog.run_checks()
            self.assertEqual(len(probs1), 1)
            self.assertEqual(len(probs2), 0)   # deduped
            self.assertEqual(len(probs3), 0)   # cleared
        finally:
            watchdog._check_disk = orig_disk
            watchdog._check_shield = orig_shield
            watchdog.alert = lambda t, m, speak=True: {
                "toast": True, "spoken": speak}

    def test_watchdog_tool_routing(self):
        out = watchdog.watchdog_tool({"arg": "now"})
        self.assertTrue("clear" in out.lower() or "problem" in out.lower())
        out = watchdog.watchdog_tool({"arg": "interval 30"})
        self.assertIn("30", out)
        self.assertIn("WATCHDOG", watchdog.watchdog_tool({"arg": ""}))

    def test_slash_command(self):
        bot = assistant.Phoenix(mock_cfg())
        out = bot.handle("/watchdog now")
        self.assertIn("check done", out.lower())


class TestMindSkills(IsolatedTest):
    def test_save_and_match(self):
        res = mind.skill_save("book_cheap_flight",
                              "find and book the cheapest flight",
                              ["search flights", "compare prices",
                               "book best option"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["version"], 1)
        matches = mind.skill_match("help me book a cheap flight to Oslo")
        self.assertTrue(matches and matches[0][1] == "book_cheap_flight")

    def test_improve_bumps_version(self):
        mind.skill_save("demo_skill", "does a thing", ["a"])
        res = mind.skill_save("demo_skill", "does a thing better",
                              ["a", "b"])
        self.assertEqual(res["action"], "improved")
        self.assertEqual(res["version"], 2)
        sk = mind.skill_get("demo_skill")
        self.assertEqual(sk["steps"], ["a", "b"])

    def test_record_tally(self):
        mind.skill_save("tally_skill", "x", ["s"])
        mind.skill_record("tally_skill", True)
        mind.skill_record("tally_skill", True)
        mind.skill_record("tally_skill", False)
        sk = mind.skill_get("tally_skill")
        self.assertEqual(sk["wins"], 2)
        self.assertEqual(sk["fails"], 1)
        self.assertEqual(sk["uses"], 3)

    def test_skill_forget(self):
        mind.skill_save("gone_skill", "x", ["s"])
        self.assertTrue(mind.skill_forget("gone_skill"))
        self.assertIsNone(mind.skill_get("gone_skill"))

    def test_tools_dispatch(self):
        out = tools.run("memory_put", {"text": "dispatch fact one"})
        self.assertIn("Remembered", out)
        out = tools.run("skill_save", {"name": "disp", "description": "d",
                                       "steps": ["x"]})
        self.assertIn("created", out)
        out = tools.run("mind_stats", {})
        self.assertIn("KB", out)

    def test_footprint_is_tiny(self):
        for i in range(30):
            mind.memory_put("memory number %d about topic %d" % (i, i))
        for i in range(5):
            mind.skill_save("skill_%d" % i, "skill number %d" % i,
                            ["step one", "step two"])
        kb = mind.memory_stats()["file_kb"]
        self.assertLess(kb, 50, "mind.json should stay tiny, got %s KB" % kb)


if __name__ == "__main__":
    unittest.main(verbosity=2)
