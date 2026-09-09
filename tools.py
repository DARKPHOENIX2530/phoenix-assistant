"""Local tools the assistant (or the user) can run.

Each tool is a function(args_dict) -> str. The same implementations back
both the AI function-calling loop and the user's slash commands, so they
behave identically whether Phoenix decides to act or you type the command.

Free / no-extra-account services are preferred (web search runs through
DuckDuckGo's HTML endpoint; nothing else needs an account).
"""
import ctypes
import datetime
import html as html_lib
import os
import platform
import re
import subprocess
import urllib.parse
import webbrowser

import requests

import browser_profiles

NOTES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": ("Search the web (DuckDuckGo) for up-to-date "
                            "information. Use whenever the answer may be "
                            "stale, news-related, or unknown to you."),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string",
                                         "description": "Search query"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "current_time",
            "description": "Get the current local date and time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_info",
            "description": "Get basic info about this computer (OS, CPU, RAM).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": ("Open a website in the user's default browser. "
                            "If the user names a BROWSER (chrome/edge/santa) "
                            "and/or an ACCOUNT/nickname (e.g. 'dragon'), set "
                            "browser/identity - Phoenix opens that browser "
                            "pinned to that profile so the right Google "
                            "account is signed in. Use browser_identities "
                            "first when unsure what exists."),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "Full URL to open"},
                    "browser": {"type": "string",
                                "description": "chrome, edge or santa - when "
                                "the user names a browser"},
                    "identity": {"type": "string",
                                 "description": "Account nickname, email or "
                                 "profile name when the user names one (e.g. "
                                 "'dragon', 'work@gmail.com')"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_identities",
            "description": ("List the Google accounts/profiles found in the "
                            "user's installed browsers (Chrome, Edge, Santa) "
                            "with saved nicknames, or SAVE a nickname for one "
                            "('remember that dragon = my chrome Profile 3'). "
                            "Call before opening an identity the user named, "
                            "if you are not sure which profile it is."),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list", "save_alias"],
                               "description": "list (default) or save_alias"},
                    "browser": {"type": "string", "description": "For "
                                "save_alias: chrome/edge/santa"},
                    "folder": {"type": "string", "description": "For "
                               "save_alias: profile folder, e.g. 'Profile 3'"},
                    "alias": {"type": "string", "description": "For "
                              "save_alias: nickname, e.g. 'dragon'"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": ("Launch a safe, known app on Windows "
                            "(notepad, calc, explorer, cmd, chrome, edge, "
                            "taskmgr, paint, wordpad)."),
            "parameters": {
                "type": "object",
                "properties": {"app": {"type": "string",
                                       "description": "App name or alias"}},
                "required": ["app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_notes",
            "description": ("Read, write, append or list plain-text notes "
                            "stored on this machine. Notes survive restarts. "
                            "Use for remembering facts, to-do items and "
                            "anything the user wants saved. The special note "
                            "name 'memory' is the persistent memory file."),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list", "read", "write", "append",
                                        "delete"]},
                    "name": {"type": "string", "description": "Note name "
                             "(default 'note'). 'memory' is persistent "
                             "memory."},
                    "content": {"type": "string",
                                "description": "Text for write/append"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a specific location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City or location name"}
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_clipboard",
            "description": "Read from or write to the system clipboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write"]},
                    "content": {"type": "string", "description": "Text to write to clipboard (if action is write)"}
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_control",
            "description": "Control system media playback and volume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["play", "pause", "next", "previous", "mute", "volume_up", "volume_down"]}
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "power_options",
            "description": "Put the computer to sleep, restart, or shut down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["sleep", "restart", "shutdown"]}
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_code",
            "description": "Edit the assistant's own Python source code safely. Replaces exact text. Automatically tests 5 times and reverts if broken.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "The file to edit (e.g. 'tools.py')"},
                    "search_string": {"type": "string", "description": "Exact code snippet to replace"},
                    "replacement_string": {"type": "string", "description": "New code to insert"}
                },
                "required": ["filename", "search_string", "replacement_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_put",
            "description": ("Save a durable fact, preference or event to "
                            "long-term memory (survives restarts, auto-de-"
                            "duplicated). Use for things the user wants you "
                            "to remember about them or their world."),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "The fact to remember, one "
                             "self-contained sentence"},
                    "kind": {"type": "string",
                             "enum": ["fact", "pref", "event", "skill_note"],
                             "description": "fact=default; pref=user "
                             "preference; event=scheduled/important thing"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_recall",
            "description": ("Search long-term memory for facts relevant to "
                            "a topic. Use before answering personal questions "
                            "like 'what do you know about me'."),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string",
                                         "description": "Topic or keywords"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_forget",
            "description": ("Delete long-term memories matching a topic. "
                            "Use when the user asks you to forget something."),
            "parameters": {
                "type": "object",
                "properties": {"match": {"type": "string",
                                         "description": "Topic/keywords of "
                                         "memories to delete"}},
                "required": ["match"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_save",
            "description": ("Save a reusable skill: a named recipe of steps "
                            "that achieved a goal, so future similar goals "
                            "are solved faster. Call this after solving "
                            "something non-trivial. Saving an existing name "
                            "improves it (bumps the version)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short "
                             "snake_case name, e.g. 'book_cheap_flight'"},
                    "description": {"type": "string",
                                    "description": "What goal this achieves"},
                    "steps": {"type": "array",
                              "items": {"type": "string"},
                              "description": "Ordered steps, each concrete "
                              "and self-contained (max 12)"},
                },
                "required": ["name", "description", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_run",
            "description": ("Fetch the best saved skill for a goal and "
                            "return its steps to execute. Prefer this over "
                            "re-deriving a plan when a similar skill exists."),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "What the "
                             "user wants done"},
                    "name": {"type": "string", "description": "Optional "
                             "exact skill name"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_record",
            "description": ("Report whether a saved skill worked when you "
                            "ran it. Skills with high win rates are preferred "
                            "and evolved; failing ones decay away."),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "success": {"type": "boolean",
                                "description": "true if the goal was achieved"},
                },
                "required": ["name", "success"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": ("List all self-evolved skills with versions, "
                            "usage counts and win rates."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_forget",
            "description": "Delete a saved skill by name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string",
                                        "description": "Skill name"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_model",
            "description": ("Switch the AI model the assistant currently "
                            "thinks with. Use when the user asks to "
                            "activate/use/change a model. On OpenRouter, "
                            "approximate names are matched to real model "
                            "ids (preferring :free ones)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string",
                              "description": "Model id or close name, e.g. "
                              "'nvidia/nemotron-3-ultra-550b-a55b:free' or "
                              "'nemotron ultra'"},
                },
                "required": ["model"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_provider",
            "description": ("Switch the AI provider/brain (gemini, groq, "
                            "openrouter, github, cerebras, ollama, mock). "
                            "Use when the user asks to change provider or go "
                            "offline."),
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string",
                                 "description": "Provider name"},
                },
                "required": ["provider"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_send",
            "description": ("Open a web page in the user's real browser, "
                            "TYPE a message into it and press Enter - the "
                            "site receives the text like a human typing it. "
                            "Use when the user says things like 'open "
                            "gemini.google.com and ask/tell it ...' - they "
                            "want the WEBSITE itself to get the message, "
                            "not your own answer. Uses their logged-in "
                            "browser session. The site's reply cannot be "
                            "read back."),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "Page to open (defaults to "
                            "Gemini's web app)"},
                    "text": {"type": "string",
                             "description": "The exact message to type and "
                             "send"},
                    "wait_seconds": {"type": "integer",
                                     "description": "Page-load wait before "
                                     "typing (default 7)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_login",
            "description": ("Open a site in Phoenix's own browser window so "
                            "the USER can log in manually (Gmail, ChatGPT, "
                            "etc.). The session is saved, and later "
                            "web_read/web_send calls with profile='phoenix' "
                            "reuse it. NEVER ask the user for their password "
                            "and NEVER type credentials yourself - point "
                            "the user here when a site needs login. If the "
                            "user names a browser/identity instead, prefer "
                            "opening THEIR browser+profile with open_url - "
                            "their login already exists there."),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "Site to log in to (default: "
                            "Gmail)"},
                    "timeout_seconds": {"type": "integer",
                                        "description": "How long to keep the "
                                        "login window open (default 300)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_read",
            "description": ("Open a chat website (ChatGPT, Gemini, "
                            "Deepseek, ...), send the user's message to the "
                            "AI ON THAT SITE, wait, and READ ITS REPLY back "
                            "as text. Like web_send but the reply comes "
                            "back to you so you can relay it. Prefer the "
                            "official domain. Use profile='edge' to reuse "
                            "the user's logged-in sessions. Use when the "
                            "user wants another AI's actual answer."),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "Chat site URL (default: Gemini "
                            "web app)"},
                    "text": {"type": "string",
                             "description": "Message to send that site's AI"},
                    "wait_seconds": {"type": "integer",
                                     "description": "Max seconds to wait for "
                                     "the reply (default 25)"},
                    "browser": {"type": "string",
                                "description": "chrome/edge/santa when the user "
                                "named a browser - types into THAT browser with "
                                "THAT profile's logged-in account"},
                    "identity": {"type": "string",
                                 "description": "Account nickname, email or "
                                 "profile name when the user named one (e.g. "
                                 "'dragon') - uses that exact Google identity"},
                    "profile": {"type": "string",
                                "enum": ["phoenix", "edge", "chrome", "chromium"],
                                "description": "Browser profile: 'phoenix' "
                                "reuses sessions saved via web_login (use "
                                "this after the user logged in once); edge/"
                                "chrome reuse that browser's own profile"},
                    "browser": {"type": "string",
                                "description": "chrome/edge/santa when the user "
                                "named a browser - overrides 'profile' and "
                                "uses their real account(s)"},
                    "identity": {"type": "string",
                                 "description": "Account nickname, email or "
                                 "profile name when the user named one (e.g. "
                                 "'dragon') - picks the exact Google identity"},
                    "headless": {"type": "boolean",
                                 "description": "Hide the browser window "
                                 "(default false - show it)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_ai",
            "description": ("Send a message to a DIFFERENT AI and get its "
                            "reply. Use when the user says things like 'ask "
                            "Gemini', 'talk to ChatGPT', or wants two AIs to "
                            "converse. Do NOT switch your own brain for "
                            "this - keep being Phoenix and relay what the "
                            "other AI said."),
            "parameters": {
                "type": "object",
                "properties": {
                    "ai": {"type": "string",
                           "description": "Which AI to ask: gemini, groq, "
                           "github(gpt), cerebras, ollama, claude, or any "
                           "OpenRouter model name"},
                    "message": {"type": "string",
                                "description": "The exact message to send "
                                "that AI"},
                },
                "required": ["ai", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mind_stats",
            "description": ("Report the mind's footprint: memory count, "
                            "skill count and disk usage of notes/mind.json."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_APPS = {
    "notepad": "notepad", "editor": "notepad",
    "calc": "calc", "calculator": "calc",
    "paint": "mspaint", "mspaint": "mspaint",
    "wordpad": "write", "write": "write",
    "cmd": "cmd", "terminal": "cmd", "console": "cmd", "command": "cmd",
    "powershell": "powershell",
    "explorer": "explorer", "files": "explorer", "file": "explorer",
    "chrome": "chrome", "browser": "chrome",
    "edge": "msedge", "msedge": "msedge",
    "taskmgr": "taskmgr", "task manager": "taskmgr", "taskman": "taskmgr",
    "camera": "start microsoft.windows.camera:",
    "settings": "start ms-settings:",
}


def _safe_note_name(name):
    name = (name or "note").strip().lower()
    name = re.sub(r"[^a-z0-9._-]+", "_", name)
    name = name.strip("._")
    if not name or ".." in name:
        raise ValueError("Bad note name: %r" % (name,))
    return name


def _note_path(name):
    os.makedirs(NOTES_DIR, exist_ok=True)
    safe = _safe_note_name(name)
    if not safe.endswith((".md", ".txt")):
        safe += ".md"
    return os.path.join(NOTES_DIR, safe)


def _read_note_raw(name):
    path = _note_path(name)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def manage_notes(args):
    action = (args.get("action") or "list").lower()
    name = args.get("name") or "note"
    content = str(args.get("content") or "")

    if action == "list":
        os.makedirs(NOTES_DIR, exist_ok=True)
        entries = sorted(os.listdir(NOTES_DIR))
        if not entries:
            return "No notes yet. Use /remember <fact> or /note <name> <text>."
        lines = ["Notes on disk (%s):" % NOTES_DIR]
        for entry in entries:
            path = os.path.join(NOTES_DIR, entry)
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8",
                              errors="replace") as fh:
                        nlines = sum(1 for _ in fh)
                except OSError:
                    nlines = 0
                lines.append("  - %s (%d lines)" % (entry, nlines))
        return "\n".join(lines)

    path = _note_path(name)

    if action == "read":
        if not os.path.exists(path):
            return 'No note named "%s" yet.' % name
        body = _read_note_raw(name)
        if len(body) > 3000:
            body = body[:3000] + "\n... [truncated]"
        return "--- %s ---\n%s" % (os.path.basename(path), body)

    if action in ("write", "append"):
        if not content.strip():
            return "Nothing written - content was empty."
        os.makedirs(NOTES_DIR, exist_ok=True)
        if action == "write":
            text = content
        else:
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            existing = _read_note_raw(name)
            text = existing
            if text and not text.endswith("\n"):
                text += "\n"
            text += "\n[%s] %s\n" % (stamp, content)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return 'Saved to "%s".' % path

    if action == "delete":
        if os.path.exists(path):
            os.remove(path)
            return 'Deleted "%s".' % path
        return 'No note named "%s".' % name

    raise ValueError('Unknown action "%s".' % action)


def web_search(args, max_results=5):
    query = str(args.get("query") or "").strip()
    if not query:
        return "Empty search query."
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return ("Web search failed: %s. Tell the user the search is "
                "unavailable right now and answer from what you know." % exc)

    body = resp.text
    title_matches = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
    snippet_matches = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</a>', body, re.S)

    def clean(frag):
        frag = re.sub(r"<[^>]+>", "", frag)
        return html_lib.unescape(frag).strip()

    results = []
    for i, (href, title) in enumerate(title_matches):
        if len(results) >= max_results:
            break
        real = ""
        try:
            parsed = urllib.parse.urlparse(href)
            params = urllib.parse.parse_qs(parsed.query)
            if params.get("uddg"):
                real = params["uddg"][0]
            elif params.get("u3"):
                real = params["u3"][0]
        except ValueError:
            pass
        # Skip DuckDuckGo ad/tracking redirects; keep organic results only.
        if "y.js" in href or "ad_domain" in href or not real:
            continue
        snippet = clean(snippet_matches[i]) if i < len(snippet_matches) else ""
        results.append("%d. %s\n   %s%s" % (
            len(results) + 1, clean(title), real,
            ("\n   " + snippet) if snippet else ""))

    if not results:
        return ("No results found for %r. Tell the user politely and offer "
                "to rephrase." % query)
    return "Search results for %r:\n%s" % (query, "\n".join(results))


def current_time(_args=None):
    now = datetime.datetime.now()
    return ("It is %s on %s." % (now.strftime("%I:%M %p").lstrip("0"),
                                 now.strftime("%A, %B %d, %Y")))


def _ram_windows():
    try:
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MemoryStatus()
        stat.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total = stat.ullTotalPhys / (1024 ** 3)
            avail = stat.ullAvailPhys / (1024 ** 3)
            return total, avail
    except Exception:
        pass
    return None


def windows_release():
    """Return '11', '10', etc.

    platform.release() lies on Windows 11 (it reports '10'); the real
    tell is the build number (Win 11 starts at 22000).
    """
    if os.name != "nt":
        return platform.release()
    try:
        import sys
        build = sys.getwindowsversion().build
    except Exception:
        return platform.release()
    return "11" if build >= 22000 else platform.release()


def system_info(_args=None):
    uname = platform.uname()
    lines = [
        "Host: %s" % uname.node,
        "OS: %s %s (%s)" % (uname.system, windows_release(), uname.machine),
        "Python: %s" % platform.python_version(),
        "CPU cores: %s" % os.cpu_count(),
    ]
    if os.name == "nt":
        ram = _ram_windows()
        if ram:
            total, avail = ram
            lines.append("RAM: %.1f GB total, %.1f GB free"
                         % (total, avail))
    else:
        try:
            with open("/proc/meminfo", "r") as fh:
                for line in fh:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        lines.append("RAM: %.1f GB total" % (kb / (1024 ** 2)))
                        break
        except OSError:
            pass
    return "\n".join(lines)


def _format_idents(candidates):
    lines = []
    for i, ident in enumerate(candidates, 1):
        email = ident["emails"][0] if ident["emails"] else "(no Google account)"
        alias = (" (aka %s)" % ", ".join(ident["aliases"])) \
            if ident["aliases"] else ""
        lines.append("  %d. %s [%s] %s - %s%s"
                     % (i, ident["browser_display"], ident["folder"],
                        ident["profile_name"], email, alias))
    return "\n".join(lines)


def _resolve_identity(query, candidates):
    """Match a free-text mention ('dragon', 'work@gmail.com') to one of the
    given identity dicts, or None."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for ident in candidates:                       # exact alias wins
        if q in [a.lower() for a in ident["aliases"]]:
            return ident
    for ident in candidates:                       # exact email
        if q in [e.lower() for e in ident["emails"]]:
            return ident
    for ident in candidates:                       # email prefix
        if any(e.split("@")[0].lower() == q for e in ident["emails"]):
            return ident
    for ident in candidates:                       # profile / folder name
        if q == ident["folder"].lower() or \
                (len(q) >= 3 and q in ident["profile_name"].lower()):
            return ident
    for token in re.split(r"[^a-z0-9._@+-]+", q):  # "dragon gmail" -> dragon
        if len(token) < 3:
            continue
        for ident in candidates:
            if token in [a.lower() for a in ident["aliases"]] or \
                    any(token == e.split("@")[0].lower()
                        for e in ident["emails"]):
                return ident
    return None


def _identity_launch_spec(browser_key, identity_query):
    """Resolve browser+identity args to one identity dict, or return an
    error string explaining what IS available."""
    bkey = (browser_key or "").strip().lower()
    if bkey and bkey not in ("chrome", "edge", "santa"):
        return ("! Unknown browser %r - I can drive chrome, edge or santa."
                % browser_key)
    candidates = [i for i in browser_profiles.identities()
                  if not bkey or i["browser"] == bkey]
    if not candidates:
        return ("! No %s profiles with Google accounts found on this PC. "
                "Sign in once in %s, then tell me again - or say 'list my "
                "browser identities'."
                % (bkey or "Chrome/Edge/Santa", bkey or "one of them"))
    if identity_query:
        ident = _resolve_identity(identity_query, candidates)
        if ident is None:
            return ("! No account matches %r. I can see:\n%s\n"
                    "Say 'remember that <nickname> = <number above>' and I "
                    "will save the nickname."
                    % (identity_query, _format_idents(candidates)))
        return ident
    # browser named but no identity: prefer the Default profile
    return next((i for i in candidates if i["folder"].lower() == "default"),
                candidates[0])


def _open_with_identity(url, browser_key, identity_query):
    """Open url in the NAMED browser pinned to the NAMED Google identity."""
    got = _identity_launch_spec(browser_key, identity_query)
    if isinstance(got, str):
        return got
    ident = got
    ok, msg = browser_profiles.launch(ident["browser"], ident["folder"], url)
    if not ok:
        return msg
    who = ident["emails"][0] if ident["emails"] else ident["profile_name"]
    note = "" if ident["emails"] else \
        " (sign in once in that window - no Google account on it yet)"
    return ("Opened %s in %s profile %r as %s%s. If %s was already running, "
            "I closed it first so the right profile wins."
            % (url, ident["browser_display"], ident["folder"], who, note,
               ident["browser_display"]))


def open_url(args):
    raw = str(args.get("url") or "").strip()
    if not raw:
        return "No URL given."
    # Reject junk before it ever reaches the browser.
    if any(ch.isspace() for ch in raw):
        return "That does not look like a valid URL (%r)." % raw
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    host = urllib.parse.urlparse(raw).hostname
    if not host or ("." not in host and host.lower() != "localhost"):
        return ("That does not look like a valid URL (%r) - try a domain "
                "like example.com or paste the whole URL." % raw)
    browser_key = str(args.get("browser") or "").strip().lower()
    identity_query = str(args.get("identity") or "").strip()
    if browser_key or identity_query:
        return _open_with_identity(raw, browser_key, identity_query)
    try:
        opened = webbrowser.open(raw)
    except webbrowser.Error:
        opened = False
    return ("Opened %s in your browser." % raw) if opened \
        else ("Could not open a browser window for %s." % raw)


def save_identity_alias(alias, mention):
    """Remember that 'alias' (e.g. 'dragon') means the identity the user
    is pointing at - by number from /identities, email, or profile name."""
    idents = browser_profiles.identities()
    if not idents:
        return ("No Chrome/Edge/Santa profiles found. Sign in once in one "
                "of them, then try again.")
    mention = (mention or "").strip()
    if re.fullmatch(r"\d+", mention):
        idx = int(mention) - 1
        if not 0 <= idx < len(idents):
            return "! No identity number %s (I see %d - run /identities)." \
                % (mention, len(idents))
        ident = idents[idx]
    else:
        ident = _resolve_identity(mention, idents)
        if ident is None:
            return ("! Nothing matches %r. Run /identities to see the list, "
                    "then use its number." % mention)
    got = browser_profiles.save_alias(ident["browser"], ident["folder"],
                                      alias)
    who = ident["emails"][0] if ident["emails"] else ident["profile_name"]
    return ("Saved: '%s' now means %s profile %r (%s). Try 'open %s gmail "
            "in %s'." % (got, ident["browser_display"], ident["folder"],
                          who, got, ident["browser"]))


def browser_identities_tool(args):
    """List (or nickname) the Google identities found in local browsers."""
    action = str(args.get("action") or "list").strip().lower()
    if action == "save_alias":
        browser_key = str(args.get("browser") or "").strip().lower()
        folder = str(args.get("folder") or "").strip()
        alias = str(args.get("alias") or "").strip()
        if not (browser_key and folder and alias):
            return ("! save_alias needs browser (chrome/edge/santa), folder "
                    "(e.g. 'Profile 3') and alias (e.g. 'dragon'). Run "
                    "action=list first to see the folders.")
        got = browser_profiles.save_alias(browser_key, folder, alias)
        return ("Saved: '%s' now means %s profile %r. Say 'open %s gmail in "
                "%s' and I will use exactly that."
                % (got, browser_key, folder, alias, browser_key))
    idents = browser_profiles.identities()
    if not idents:
        return ("No Chrome/Edge/Santa profiles with Google accounts found. "
                "Sign in once in one of those browsers, then ask me again.")
    return ("I can see %d browser identit%s. Name one (nickname, email or "
            "profile name) and I will open that exact browser + account.\n%s"
            % (len(idents), "y" if len(idents) == 1 else "ies",
               _format_idents(idents)))


KNOWN_APPS = ("notepad, calc (calculator), paint, wordpad, cmd, powershell, "
              "explorer, chrome, edge, taskmgr (task manager), camera, settings")


def open_app(args):
    if os.name != "nt":
        return ("open_app only works on Windows. Suggest the user open it "
                "themselves.")
    key = str(args.get("app") or "").strip().lower()
    target = _APPS.get(key)
    if not target:
        return ('Unknown app "%s".\nKnown apps: %s' % (key, KNOWN_APPS))
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    devnull = getattr(subprocess, "DEVNULL", -3)
    try:
        subprocess.Popen(
            "start \"\" " + target, shell=True,
            stdin=devnull, stdout=devnull, stderr=devnull,
            creationflags=flags)
    except OSError as exc:
        return "Could not launch %s: %s" % (target, exc)
    return "Launched %s." % target


def get_weather(args):
    location = str(args.get("location") or "").strip()
    if not location:
        return "No location provided."
    try:
        url = "https://wttr.in/" + urllib.parse.quote(location) + "?format=j1"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        current = data["current_condition"][0]
        desc = current["weatherDesc"][0]["value"]
        temp = current["temp_C"]
        feels = current["FeelsLikeC"]
        return "Current weather in %s: %s, %s°C (feels like %s°C)." % (location, desc, temp, feels)
    except Exception as exc:
        return "Could not get weather: %s" % exc


def manage_clipboard(args):
    action = str(args.get("action") or "read").lower()
    content = str(args.get("content") or "")
    if os.name != "nt":
        return "Clipboard tool only supported on Windows."
    
    try:
        if action == "write":
            process = subprocess.Popen("clip", stdin=subprocess.PIPE, shell=True)
            process.communicate(content.encode("utf-16le"))
            return "Copied text to clipboard."
        else:
            output = subprocess.check_output(["powershell", "-command", "Get-Clipboard"], text=True)
            return "Clipboard contents:\n%s" % output.strip()
    except Exception as exc:
        return "Clipboard error: %s" % exc


def media_control(args):
    action = str(args.get("action") or "").lower()
    if os.name != "nt":
        return "Media control only supported on Windows."
    
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_DOWN = 0xAE
    VK_VOLUME_UP = 0xAF
    VK_MEDIA_NEXT_TRACK = 0xB0
    VK_MEDIA_PREV_TRACK = 0xB1
    VK_MEDIA_PLAY_PAUSE = 0xB3
    
    actions = {
        "play": VK_MEDIA_PLAY_PAUSE,
        "pause": VK_MEDIA_PLAY_PAUSE,
        "next": VK_MEDIA_NEXT_TRACK,
        "previous": VK_MEDIA_PREV_TRACK,
        "mute": VK_VOLUME_MUTE,
        "volume_up": VK_VOLUME_UP,
        "volume_down": VK_VOLUME_DOWN
    }
    
    vk = actions.get(action)
    if not vk:
        return "Unknown media action: %s" % action
        
    try:
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        return "Successfully sent %s command." % action
    except Exception as exc:
        return "Failed to send media command: %s" % exc


def power_options(args):
    action = str(args.get("action") or "").lower()
    if os.name != "nt":
        return "Power options only supported on Windows."
        
    try:
        if action == "sleep":
            os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
            return "Putting computer to sleep."
        elif action == "restart":
            os.system("shutdown /r /t 5")
            return "Restarting computer in 5 seconds."
        elif action == "shutdown":
            os.system("shutdown /s /t 5")
            return "Shutting down computer in 5 seconds."
        else:
            return "Unknown power action: %s" % action
    except Exception as exc:
        return "Failed to execute power option: %s" % exc


def edit_code(args):
    filename = str(args.get("filename") or "").strip()
    search_string = str(args.get("search_string") or "")
    replacement_string = str(args.get("replacement_string") or "")
    
    if not filename.endswith(".py") or ".." in filename:
        return "Error: Can only edit local .py files."
        
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if not os.path.exists(path):
        return "Error: File %s not found." % filename
        
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        
    if search_string not in content:
        return "Error: search_string not found in file. You must provide the EXACT existing code snippet."
        
    new_content = content.replace(search_string, replacement_string, 1)
    backup = content
    
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        syntax_res = subprocess.run(["python", "-m", "py_compile", filename], capture_output=True, text=True)
        if syntax_res.returncode != 0:
            raise Exception("Syntax Error:\\n" + syntax_res.stderr)
            
        for i in range(1, 6):
            test_res = subprocess.run(["python", "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True)
            if test_res.returncode != 0:
                raise Exception("Unit tests failed on run %d:\\n%s" % (i, test_res.stderr))
                
        return "Successfully updated %s. Syntax and 5x tests passed! The new code is now active." % filename
        
    except Exception as exc:
        with open(path, "w", encoding="utf-8") as f:
            f.write(backup)
        return "Edit failed and was reverted. Reason:\\n%s" % exc


def memory_put(args):
    """Store a durable fact/preference about the user or their world."""
    import mind
    res = mind.memory_put(args.get("text") or "",
                          (args.get("kind") or "fact").lower())
    if not res.get("ok"):
        return "! Memory not saved: %s" % res.get("error")
    if res["action"] == "reinforced":
        return ('Memory reinforced (I already knew it, now I believe it '
                'harder): "%s"' % res["text"])
    return 'Remembered: "%s"' % res["text"]


def memory_recall_tool(args):
    import mind
    res = mind.memory_recall(args.get("query") or "", k=6)
    if not res:
        return "No matching memories. I still remember things across " \
               "restarts - tell me to remember and I will."
    lines = ["Recalled %d:" % len(res)]
    for m in res:
        lines.append("- (%s) %s" % (m["kind"], m["text"]))
    return "\n".join(lines)


def memory_forget_tool(args):
    import mind
    n = mind.memory_forget(args.get("match") or "")
    if not n:
        return 'No memory matched "%s" - nothing deleted.' % args.get("match")
    return "Forgot %d memory(ies) matching \"%s\"." % (n, args.get("match"))


def skill_save_tool(args):
    import mind
    res = mind.skill_save(args.get("name"), args.get("description"),
                          args.get("steps"))
    if not res.get("ok"):
        return "! Skill not saved: %s" % res.get("error")
    if res["action"] == "improved":
        return ('Skill "%s" improved to v%d. Steps: %s'
                % (res["name"], res["version"], " -> ".join(res["steps"])))
    return ('Skill "%s" created (v1). Next time a similar goal appears, '
            'I will reuse it. Steps: %s'
            % (res["name"], " -> ".join(res["steps"])))


def skill_run_tool(args):
    """Reuse a saved skill: returns its steps for the AI to execute now."""
    import mind
    goal = args.get("goal") or args.get("name") or ""
    name = (args.get("name") or "").strip()
    sk = mind.skill_get(name) if name else None
    if not sk:
        matches = mind.skill_match(goal, k=1)
        if matches:
            sk = mind.skill_get(matches[0][1])
    if not sk:
        return ('No skill matches "%s". Devise a plan, run it, then save '
                'what worked with skill_save.' % (goal or name))
    lines = ["Reuse skill %s (v%d, used %d times):"
             % (sk["name"], sk["version"], sk.get("uses", 0)),
             "Goal: %s" % sk["description"],
             "Steps:"]
    for i, step in enumerate(sk["steps"], 1):
        lines.append("  %d. %s" % (i, step))
    lines.append("Run the steps now. Then call skill_record with "
                 "success=true/false.")
    return "\n".join(lines)


def skill_record_tool(args):
    import mind
    res = mind.skill_record(args.get("name") or "",
                            bool(args.get("success")))
    if not res.get("ok"):
        return "! %s" % res.get("error")
    return ('Skill "%s": %s. Tally: %d uses, %d wins, %d fails (v%d).'
            % (res["name"], "worked" if args.get("success") else "failed",
               res["uses"], res["wins"], res["fails"], res["version"]))


def skill_list_tool(args):
    import mind
    skills = mind.skill_list()
    if not skills:
        return ("No skills yet. When I solve something non-trivial, I save "
                "the recipe so future me is faster.")
    lines = ["Self-evolved skills (%d):" % len(skills)]
    for s in skills:
        lines.append("- %s (v%s, %s uses, win %s): %s"
                     % (s["name"], s["version"], s["uses"],
                        s["win_rate"], s["description"]))
    return "\n".join(lines)


def skill_forget_tool(args):
    import mind
    if mind.skill_forget(args.get("name") or ""):
        return 'Skill "%s" deleted.' % args.get("name")
    return 'No skill named "%s".' % args.get("name")


def mind_stats_tool(args):
    import mind
    s = mind.memory_stats()
    return ("Mind: %d memories, %d skills, %s KB on disk (notes/mind.json). "
            "No background processes; loads only when answering."
            % (s["memories"], s["skills"], s["file_kb"]))


def switch_model_tool(args):
    """Change the active model. Fuzzy-matches names for OpenRouter."""
    wanted = str(args.get("model") or "").strip()
    if not wanted:
        return "! No model name given."
    import config as _cfg
    cfg = _cfg.load()
    name = cfg["settings"].get("provider", "mock")
    spec = cfg["providers"].get(name)
    if not spec:
        return "! Active provider '%s' has no spec." % name

    target = wanted
    if name == "openrouter":
        target = _match_openrouter_model(wanted, spec.get("api_key", ""))
        if not target:
            return ('! No OpenRouter model matches "%s". Try an exact id '
                    'like nvidia/nemotron-3-super-120b-a12b:free.' % wanted)

    spec["model"] = target
    _cfg.save(cfg)
    return ('Model switched to %s (provider %s). Say something to test it.'
            % (target, name))


def _or_model_ids(api_key=""):
    """All OpenRouter model ids (empty list on any error)."""
    import requests as _rq
    try:
        resp = _rq.get("https://openrouter.ai/api/v1/models",
                       headers={"Authorization": "Bearer " + api_key}
                       if api_key else {}, timeout=20)
        return [m["id"] for m in resp.json().get("data", [])]
    except Exception:
        return []


def _match_openrouter_model(wanted, api_key=""):
    """Map a loose model name to a real OpenRouter model id.

    Scoring: exact id > token overlap on the id path > substring. Free
    variants (:free) get a bonus so casual requests stay on free models.
    Returns the model id, or '' when nothing plausible matches.
    """
    import re as _re
    ids = _or_model_ids(api_key)
    if not ids:
        return ""
    wanted_l = wanted.lower()
    if wanted_l in ids:
        return wanted_l
    wtokens = set(t for t in _re.findall(r"[a-z0-9]+", wanted_l)
                  if t not in ("the", "a", "an", "model", "free", "use",
                               "switch", "activate", "please", "to"))
    best, best_score = "", -1
    for mid in ids:
        ml = mid.lower()
        mtokens = set(_re.findall(r"[a-z0-9]+", ml))
        overlap = wtokens & mtokens
        if not overlap and wanted_l not in ml:
            continue
        # all requested tokens present -> strong match; else partial
        score = len(overlap) * 2 + (3 if ":free" in mid else 0)
        if wanted_l in ml:
            score += 2
        if wtokens and wtokens.issubset(mtokens):
            score += 4
        if score > best_score:
            best, best_score = mid, score
    return best if best_score >= 4 else ""


def switch_provider_tool(args):
    import config as _cfg
    cfg = _cfg.load()
    name = str(args.get("provider") or "").strip().lower()
    if name not in cfg["providers"]:
        return ('! Unknown provider "%s". Known: %s'
                % (name, ", ".join(sorted(cfg["providers"].keys()))))
    cfg["settings"]["provider"] = name
    _cfg.save(cfg)
    spec = cfg["providers"][name]
    return ('Provider switched to %s (model %s).'
            % (name, spec.get("model", "?")))


# --------------------------------------------------------------------------- #
# talk to another AI
# --------------------------------------------------------------------------- #
_re_modelish = re.compile(
    r"[a-z0-9]+-[0-9]|gemma|llama|nemotron|inkling|lfm-|ling-|laguna|"
    r"north-mini|qwen|mistral|deepseek|kimi|glm|minimax|grok|phi-|gpt-")

_AI_ALIASES = {
    "gemini": "gemini", "google": "gemini", "bard": "gemini",
    "groq": "groq",
    "openrouter": "openrouter",
    "github": "github", "gpt": "github", "openai": "github",
    "cerebras": "cerebras",
    "ollama": "ollama", "local": "ollama",
    "claude": "claude", "anthropic": "claude",
}


def _resolve_ai(name):
    """Map a loose AI name to (provider_name, spec, note) or (None, None, tip)."""
    import config as _cfg
    cfg = _cfg.load()
    key = str(name or "").strip().lower()
    # A concrete model request (e.g. 'google gemma-4-31b') must be checked
    # on the ORIGINAL string, before the alias loop collapses it to a
    # provider name like 'gemini' and loses the model id.
    modelish = bool(_re_modelish.search(key))
    if not modelish:
        for alias, target in _AI_ALIASES.items():
            if alias in key:
                key = target
                break
    if not modelish and key in cfg["providers"] and key != "mock":
        spec = cfg["providers"][key]
        if spec.get("api_key") or not spec.get("requires_key", True):
            return key, spec, ""
        return None, None, ('%s has no API key set - add it with /key %s '
                            '<APIKEY> (or ask for a free OpenRouter model '
                            'instead)' % (key, key))
    # Not a configured provider: fall back to a free OpenRouter model.
    or_spec = cfg["providers"].get("openrouter")
    if or_spec and or_spec.get("api_key"):
        model = _match_openrouter_model(key, or_spec.get("api_key", ""))
        if model:
            probe = dict(or_spec)
            probe["model"] = model
            return "openrouter:" + model, probe, ""
        return None, None, ('No free OpenRouter model matches "%s".' % name)
    return None, None, ("I can reach: %s - or any OpenRouter model if its "
                        "key is set." % ", ".join(
                            n for n in sorted(cfg["providers"]) if n != "mock"))


# --------------------------------------------------------------------------- #
# type into a website (browser automation, zero deps)
# --------------------------------------------------------------------------- #
GEMINI_APP_URL = "https://gemini.google.com/app"

# Per-site reply extraction strategies for web_read.
# "turns"  -> chat turns in an ordered list; the last <li> is the reply;
#             its first <h4>-ish header says who spoke ("ChatGPT said:").
# "roles"  -> OpenAI-style data-message-author-role attributes.
# "custom" -> site-specific selectors.
_SITE_STRATEGY = {
    "chatgpt.com": {
        "kind": "turns",
        "box": "textarea",
        "turn_sel": "main ol li, ol li",
        "who_sel": "h4",
    },
    "chat.openai.com": {
        "kind": "turns",
        "box": "textarea",
        "turn_sel": "main ol li, ol li",
        "who_sel": "h4",
    },
    "gemini.google.com": {
        "kind": "custom",
        "box": "div.ql-editor[contenteditable='true'], rich-textarea textarea",
        "reply_sel": ["model-response", "message-content", "div[class*='response']"],
    },
    "chat.deepseek.com": {
        "kind": "custom",
        "box": "textarea",
        "reply_sel": ["div.ds-markdown", "div[class*='markdown']"],
    },
}
_GENERIC_STRATEGY = {
    "kind": "custom",
    "box": "textarea, div[contenteditable='true']",
    "reply_sel": ["[class*='assistant' i]", "[class*='response' i]",
                  "[class*='bot' i]"],
}


def _site_strategy(url):
    for domain, prof in _SITE_STRATEGY.items():
        if domain in url.lower():
            return prof
    return _GENERIC_STRATEGY


def _first_text(el, selectors):
    for sel in selectors:
        try:
            node = el.query_selector(sel)
            if node:
                t = (node.inner_text() or "").strip()
                if t:
                    return t
        except Exception:
            continue
    return ""


def _extract_reply(page, strategy):
    """Pull the latest assistant turn's text. Returns '' while pending."""
    try:
        if strategy["kind"] == "turns":
            turns = page.query_selector_all(strategy["turn_sel"])
            if not turns:
                return ""
            last = turns[-1]
            who = ""
            try:
                h = last.query_selector(strategy.get("who_sel", "h4"))
                if h:
                    who = (h.inner_text() or "").strip()
            except Exception:
                pass
            body = (last.inner_text() or "").strip()
            if who and body.startswith(who):
                body = body[len(who):].strip()
            # Only assistant turns carry a "said" header; skip our own echo
            if "said" not in who.lower():
                return ""
            return body[:1500]
        # custom selectors
        for sel in strategy["reply_sel"]:
            els = page.query_selector_all(sel)
            if els:
                t = (els[-1].inner_text() or "").strip()
                if t:
                    return t[:1500]
        return ""
    except Exception:
        return ""


def _set_clipboard(text):
    """Unicode-safe clipboard set via PowerShell. Returns bool."""
    import base64
    script = "Set-Clipboard -Value '" + str(text).replace("'", "''") + "'"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", encoded],
            capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def web_send(args):
    """Open a page in the user's browser, TYPE text into it, press Enter.

    Uses the user's real browser + their logged-in session (e.g. Google
    account for gemini.google.com/app). If browser/identity args are
    given, the NAMED browser is launched pinned to the NAMED profile, so
    the message is typed under that exact Google identity. Zero new
    dependencies: clipboard via PowerShell, keystrokes via ctypes.
    """
    url = str(args.get("url") or "").strip()
    text = str(args.get("text") or "").strip()
    if not text:
        return "! No text to send."
    if _looks_like_secret(text):
        return ("! I won't type passwords, PINs or verification codes into "
                "websites. Log in once with web_login (manual), then I can "
                "chat on the logged-in site safely.")
    if not url:
        url = GEMINI_APP_URL
    if not url.startswith(("http://", "https://")):
        return "! url must start with http:// or https://"
    if os.name != "nt":
        return "! Typing into a page is Windows-only right now."
    browser_key = str(args.get("browser") or "").strip().lower()
    identity_query = str(args.get("identity") or "").strip()
    try:
        wait = max(2, min(30, int(args.get("wait_seconds") or 7)))
    except (TypeError, ValueError):
        wait = 7

    import ctypes
    import time

    if browser_key or identity_query:
        got = _identity_launch_spec(browser_key, identity_query)
        if isinstance(got, str):
            return got
        ident = got
        ok, msg = browser_profiles.launch(ident["browser"], ident["folder"],
                                          url)
        if not ok:
            return msg
        wait = max(wait, 9)   # fresh window needs a beat more to settle
    else:
        import webbrowser
        webbrowser.open(url)
    time.sleep(wait)                       # let the page load + focus input
    if not _set_clipboard(text):
        return "! Could not put the message on the clipboard."

    u32 = ctypes.windll.user32
    KEYUP = 0x0002
    # Ctrl+V into the focused chat box
    u32.keybd_event(0x11, 0, 0, 0)         # Ctrl down
    u32.keybd_event(0x56, 0, 0, 0)         # 'V' down
    time.sleep(0.05)
    u32.keybd_event(0x56, 0, KEYUP, 0)     # 'V' up
    u32.keybd_event(0x11, 0, KEYUP, 0)     # Ctrl up
    time.sleep(0.8)                        # let the site render the paste
    u32.keybd_event(0x0D, 0, 0, 0)         # Enter down
    u32.keybd_event(0x0D, 0, KEYUP, 0)     # Enter up

    tail = ""
    if browser_key or identity_query:
        who = ident["emails"][0] if ident["emails"] else ident["profile_name"]
        tail = " (in %s, profile %r, as %s)" % (ident["browser_display"],
                                                ident["folder"], who)
    return ("Opened %s%s, typed your message and pressed Enter. The site's "
            "reply appears in the browser - I cannot read it back yet."
            % (url, tail))


# --------------------------------------------------------------------------- #
# logins + typing guardrails
# --------------------------------------------------------------------------- #
PHOENIX_PROFILE_DIR = os.path.expanduser(
    "~/AppData/Local/PhoenixBrowserProfile")

_PASSWORD_MARKS = (
    "password", "passwd", "passcode", "pwd", "otp", "pin code", "pin:",
    "one-time code", "verification code", "2fa", "recovery code",
)


def _looks_like_secret(text):
    low = str(text or "").lower()
    if any(mark in low for mark in _PASSWORD_MARKS):
        return True
    # bare OTP-style codes (4-8 digits) typed alone
    import re as _re
    if _re.fullmatch(r"\s*\d{4,8}\s*", str(text or "")):
        return True
    return False


def web_login(args):
    """Open a site in Phoenix's own browser profile for MANUAL login.

    The window stays open until the user closes it (or the timeout).
    Cookies/session persist in PHOENIX_PROFILE_DIR, so later web_read/
    web_send calls with profile='phoenix' reuse that login. Phoenix
    NEVER types passwords itself - this is the deliberate boundary.
    """
    url = str(args.get("url") or "https://mail.google.com").strip()
    if not url.startswith(("http://", "https://")):
        return "! url must start with http:// or https://"
    try:
        timeout = max(60, min(900, int(args.get("timeout_seconds") or 300)))
    except (TypeError, ValueError):
        timeout = 300

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ("! Need Playwright: python -m pip install playwright && "
                "python -m playwright install chromium")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                PHOENIX_PROFILE_DIR, headless=False,
                args=["--start-maximized"])
            page = browser.new_page()
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            print("[phoenix] Login window open for %s - log in manually. "
                  "Session is saved for later automation." % url)
            # poll until the user closes the window or timeout hits
            import time as _t
            t0 = _t.time()
            try:
                while _t.time() - t0 < timeout:
                    if not browser.pages:
                        break
                    # if the user navigated deep into the site, treat as done
                    page.wait_for_timeout(2000)
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
        return ("Login window closed. Session saved in Phoenix's browser "
                "profile - web_read/web_send with profile='phoenix' will "
                "reuse it on %s." % url.split("//", 1)[-1].split("/")[0])
    except Exception as exc:
        return "! web_login failed: %s" % exc


def web_read(args):
    """Open a chat site, send a message, WAIT, and read the AI's reply back.

    Drives a real Chromium/Edge via Playwright. If profile='edge' (or
    'chrome') is passed, uses the user's own browser profile, so their
    existing logins (Google account for Gemini, etc.) apply. Falls back
    to the bundled Chromium otherwise (fine for sites that need no
    login, or when the user logs in inside the opened window).
    """
    url = str(args.get("url") or GEMINI_APP_URL).strip()
    text = str(args.get("text") or "").strip()
    if not text:
        return "! No text to send."
    if _looks_like_secret(text):
        return ("! I won't type passwords, PINs or verification codes into "
                "websites. Log in once with web_login (manual), then I can "
                "chat on the logged-in site safely.")
    if not url.startswith(("http://", "https://")):
        return "! url must start with http:// or https://"
    try:
        wait = max(4, min(120, int(args.get("wait_seconds") or 25)))
    except (TypeError, ValueError):
        wait = 25
    profile = str(args.get("profile") or "edge").strip().lower()
    headless = bool(args.get("headless", False))
    browser_key = str(args.get("browser") or "").strip().lower()
    identity_query = str(args.get("identity") or "").strip()
    if browser_key or identity_query:
        # The user named a browser/identity - that beats the generic
        # 'profile' enum: we drive their REAL browser with THAT account.
        profile = "real"   # sentinel handled below

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ("! Reading replies needs Playwright: run "
                "python -m pip install playwright && python -m playwright "
                "install chromium")

    strategy = _site_strategy(url)
    try:
        with sync_playwright() as pw:
            try:
                if profile == "real":
                    got = _identity_launch_spec(browser_key, identity_query)
                    if isinstance(got, str):
                        return got
                    ident = got
                    exe, udd, extra = browser_profiles.playwright_launch_args(
                        ident["browser"], ident["folder"])
                    if not exe:
                        return ("! %s is not installed on this PC."
                                % (browser_key or "that browser"))
                    browser_profiles.close_running_browser(ident["browser"])
                    browser = pw.chromium.launch_persistent_context(
                        udd, executable_path=exe, headless=headless,
                        args=["--start-maximized"] + extra)
                elif profile == "phoenix":
                    browser = pw.chromium.launch_persistent_context(
                        PHOENIX_PROFILE_DIR, headless=headless,
                        args=["--start-maximized"])
                elif profile in ("edge", "chrome"):
                    exe = (("C:/Program Files (x86)/Microsoft/Edge/"
                            "Application/msedge.exe") if profile == "edge"
                           else "C:/Program Files/Google/Chrome/"
                                "Application/chrome.exe")
                    browser = pw.chromium.launch_persistent_context(
                        PHOENIX_PROFILE_DIR + "_" + profile,
                        executable_path=exe, headless=headless,
                        args=["--start-maximized"])
                else:
                    browser = pw.chromium.launch(headless=headless)
            except Exception:
                browser = pw.chromium.launch(headless=headless)

            page = browser.new_page()
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            # find the message box
            box = None
            for sel in strategy["box"].split(", "):
                try:
                    box = page.wait_for_selector(sel, timeout=15000)
                    if box:
                        break
                except Exception:
                    continue
            if box is None:
                browser.close()
                return ("! Could not find the message box on %s - the site "
                        "may need a login. Open it once in your browser, "
                        "log in, then try again (use profile=\"edge\" to "
                        "reuse your logins)." % url)

            box.click()
            page.keyboard.insert_text(text)
            page.keyboard.press("Enter")

            # poll for the reply until it appears and settles
            import time as _t
            t0 = _t.time()
            reply = ""
            while _t.time() - t0 < wait:
                reply = _extract_reply(page, strategy)
                if reply:
                    # settled? unchanged after 2.5 s means generation finished
                    page.wait_for_timeout(2500)
                    reply2 = _extract_reply(page, strategy)
                    if reply2 == reply:
                        break
                    reply = reply2 or reply
                page.wait_for_timeout(1500)

            browser.close()
            if not reply:
                return ("Sent the message to %s, but no reply appeared "
                        "within %ss (site may be slow or need a login)."
                        % (url, wait))
            return "[%s replies] %s" % (url.split("//", 1)[-1].split("/")[0],
                                        reply[:1500])
    except Exception as exc:
        return "! web_read failed: %s" % exc


def ask_ai_tool(args):
    """Send a message to ANOTHER AI and return its reply (relay, not a swap)."""
    import providers as _prov
    target = str(args.get("ai") or args.get("provider") or "").strip()
    message = str(args.get("message") or "").strip()
    if not message:
        return "! No message given to send."
    name, spec, note = _resolve_ai(target)
    if not spec:
        return "! Cannot reach '%s': %s" % (target, note)
    peer_prompt = ("You are %s, another AI assistant being asked a question "
                   "by a peer AI named Phoenix. Answer in your own voice, "
                   "briefly (max ~120 words)." % (spec.get("label") or name))
    try:
        reply = _prov.chat(spec,
                           [{"role": "user", "content": message}],
                           peer_prompt)
    except _prov.ProviderError as exc:
        # Free models are often throttled: if we asked a specific
        # OpenRouter model, retry a few sibling free models.
        if str(name).startswith("openrouter:"):
            fallback = _ask_ai_openrouter_fallback(target, message,
                                                   peer_prompt, str(exc))
            if fallback:
                return fallback
        return "! %s did not answer: %s" % (name, exc)
    reply = (reply or "").strip()
    if not reply:
        return "! %s returned an empty reply." % name
    return ('[%s says] %s' % (name, reply))


def _ask_ai_openrouter_fallback(target, message, peer_prompt, orig_err):
    """Retry the ask with other free OpenRouter models. Returns text or ''.

    If the user named a real model, honour it when it becomes reachable;
    otherwise pick healthy free models near the request.
    """
    import providers as _prov
    or_ids = [i for i in _or_model_ids() if i.endswith(":free")]
    wanted = _match_openrouter_model(target)
    candidates = [i for i in or_ids if i != wanted]
    # same family first, then everything else
    if wanted:
        fam = wanted.split("/")[0]
        candidates.sort(key=lambda i: (0 if i.startswith(fam + "/") else 1, i))
    else:
        candidates.sort()
    tried = []
    for model in candidates[:3]:
        import config as _cfg
        cfg = _cfg.load()
        probe = dict(cfg["providers"]["openrouter"])
        probe["model"] = model
        try:
            reply = _prov.chat(probe, [{"role": "user", "content": message}],
                               peer_prompt)
        except _prov.ProviderError:
            tried.append(model)
            continue
        reply = (reply or "").strip()
        if reply:
            note = (" (asked %s instead - %s was busy)"
                    % (model, wanted or "the requested model"))
            return "[%s says]%s %s" % (model, note, reply)
    return ""


def run(name, args):
    """Dispatch a tool call by name. Raises on unknown tool / bad input."""
    args = args or {}
    if name == "web_search":
        return web_search(args)
    if name == "current_time":
        return current_time(args)
    if name == "system_info":
        return system_info(args)
    if name == "open_url":
        return open_url(args)
    if name == "browser_identities":
        return browser_identities_tool(args)
    if name == "open_app":
        return open_app(args)
    if name == "manage_notes":
        return manage_notes(args)
    if name == "get_weather":
        return get_weather(args)
    if name == "manage_clipboard":
        return manage_clipboard(args)
    if name == "media_control":
        return media_control(args)
    if name == "power_options":
        return power_options(args)
    if name == "edit_code":
        return edit_code(args)
    if name == "memory_put":
        return memory_put(args)
    if name == "memory_recall":
        return memory_recall_tool(args)
    if name == "memory_forget":
        return memory_forget_tool(args)
    if name == "skill_save":
        return skill_save_tool(args)
    if name == "skill_run":
        return skill_run_tool(args)
    if name == "skill_record":
        return skill_record_tool(args)
    if name == "skill_list":
        return skill_list_tool(args)
    if name == "skill_forget":
        return skill_forget_tool(args)
    if name == "mind_stats":
        return mind_stats_tool(args)
    if name == "switch_model":
        return switch_model_tool(args)
    if name == "switch_provider":
        return switch_provider_tool(args)
    if name == "ask_ai":
        return ask_ai_tool(args)
    if name == "web_send":
        return web_send(args)
    if name == "web_read":
        return web_read(args)
    if name == "web_login":
        return web_login(args)
    raise ValueError("Unknown tool: %s" % name)
