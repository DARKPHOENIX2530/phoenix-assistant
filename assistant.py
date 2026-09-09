"""The Phoenix assistant: chat loop, slash commands, memory, tools.

The model itself is small glue - all the real "thinking" happens on the
active free-API provider. Everything here runs on the user's machine and
stays light (it idles at a few dozen MB of RAM, fine on an 8 GB laptop).
"""
import datetime
import getpass
import os
import platform
import re

import config
import mind
import providers
import tools
import voice
import ui

PROJECT = os.path.dirname(os.path.abspath(__file__))

# Modes are INDEPENDENT FLAGS: darkphoenix, voice and god can be active
# at the same time ("wake up darkphoenix and activate godmode").
MODES = ("darkphoenix", "voice", "god")

# How each mode can be named in plain speech.
_MODE_ALIAS_RE = {
    "darkphoenix": r"dark\s*phoenix|ghost(?:\s*mode|\s*protocol)?|darkphoenix",
    "voice": r"full\s*voice|voice\s*mode|voice\s*only|voice",
    "god": r"god\s*mode|godmode|god",
}
_WAKE_VERBS = (r"wake\s*up|wake|activate|turn\s*on|enable|enter|engage|"
               r"go\s*into|start|bring\s*up|on")
_SLEEP_VERBS = (r"sleep|turn\s*off|shut\s*down|disable|exit|leave|"
                r"stand\s*down|deactivate|kill|stop|off")


class Phoenix:
    def __init__(self, cfg):
        self.cfg = cfg
        self.history = []          # list of {"role","content", ...}
        self._speak_flag = cfg["settings"].get("voice_out", False)
        self.last_kind = "ai"      # "ai" for model replies, "cmd" for slash commands

    # ------------------------------------------------------------------ #
    # Plumbing
    # ------------------------------------------------------------------ #
    def active(self):
        name = self.cfg["settings"].get("provider", "mock")
        return name, self.cfg["providers"].get(name)

    # ------------------------------------------------------------------ #
    # Modes: independent flags (darkphoenix / voice / god)
    # ------------------------------------------------------------------ #
    def modes(self):
        """Active modes as a canonical-order list. Reads the legacy single
        'mode' setting for back-compat."""
        raw = self.cfg["settings"].get("modes")
        if not isinstance(raw, list):
            legacy = str(self.cfg["settings"].get("mode") or "").lower()
            raw = [legacy] if legacy in MODES else []
        return [m for m in MODES if m in raw]

    def _mode_status_line(self):
        active = self.modes()
        if not active:
            return "Modes: none (normal)."
        return "Modes: " + ", ".join(m.upper() for m in active) + "."

    def wake_mode(self, name):
        """Turn one mode ON (idempotent). Returns a human reply."""
        name = self._canon_mode(name)
        if not name:
            return "! Unknown mode %r. Modes: %s." % (name, ", ".join(MODES))
        cur = self.cfg["settings"].setdefault("modes", [])
        if name in cur:
            return "%s is already awake." % name.upper()
        cur.append(name)
        self._save()
        if name == "darkphoenix":
            try:
                purge = tools.purge_ghost_traces()
            except Exception as exc:
                purge = "! ghost purge failed: %s" % exc
            return "DARKPHOENIX AWAKE - ghost protocol on. %s" % purge
        if name == "god":
            return ("GODMODE AWAKE. Every action stays 100% free - paid "
                    "anything is refused, always. Full toolset, chained "
                    "until the task is done.")
        if name == "voice":
            self.cfg["settings"]["voice_out"] = True
            self._speak_flag = True
            self._save()
            return ("FULL VOICE AWAKE. HUD is reactor-only, mic is live: "
                    "just speak. Say 'sleep voice' (or type) to stand down.")
        return "%s awake." % name

    def sleep_mode(self, name):
        """Turn one mode OFF (idempotent)."""
        name = self._canon_mode(name)
        if not name:
            return "! Unknown mode."
        cur = self.cfg["settings"].setdefault("modes", [])
        if name not in cur:
            return "%s is already asleep." % name.upper()
        cur.remove(name)
        self._save()
        if name == "darkphoenix":
            return ("DARKPHOENIX asleep - ghost protocol off. (Traces were "
                    "already purged at wake-up; nothing was kept since.)")
        if name == "god":
            return "GODMODE asleep - back to normal tool manners."
        if name == "voice":
            return "FULL VOICE asleep - text input back to normal."
        return "%s asleep." % name

    @staticmethod
    def _canon_mode(name):
        n = (name or "").strip().lower().replace("-", " ")
        n = re.sub(r"\bmode\b", "", n).strip()
        if re.fullmatch(r"dark\s*phoenix|ghost(\s*mode|\s*protocol)?|"
                        r"darkphoenix", n):
            return "darkphoenix"
        if re.fullmatch(r"(full\s*voice|voice\s*mode|voice\s*only|voice)", n):
            return "voice"
        if re.fullmatch(r"god\s*mode|godmode|god", n):
            return "god"
        return None

    @staticmethod
    def parse_mode_phrases(line):
        """Find wake/sleep mode commands inside a normal sentence.

        Returns (actions, leftover_text) where actions is a list of
        ("wake"|"sleep", mode). Understands:
          'phoenix, full voice mode'      -> wake voice
          'wake up darkphoenix'           -> wake darkphoenix
          'sleep darkphoenix'             -> sleep darkphoenix
          'activate godmode'              -> wake god
          ...and several woke combos in ONE line, joined by and/then/+
        """
        low = " " + re.sub(r"\s+", " ", (line or "").lower()) + " "
        actions = []
        consumed = []

        def alias_pat(alias):
            return "(?:%s)" % _MODE_ALIAS_RE[alias]

        # verb + mode (+ optional trailing 'mode')
        verb = r"(?:%s)" % _WAKE_VERBS
        sverb = r"(?:%s)" % _SLEEP_VERBS
        pat_wake = re.compile(
            r"\b(%s)\s*(?:up\s*)?(?:the\s*|to\s*|in\s*)?(%s)(\s*mode)?\b"
            % (_WAKE_VERBS,
               "|".join(_MODE_ALIAS_RE[a] for a in MODES)))
        pat_sleep = re.compile(
            r"\b(%s)\s*(?:the\s*|down\s*|from\s*|off\s*)?(%s)(\s*mode)?\b"
            % (_SLEEP_VERBS,
               "|".join(_MODE_ALIAS_RE[a] for a in MODES)))

        def classify(alias_match):
            text = alias_match.group(0)
            if re.search(r"dark\s*phoenix|ghost|darkphoenix", text):
                return "darkphoenix"
            if re.search(r"god", text):
                return "god"
            return "voice"

        for m in pat_wake.finditer(low):
            actions.append(("wake", classify(m)))
            consumed.append(m.span())
        for m in pat_sleep.finditer(low):
            # ignore if this span already woke ("on/off" collisions)
            if any(s <= m.start() < e for s, e in consumed):
                continue
            actions.append(("sleep", classify(m)))
            consumed.append(m.span())

        # reversed forms: 'voice on' / 'godmode off' / 'darkphoenix on'
        for alias in MODES:
            pat_on = re.compile(r"\b(%s)(\s*mode)?\s+on\b" % alias_pat(alias))
            pat_off = re.compile(r"\b(%s)(\s*mode)?\s+off\b"
                                 % alias_pat(alias))
            for m in pat_on.finditer(low):
                if any(s <= m.start() < e for s, e in consumed):
                    continue
                actions.append(("wake", alias))
                consumed.append(m.span())
            for m in pat_off.finditer(low):
                if any(s <= m.start() < e for s, e in consumed):
                    continue
                actions.append(("sleep", alias))
                consumed.append(m.span())

        # vocative form: "phoenix, full voice mode" / "phoenix: god mode"
        voc = re.search(r"\bphoenix\s*[,:!]([^\n]{0,60})", low)
        if voc:
            clause = voc.group(1)
            if not re.search(r"(?:%s)" % _SLEEP_VERBS, clause):
                for alias in MODES:
                    if re.search(alias_pat(alias), clause):
                        actions.append(("wake", alias))
                        consumed.append(voc.span())
                        break

        # de-dup, keep order: sleeps first so 'wake up x and sleep y' is
        # deterministic; wakes last (purge runs after any sleeps)
        seen, ordered = set(), []
        for act, mode in actions:
            if (act, mode) not in seen:
                seen.add((act, mode))
                ordered.append((act, mode))
        ordered.sort(key=lambda a: 0 if a[0] == "sleep" else 1)

        leftover = low
        if consumed:
            for s, e in sorted(consumed, reverse=True):
                leftover = leftover[:s] + " " + leftover[e:]
            leftover = re.sub(r"\b(and|then|also|\+)\b", " ", leftover)
            leftover = re.sub(r"\s+", " ", leftover).strip()
        if leftover.strip(" .!,?") in ("phoenix", ""):
            leftover = ""
        return ordered, leftover

    def _apply_mode_actions(self, actions):
        parts = []
        for act, mode in actions:
            out = self.wake_mode(mode) if act == "wake" \
                else self.sleep_mode(mode)
            parts.append(out)
        return parts

    def switch_mode(self, mode):
        """Explicit /mode command: wake a mode WITHOUT sleeping the others
        (modes overlap), or 'off'/'normal' to sleep everything."""
        mode = (mode or "").strip().lower()
        if mode in ("", "off", "none", "normal", "exit"):
            changed = [self.sleep_mode(m) for m in self.modes()]
            self.cfg["settings"]["modes"] = []
            self.cfg["settings"].pop("mode", None)
            self._save()
            return (" ".join(changed) + " All modes asleep. Back to "
                    "normal.").strip()
        canon = self._canon_mode(mode)
        if not canon:
            return ("! Unknown mode %r. Modes: %s - or 'off' for all off. "
                    "Wake phrases like 'wake up darkphoenix' also work."
                    % (mode, ", ".join(MODES)))
        return self.wake_mode(canon) + " " + self._mode_status_line()

    def _save(self):
        config.save(self.cfg)

    def system_prompt(self):
        name, spec = self.active()
        label = spec.get("label", name) if spec else name
        model = spec.get("model", "?") if spec else "?"
        today = datetime.datetime.now().strftime("%A, %B %d, %Y")
        os_name = platform.system() or "this computer"
        if platform.system() == "Windows":
            os_name = "Windows %s" % tools.windows_release()

        text = (
            "You are Phoenix, a personal AI assistant. You run as "
            "a lightweight local Python program on the user's computer "
            "(%s - modest hardware, all heavy AI inference happens on a "
            "free cloud API, so never suggest running large local models). "
            "Today is %s. Current brain: %s with model %s.\n"
            % (os_name, today, label, model)
            + "Personality: warm, direct, concise. No fluff, no lecture. "
            "Answer in the user's language.\n"
            + "Capabilities you actually have (call these tools when they "
            "help, and tell the user honestly if you cannot do something):\n"
            + " - web_search: live web results - USE IT when facts may be "
            "stale or you are unsure (news, prices, docs, today's anything).\n"
            + " - current_time / system_info: local clock and PC stats.\n"
            + " - open_url: open a site in their browser. If they name a "
            "BROWSER (chrome/edge/santa) and/or an ACCOUNT nickname or email "
            "(e.g. 'open dragon gmail in chrome'), pass browser= and "
            "identity= so the EXACT browser + Google profile opens. Call "
            "browser_identities (action=list) first when unsure which "
            "profile a nickname means.\n"
            + " - browser_identities: list the Google accounts signed into "
            "their browsers with saved nicknames, or save_alias to remember "
            "'dragon = chrome Profile 3'.\n"
            + " - open_app: launch a known Windows app.\n"
            + " - manage_notes: save facts/todos/anything to disk notes that "
            "survive restarts. Prefer appending to the note named 'memory' "
            "for facts the user explicitly wants remembered.\n"
            + " - memory_put/memory_recall/memory_forget: your long-term "
            "mind (auto-recalled each message).\n"
            + " - skill_save/skill_run/skill_record: your self-evolving "
            "skill library.\n"
            + " - switch_model / switch_provider: change which AI model or "
            "provider you think with, whenever the user asks (e.g. 'use "
            "the nemotron ultra model') or when the current one fails.\n"
            + " - ask_ai: send a message to a DIFFERENT AI (gemini, groq, "
            "github/gpt, cerebras, ollama, claude, or any OpenRouter model) "
            "and get its reply. When the user says 'ask Gemini' or 'talk to "
            "ChatGPT', use ask_ai and then RELAY what that AI said - stay "
            "Phoenix, do not pretend to be the other AI and do not switch "
            "your own brain for this.\n"
            + " - web_read: open a chat site (ChatGPT, Gemini, Deepseek...), "
            "send a message to the AI THERE, wait, and READ ITS REPLY back "
            "as text you can relay. Use when the user wants another site's "
            "AI to actually answer (e.g. 'talk to ChatGPT on its site and "
            "tell me what it says'). Prefer official domains; pass "
            "profile='edge' to reuse the user's logins, or browser=/"
            "identity= when they named a specific browser/account. Takes "
            "~30-60 s.\n"
            + " - web_login: when a site needs a login the user does not have "
            "in their browser profile yet, open it with web_login and ask "
            "the user to log in MANUALLY in the window that appears. The "
            "session is then saved for web_read/web_send (profile='phoenix'). "
            "NEVER type passwords, PINs or OTP codes yourself - that is "
            "refused on purpose.\n"
            + " - web_send: open a page in the user's REAL browser, TYPE a "
            "message into it and press Enter (uses their logged-in session; "
            "the site's reply cannot be read back). When the user says 'open "
            "gemini.google.com and ask/tell it ...' or 'type this into "
            "ChatGPT's site', they mean web_send - NOT open_url, NOT your "
            "own answer, NOT a provider switch. If they named a browser or "
            "account ('open gemini in chrome as dragon and ask it how it "
            "is'), pass the same browser= and identity= to web_send so the "
            "message is typed under THAT Google identity.\n"
            + " - FIND-THEN-MESSAGE chains: when the user asks like 'find a "
            "free site to talk to <AI> and message it', do web_search first, "
            "pick the OFFICIAL domain for that AI (e.g. chatgpt.com for "
            "OpenAI models, gemini.google.com for Gemini) over third-party "
            "free-chat mirrors, then use web_send to type their message "
            "there. If the user wants proof it is the real AI, include that "
            "in the typed message or say which URL you chose and why.\n"
            + "Rules: do not invent tool results; if a tool fails say so. "
            "Never ask for or print the user's API keys. Be mindful that the "
            "user may switch providers between messages.\n"
        )
        if "god" in self.modes():
            text += (
                "\nGOD MODE ACTIVE - the user explicitly authorized full "
                "tool freedom: chain as many tools as the task needs, "
                "multi-step and end-to-end, until the task is DONE (web "
                "searches, opening browsers/identities, typing into sites, "
                "reading replies, notes, apps - all of it, no asking "
                "permission between steps). ABSOLUTE RULE: everything must "
                "cost the user ABSOLUTELY 0 - free tiers, free APIs, local "
                "resources only. If any step would cost money (paid API, "
                "subscription, purchase, trial that auto-bills), STOP and "
                "tell the user - never spend. Never type passwords/OTP "
                "even here.\n")
        if "darkphoenix" in self.modes():
            text += (
                "\nDARK PHOENIX (ghost protocol) ACTIVE: the user wants NO "
                "evidence left after this session. Saved transcripts and "
                "the persistent mind file have already been purged and "
                "auto-save is disabled. Do NOT write memories, notes, "
                "transcripts or persistent artifacts (use memory_put / "
                "manage_notes / skill_save / /save only if the user "
                "re-asks explicitly); do not leave temp files; close "
                "browser windows you open when a task ends. Speak plainly "
                "about what was wiped.\n")
        if "voice" in self.modes():
            text += (
                "\nFULL VOICE MODE: the user is talking, not typing. Keep "
                "answers SHORT and speakable (2-5 sentences), no lists "
                "or code walls - describe rather than dump.\n")
        return text
        mem = tools._read_note_raw("memory")
        if mem.strip():
            text += ("\n[Persistent memory so far - keep it updated with "
                     "manage_notes when the user tells you new lasting "
                     "facts]\n" + mem[:1500])

        # Mind: relevant long-term memories + known skills + user prefs.
        last_user = ""
        for msg in reversed(self.history):
            if msg.get("role") == "user":
                last_user = msg.get("content") or ""
                break
        ctx = mind.context_block(last_user)
        if ctx:
            text += ("\n[MIND - long-term memory + self-evolved skills. "
                     "Use memory_recall/memory_put to search or add. Reuse "
                     "matched skills via skill_run and call skill_record "
                     "afterwards. Save genuinely reusable new skills with "
                     "skill_save.]\n" + ctx)
        return text

    def tool_runner(self, name, args):
        if "darkphoenix" in self.modes() and name in (
                "memory_put", "skill_save", "skill_record", "web_login"):
            return ("(ghost mode: I am not writing memories, skills or "
                    "browser-profile data. Ask me explicitly to leave "
                    "ghost mode first if you want that saved.)")
        if name == "purge_ghost_traces" and \
                "darkphoenix" not in self.modes():
            return ("(purge_ghost_traces only runs while darkphoenix is "
                    "awake.)")
        try:
            out = tools.run(name, args)
        except Exception as exc:
            out = "Tool error: %s" % exc
        return str(out)

    def _trim(self):
        limit = max(2, int(self.cfg["settings"].get("max_history", 16)) * 2)
        while len(self.history) > limit:
            self.history.pop(0)
        # Never leave a dangling tool message / open tool call at the front.
        while self.history and (
                self.history[0].get("role") == "tool"
                or self.history[0].get("tool_calls")):
            self.history.pop(0)

    # ------------------------------------------------------------------ #
    # The answer path
    # ------------------------------------------------------------------ #
    def answer(self, user_text):
        self.history.append({"role": "user", "content": user_text})
        name, spec = self.active()
        try:
            reply = providers.chat(
                spec, self.history, self.system_prompt(),
                tools.TOOLS, self.tool_runner)
        except providers.ProviderError as exc:
            reply = self._failover(exc, name, spec)
            if reply is None:
                self.history.pop()  # keep history clean; user may retry
                hint = ""
                if spec and spec.get("type") != "mock":
                    hint = ("\n(Fix it with /key, /model or /provider, or "
                            "type /provider mock to keep chatting offline.)")
                return "! %s%s" % (exc, hint)
        except KeyboardInterrupt:
            self.history.pop()  # user aborted mid-call; forget the half-ask
            return "(interrupted - say it again, or Ctrl+C at the prompt to quit)"

        reply = (reply or "").strip()
        if not reply:
            reply = "(empty reply from provider)"
        self.history.append({"role": "assistant", "content": reply})
        self._trim()
        return reply

    # ---- automatic failover ------------------------------------------- #
    _BUSY_MARKS = ("429", "overloaded", "temporarily", "rate limited",
                   "upstream error", "503", "502", "timeout")

    def _failover(self, exc, name, spec):
        """On a busy/overloaded upstream model, try sibling free models.

        Only for openrouter (which has many equivalent free models) and
        only when the error looks transient. Returns the reply string or
        None if failover did not help (caller shows the original error).
        """
        if not spec or name != "openrouter":
            return None
        err = str(exc).lower()
        if not any(mark in err for mark in self._BUSY_MARKS):
            return None
        old_model = spec.get("model", "")
        ids = [i for i in tools._or_model_ids(spec.get("api_key", ""))
               if i.endswith(":free") and i != old_model]
        if not ids:
            return None
        # prefer same family first (e.g. nvidia -> nvidia), then others
        family = old_model.split("/")[0]
        ids.sort(key=lambda i: (0 if i.startswith(family + "/") else 1, i))
        for candidate in ids[:4]:
            spec["model"] = candidate
            try:
                reply = providers.chat(
                    spec, self.history, self.system_prompt(),
                    tools.TOOLS, self.tool_runner)
            except providers.ProviderError:
                continue
            reply = (reply or "").strip()
            if reply:
                self.history.append({"role": "assistant", "content": reply})
                self._trim()
                return (reply + "\n\n(auto-switched to %s - %s was "
                        "overloaded; /model %s switches back)"
                        % (candidate, old_model, old_model))
            return None
        spec["model"] = old_model
        return None

    def speak_reply(self, text):
        if self._speak_flag:
            voice.speak(text)

    # ------------------------------------------------------------------ #
    # Entry point used by the REPL
    # ------------------------------------------------------------------ #
    def handle(self, line):
        """Handle one user line. Returns the reply string, or None to quit."""
        line = line.strip()
        if not line:
            return ""
        if not line.startswith("/"):
            # wake/sleep phrases work even inside a longer sentence
            actions, leftover = self.parse_mode_phrases(line)
            if actions:
                parts = self._apply_mode_actions(actions)
                leftover = (leftover or "").strip(" ,.!?")
                if leftover:
                    parts.append(self.answer(leftover))
                reply = "\n".join(parts)
                self.last_kind = "cmd"
                if self._speak_flag:
                    self.speak_reply(reply)
                return reply
            self.last_kind = "ai"
            reply = self.answer(line)
            self.speak_reply(reply)
            return reply
        self.last_kind = "cmd"
        cmd, _, rest = line.partition(" ")
        cmd = cmd.lstrip("/").lower()   # '/time' -> 'time'
        rest = rest.strip()
        reply = self.command(cmd, rest)
        if reply is None:
            return None
        if cmd not in ("say",) and self._speak_flag and self.last_kind == "ai":
            self.speak_reply(reply)
        return reply

    # ------------------------------------------------------------------ #
    # Slash commands
    # ------------------------------------------------------------------ #
    def command(self, cmd, rest):
        if cmd in ("quit", "exit", "bye"):
            return None

        if cmd in ("help", "?"):
            return self._help_text()

        if cmd == "provider":
            return self._cmd_provider(rest)

        if cmd == "model":
            return self._cmd_model(rest)

        if cmd == "key":
            return self._cmd_key(rest)

        if cmd in ("new", "clear", "reset"):
            self.history = []
            return "Conversation memory cleared."

        if cmd == "setup":
            return self._cmd_setup(rest)

        if cmd == "add-provider":
            return self._cmd_add_provider(rest)

        if cmd == "search":
            return tools.web_search({"query": rest or " "})

        if cmd == "time":
            return tools.current_time()

        if cmd == "sys":
            return tools.system_info()

        if cmd == "models":
            import config as _cfgmod
            cfg2 = _cfgmod.load()
            pname = cfg2["settings"].get("provider", "")
            spec2 = cfg2["providers"].get(pname) or {}
            if pname != "openrouter":
                return ("Active provider is %s; model listing is for "
                        "openrouter." % (pname or "?"))
            ids = tools._or_model_ids(spec2.get("api_key", ""))
            free = [i for i in ids if i.endswith(":free")]
            if not free:
                return "Could not fetch the OpenRouter model list right now."
            head = [i for i in free if i.startswith("nvidia/")][:6]
            rest = [i for i in free if not i.startswith("nvidia/")][:14]
            show = head + rest
            return ("Free models now (active: %s):\n  "
                    "nvidia first:\n  " % spec2.get("model", "?")) \
                + "\n  ".join(show)

        if cmd == "mode":
            return self.switch_mode(rest)

        if cmd in ("modes", "modestatus"):
            active = self.modes()
            return (self._mode_status_line() + "\n"
                    "  wake: 'phoenix, <mode> mode' / 'wake up <mode>' / "
                    "'activate godmode'\n"
                    "  sleep: 'sleep <mode>' / '<mode> off' / /mode off")

        if cmd == "purge":
            if "darkphoenix" not in self.modes():
                return ("! /purge only works while DARKPHOENIX is awake (it "
                        "deletes transcripts, mind and Phoenix's browser "
                        "profile). Wake it: 'wake up darkphoenix'.")
            return tools.purge_ghost_traces({})

        if cmd == "app":
            return tools.open_app({"app": rest})

        if cmd in ("identities", "identity"):
            if not rest:
                return tools.browser_identities_tool({"action": "list"})
            m = re.match(r"(?s)(?:remember|alias|save)\s+(\S+)\s*"
                         r"(?:=|as|is|->)\s*(.+)", rest)
            if m:
                return tools.save_identity_alias(m.group(1), m.group(2))
            return ("Usage: /identities   list your browser Google "
                    "identities\n"
                    "       /identities remember dragon = 2   save a "
                    "nickname (2 = list number)")

        if cmd == "remember":
            return tools.manage_notes(
                {"action": "append", "name": "memory", "content": rest})

        if cmd == "forget":
            return tools.manage_notes(
                {"action": "delete", "name": "memory", "content": ""})

        if cmd == "memory":
            if not rest:
                s = mind.memory_stats()
                return ("Mind: %d memories, %d skills, %s KB "
                        "(notes/mind.json).\n"
                        "  /memory add <fact>   remember something\n"
                        "  /memory find <text>  search memories\n"
                        "  /memory drop <text>  forget matching memories"
                        % (s["memories"], s["skills"], s["file_kb"]))
            sub, _, arg = rest.partition(" ")
            sub = sub.lower()
            if sub in ("add", "remember"):
                return tools.memory_put({"text": arg})
            if sub in ("find", "search"):
                return tools.memory_recall_tool({"query": arg})
            if sub in ("drop", "forget", "del"):
                return tools.memory_forget_tool({"match": arg})
            return "Usage: /memory [add|find|drop] <text>"

        if cmd in ("skills",):
            return tools.skill_list_tool({})

        if cmd == "skill":
            if not rest:
                return ("Usage: /skill <name-or-goal>   (fetch and show a "
                        "saved skill; the AI reuses it automatically)\n"
                        "       /skill drop <name>   delete a skill")
            if rest.lower().startswith("drop "):
                return tools.skill_forget_tool(
                    {"name": rest[5:].strip()})
            return tools.skill_run_tool({"goal": rest})

        if cmd == "mind":
            s = mind.memory_stats()
            lines = ["PHOENIX MIND - %d memories, %d skills, %s KB on disk "
                     "(notes/mind.json)" % (s["memories"], s["skills"],
                                            s["file_kb"]),
                     "  Kinds: " + (", ".join("%s=%d" % (k, v) for k, v in
                                               sorted(s["kinds"].items()))
                                    or "none"),
                     "  No background processes. Loaded only while answering."]
            skills = mind.skill_list()
            if skills:
                lines.append("  Top skills:")
                for sk in skills[:5]:
                    lines.append("    - %s (v%s, %s uses, win %s)"
                                 % (sk["name"], sk["version"], sk["uses"],
                                    sk["win_rate"]))
            return "\n".join(lines)

        if cmd in ("note", "write"):
            name, _, content = rest.partition(" ")
            if not content:
                return "Usage: /note <name> <text to save>"
            return tools.manage_notes(
                {"action": "write", "name": name, "content": content})

        if cmd in ("read", "note?"):
            return tools.manage_notes(
                {"action": "read", "name": rest or "note", "content": ""})

        if cmd in ("notes", "list"):
            return tools.manage_notes({"action": "list"})

        if cmd == "save":
            return self._cmd_save()

        if cmd == "voice":
            return self._cmd_voice(rest)

        if cmd == "listen":
            text = voice.listen(
                device=self.cfg["settings"].get("mic_device") or None)
            if not text:
                return "(did not catch that - nothing heard or no mic)"
            return "You said: " + text + "\n" + self.answer(text)

        if cmd == "mic":
            sub = rest.lower()
            if sub.startswith("use") or sub == "auto":
                target = rest[3:].strip() if sub.startswith("use") else "auto"
                self.cfg["settings"]["mic_device"] = target or "auto"
                self._save()
                idx, name = voice._pick_device(
                    target if target not in ("", "auto") else None)
                if idx is None:
                    return "Mic problem: %s" % name
                return ('Mic set to: %s. /listen and the HUD mic button '
                        'will use it.' % name)
            try:
                rows = voice.list_mics()
            except Exception as exc:
                return "Mic diagnostics failed: %s" % exc
            if not rows:
                return ("No mic devices found. Install once with: python -m "
                        "pip install SpeechRecognition pyaudio")
            lines = ["MIC DEVICES (say something while it probes):",
                     "  index  live  device"]
            for m in rows:
                lines.append("  [%2d]   %s  %s%s"
                             % (m["index"], "YES" if m["alive"] else "no ",
                                m["name"][:48],
                                "  < default" if m["is_default"] else ""))
            lines.append("\nIf the default shows 'no', Phoenix auto-uses a "
                         "live one. Force one with: /mic use <index-or-name>")
            return "\n".join(lines)

        if cmd == "say":
            ok = voice.speak(rest)
            return ("(spoken)" if ok else "(speech unavailable - text above "
                    "is all I have)")
        if cmd == "mock":
            self.cfg["settings"]["provider"] = "mock"
            self._save()
            return "Switched to offline mock mode. /setup reconnects a free AI."

        return "Unknown command /%s. Type /help for the list." % cmd

    # ---- individual command implementations --------------------------- #
    def _help_text(self):
        return (
            "PHOENIX COMMANDS\n"
            "  /setup              one-time wizard: pick a free AI + paste key\n"
            "  /provider [name]    show or switch AI (gemini, groq, openrouter,\n"
            "                      github, cerebras, ollama, mock)\n"
            "  /model [name]       show or change the active model\n"
            "  /models             list free OpenRouter models right now\n"
            "  /key [name] [key]   set an API key (prompts if you omit it)\n"
            "  /add-provider <n> <url> <model>   add any OpenAI-compatible\n"
            "                      endpoint (e.g. your own vLLM/LM Studio)\n"
            "  /new                clear conversation memory\n"
            "  /mode <name>         wake a mode: darkphoenix (ghost, violet "
            "HUD, zero traces),\n"
            "                       voice (reactor-only, mic always on), god "
            "(full tools,\n                       everything must cost 0). /mode off sleeps "
            "all.\n"
            "  /modes               show which modes are awake\n"
            "  (say: 'wake up darkphoenix', 'sleep darkphoenix',\n"
            "   'activate godmode', 'phoenix, full voice mode' - combos OK)\n"
            "\n"
            "  Just talk to me for questions, writing, coding, summaries.\n"
            "  I also act: say things like 'search the web for ...', 'open\n"
            "  youtube.com', 'open calculator', 'remember that ...'.\n"
            "\n"
            "TOOLS (also usable directly)\n"
            "  /search <query>      web search now\n"
            "  /time  /sys          clock / PC stats\n"
            "  /app <name>          open an app (notepad, calc, browser...)\n"
            "  /identities          list Google accounts in your browsers\n"
            "  /identities remember <nick> = <#|email>   nickname one\n"
            "  (then say things like 'open dragon gmail in chrome')\n"
            "  /remember <fact>     save to persistent memory\n"
            "  /forget              wipe persistent memory\n"
            "  /memory [add|find|drop] <text>   mind memory: add / search / "
            "forget\n"
            "  /skills              list self-evolved skills\n"
            "  /skill <goal>        fetch a saved skill by name or goal\n"
            "  /mind                mind stats (footprint + top skills)\n"
            "  /note <n> <text>     save a note    /read <n>  /notes\n"
            "  /save                export this conversation to a text file\n"
            "\n"
            "VOICE\n"
            "  /voice               toggle spoken replies (Windows TTS)\n"
            "  /voice in            mic mode: press Enter on an empty line to\n"
            "                       talk (needs SpeechRecognition + pyaudio)\n"
            "  /listen              dictate one message right now\n"
            "  /mic                 diagnose mics (shows which are live)\n"
            "  /mic use <index|name|auto>  force a mic (e.g. /mic use 2)\n"
            "  /say <text>          speak text directly\n"
            "  /quit                exit Phoenix")

    def _cmd_provider(self, rest):
        if not rest:
            name, spec = self.active()
            has_key = bool(spec and spec.get("api_key"))
            return ("Active provider: %s (%s)\n"
                    "  model: %s\n  key set: %s\n"
                    "Available: %s\n"
                    "Switch with: /provider <name>\n"
                    "Add key with: /key <name> <APIKEY>  (or /setup)" % (
                        name,
                        spec.get("label", "?") if spec else "missing",
                        spec.get("model", "?") if spec else "?",
                        "yes" if has_key else "NO",
                        ", ".join(sorted(self.cfg["providers"].keys()))))
        name = rest.lower()
        spec = self.cfg["providers"].get(name)
        if not spec:
            return ('No provider "%s". Known: %s' %
                    (name, ", ".join(sorted(self.cfg["providers"].keys()))))
        self.cfg["settings"]["provider"] = name
        self._save()
        return ('Switched to %s (%s, model %s).' %
                (name, spec.get("label", "?"), spec.get("model", "?")))

    def _cmd_model(self, rest):
        name, spec = self.active()
        if not rest:
            return ("Active model: %s" %
                    (spec.get("model", "?") if spec else "?"))
        if not spec:
            return "Active provider has no spec."
        spec["model"] = rest
        self._save()
        return 'Model for "%s" set to %s.' % (name, rest)

    def _cmd_key(self, rest):
        name, spec = self.active()
        parts = rest.split(None, 1)
        target = parts[0] if parts else name
        value = parts[1].strip() if len(parts) > 1 else ""
        spec_target = self.cfg["providers"].get(target.lower())
        if not spec_target:
            return ('No provider "%s". Known: %s' %
                    (target, ", ".join(sorted(self.cfg["providers"].keys()))))
        if not value:
            try:
                value = getpass.getpass(
                    "Paste API key for %s (hidden): " % target)
            except (EOFError, KeyboardInterrupt):
                return "Cancelled."
            value = value.strip()
        if not value:
            return "No key entered - nothing changed."
        spec_target["api_key"] = value
        self._save()
        return 'Key stored for "%s" (kept in config/config.json).' % target

    def _cmd_voice(self, rest):
        r = rest.lower()
        if r in ("in", "mic", "input"):
            on = not self.cfg["settings"].get("voice_in", False)
            self.cfg["settings"]["voice_in"] = on
            self._save()
            if on:
                return ("Voice INPUT: ON. From now on, press Enter on an empty "
                        "line and speak. Needs SpeechRecognition + pyaudio "
                        "installed once (see /help).")
            return "Voice INPUT: OFF."
        if r in ("off", "0"):
            self._speak_flag = False
            self.cfg["settings"]["voice_out"] = False
            self._save()
            return "Speaking replies: OFF."
        # no argument (or 'out'): toggle speaking replies
        self._speak_flag = not self._speak_flag
        self.cfg["settings"]["voice_out"] = self._speak_flag
        self._save()
        return ("Speaking replies: ON. /voice again to turn off."
                if self._speak_flag
                else "Speaking replies: OFF.")

    def _cmd_save(self):
        if "darkphoenix" in self.modes():
            return ("! Ghost mode: no transcripts are written. Evidence "
                    "purge is the whole point.")
        if not self.history:
            return "Nothing to save - the conversation is empty."
        sess_dir = os.path.join(tools.NOTES_DIR, "sessions")
        try:
            os.makedirs(sess_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            path = os.path.join(sess_dir, "chat_%s.txt" % ts)
            with open(path, "w", encoding="utf-8") as fh:
                for msg in self.history:
                    role = "you" if msg.get("role") == "user" else "phoenix"
                    fh.write("%s> %s\n\n" % (role, msg.get("content") or ""))
        except OSError as exc:
            return "Could not save: %s" % exc
        return "Conversation saved to %s" % path

    def _cmd_setup(self, _rest):
        """Interactive wizard: pick provider, optionally set model + key."""
        lines = [
            "SETUP - pick which free AI should power Phoenix.",
            "Every key is free to create (see README for sign-up links).",
        ]
        keyed = {n: p for n, p in self.cfg["providers"].items()
                 if p.get("requires_key", True)}
        names = sorted(keyed)
        for i, n in enumerate(names, 1):
            p = keyed[n]
            lines.append("  %d) %s  (model %s)"
                         % (i, p.get("label", n), p.get("model", "?")))
        lines.append("  0) cancel")
        print("\n".join(lines))

        try:
            choice = input("Pick a number (or 0 to cancel): ").strip()
            if choice == "0" or choice == "":
                return "Setup cancelled. You can still use /provider mock."
            idx = int(choice) - 1
            if idx < 0 or idx >= len(names):
                return "Bad choice."
            name = names[idx]
        except (ValueError, EOFError, KeyboardInterrupt):
            return "Setup cancelled."

        spec = self.cfg["providers"][name]
        try:
            new_model = input("Model [%s]: " % spec.get("model", "")).strip()
            if new_model:
                spec["model"] = new_model
            key = getpass.getpass("Paste your free API key (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            return "Setup cancelled."
        if not key:
            return 'No key entered - try again with /setup or /key %s.' % name

        spec["api_key"] = key
        self.cfg["settings"]["provider"] = name
        self._save()
        return ('Done! Active provider is now %s (model %s).\n'
                'Say hello, or try: "search the web for today\'s news".' %
                (name, spec.get("model", "?")))

    def _cmd_add_provider(self, rest):
        parts = rest.split(None, 2)
        if len(parts) < 3:
            return ("Usage: /add-provider <name> <base-url> <model>\n"
                    "e.g. /add-provider mylocal http://192.168.1.5:8000/v1 "
                    "qwen2.5\n"
                    "Any OpenAI-compatible endpoint works. Add its key with "
                    "/key <name>.")
        name = parts[0].lower()
        if not re_safe(name):
            return "Name must be letters/digits/underscore."
        base_url = parts[1].rstrip("/")
        model = parts[2]
        self.cfg["providers"][name] = {
            "type": "openai",
            "label": name,
            "model": model,
            "base_url": base_url,
            "api_key": "",
            "requires_key": True,
        }
        self._save()
        return ('Added provider "%s" (%s, model %s).\n'
                "Now set its key with: /key %s <APIKEY>  then:\n"
                "  /provider %s" % (name, base_url, model, name, name))


def re_safe(name):
    import re
    return bool(re.fullmatch(r"[a-zA-Z0-9_]+", name))
