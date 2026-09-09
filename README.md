# Phoenix - personal assistant (lightweight edition)

A personal AI assistant, **sized for your actual machine**: Windows 11,
Ryzen 3 7320U (integrated graphics), 8 GB RAM. It does **not** try to run an
LLM locally — that would crawl on this hardware. Instead the *brain* lives on
**free cloud AI APIs**
(no payment method needed), and everything else runs locally and weighs
almost nothing (~50 MB RAM). You can even run it fully offline in mock mode.

```
you> who won the cricket match last night?
Phoenix (calls web_search) -> answers with real results

you> open youtube.com and search for lo-fi
you> open calculator
you> remember my exam is on Friday
you> remind me what you remember
```

## Features

| Feature | How |
|---|---|
| Chat with an AI | free cloud models: Gemini, Groq, OpenRouter, GitHub Models, Cerebras — **switch anytime with `/provider`**, no reinstall |
| Web search | real live results, no API key needed (DuckDuckGo) |
| Open browser / apps | say *"open youtube.com"*, *"open notepad"*, *"open calculator"* |
| Notes + memory | facts you want kept are saved to disk (`/remember`), survive restarts, and load back into the AI's context next session |
| PC info | `/sys` shows OS, CPU cores, RAM (live free memory too) |
| Voice | optional: speaking replies use Windows built-in TTS (zero install); mic input needs one `pip install` |
| Transcripts | `/save` exports the current conversation to `notes/sessions/` |
| Runs anywhere | offline **mock mode** (`/provider mock`) with no internet and no key |
| Scriptable | `python main.py --once "question"` answers one prompt and exits |

## What it needs to run (specs)

- **OS:** Windows 10/11 (also works on macOS/Linux with tiny tweaks)
- **CPU/RAM:** anything — AI thinking happens in the cloud; ~50 MB RAM idle
- **Internet:** yes, for real answers (voice/search/cloud); optional otherwise
- **Python:** 3.10+ ([python.org](https://www.python.org/downloads/) — tick
  *"Add python.exe to PATH"*)
- **GPU:** none needed. That's the whole point — your Radeon iGPU never matters
- **Mic** (only for voice input): needs `pip install SpeechRecognition pyaudio`
- **Disk:** under 5 MB for the code + your notes

## Quick start

1. Install Python 3.10+ if you don't have it.
2. Double-click **`run.bat`** (or run `python main.py`).
3. Type **`/setup`** and follow the wizard: pick an AI, paste your free key.
4. Say hello. Done.

> First `/setup` only — afterwards it just runs. Keys are stored locally in
> `config/config.json` (never sent anywhere except the provider you chose).

## The free AIs and where to get a key

Every one below has a real free tier — no credit card:

| Provider | Get key at | Notes |
|---|---|---|
| **Gemini** | https://aistudio.google.com/apikey | Big free daily quota, great all-rounder (default) |
| **Groq** | https://console.groq.com/keys | Extremely fast Llama models, generous free tier |
| **OpenRouter** | https://openrouter.ai/keys | Free models like `meta-llama/llama-3.3-70b-instruct:free` |
| **GitHub Models** | https://models.github.ai (sign in → get token) | Free GPT-4o mini and others |
| **Cerebras** | https://cloud.cerebras.ai | Very fast free inference |

Paste the key with `/key` or during `/setup`. **Rate limits are normal** on
free tiers — if you hit "429", wait a minute or `/provider` to another AI.
Ollama (local models) is optional: install it, `ollama pull qwen2.5:1.5b`,
then `/provider ollama`.

### Use your own OpenAI-compatible endpoint
Run any server (LM Studio, vLLM, llama.cpp, another PC on your network, a
paid API like OpenAI/Azure/DeepSeek) and register it once:

```
/add-provider myapi http://192.168.1.5:8000/v1 qwen2.5
/key myapi sk-...
/provider myapi
```

## Command reference

```
/setup                  one-time wizard (pick AI + paste key)
/provider [name]        show / switch AI brain
/model [name]           show / change model on the active provider
/key [provider] [key]   store an API key
/add-provider ...       register any OpenAI-compatible endpoint
/new                    clear conversation memory
/search <query>         web search right now
/notes  /read <name>    list / read saved notes
/note <name> <text>     save a note            /remember <fact>
/forget                 wipe persistent memory
/save                   export this conversation to notes/sessions/
/time  /sys             clock / PC stats
/app <name>             open notepad, calc, browser, taskmgr, ...
/voice                  toggle spoken replies (Windows TTS)
/voice in               mic mode: press Enter on an empty line to talk
/listen                 dictate one message right now
/say <text>             speak text aloud
/quit                   exit
```

> Not a terminal fan? `python main.py --once "open youtube.com and search for lo-fi"`
> answers a single prompt and exits — handy for automation and quick demos.

You usually don't need commands — just talk:

```
"search the web for best budget laptops 2026"
"open github.com"
"open calculator"
"remember my wifi password is on a sticky note"
"what do you remember about me?"
```

## For developers

Everything runs offline in tests — no API keys, no network, real user files
are redirected to a temp directory:

```
python -m unittest discover -s tests -v   # full offline test suite
python main.py --selftest                 # quick end-to-end mock run
python -m py_compile *.py                 # syntax check
```

Colored output is automatic in a real terminal and disables itself when
piped or when `NO_COLOR` is set, so scripts stay clean.

## Project layout

```
config.py      defaults + config/config.json loader/saver
providers.py   one chat() for every AI (free APIs, mock, custom)
tools.py       web search, notes/memory, clock, PC info, apps, browser
mind.py        persistent memory + self-evolving skills (notes/mind.json)
assistant.py   the brain glue: prompts, tools loop, slash commands
voice.py       optional speech out (built-in TTS) + mic in
ui.py          console colors (auto-disable for pipes/NO_COLOR)
main.py        entry point / chat loop / --once / offline self-test
gui.py         double-click launcher for the futuristic GUI
futuristic_gui.py  futuristic CustomTkinter desktop GUI
webgui_server.py   HUD web server (browser frontend, port 8055)
webgui/index.html  the HUD page itself
tests/         offline unit tests (no keys, no network)
run.bat        double-click launcher (Windows)
notes/         your notes + memory.md (auto-created, git-ignored)
```

## Desktop GUI

A separate futuristic-looking desktop app built on CustomTkinter with a dark theme (golden-yellow accent, frosted panels, chat bubbles).

```
python futuristic_gui.py       open the GUI
python gui.py                  same (double-click-friendly)
python futuristic_gui.py --selftest   offline self-check
```

The GUI keeps all the same features as the CLI: chat with the AI, `/commands`, web search, notes, voice, app launching, etc. It adds a sidebar with provider/model/status/voice toggle/new-chat/save, a chat stream with distinct user and AI bubbles + auto-scroll, an input bar with send + mic button, a bottom status bar with live state, and system-tray minimize (Windows, optional).

The GUI works without customtkinter installed too - it falls back to plain tkinter with a dark theme. The `run.bat` launcher installs customtkinter automatically on first run.

> The GUI and CLI share the same `assistant.Phoenix` core, so they behave identically. You can use whichever you prefer.

## HUD (web GUI)

The showpiece frontend: a browser-rendered sci-fi HUD with golden-yellow styling — animated arc-reactor orb, glass panels, grid backdrop — wired straight into the same Phoenix brain.

```
python webgui_server.py        starts the HUD and opens your browser
                               -> http://127.0.0.1:8055
```

Zero extra dependencies (uses only the standard library + `requests` that Phoenix already needs). Everything from the CLI works here too:

- **Arc-reactor core** — pulses while Phoenix thinks, STANDBY when idle
- **AI BRAIN panel** — switch provider/model live from the dropdown
- **QUICK ACTIONS** — one-click /new, /notes, /sys, /time, voice toggle, /save
- **CONVERSATION STREAM** — chat log with YOU / PHOENIX turns, message counter
- **Input bar** — text entry, /sys /time /search quick chips, mic button, send
- **SYSTEM TELEMETRY** — live host, OS, CPU cores, RAM (free/total)
- **EVENT LOG** — timestamped HUD activity feed

The HUD is just a client: the CLI (`python main.py`) and the desktop GUI keep working independently at the same time.

### Mind: persistent memory + self-evolving skills

Phoenix has a lightweight "mind" (`mind.py`, stored in `notes/mind.json`) that
makes it grow with use — Hermes-style memory and skill learning, at a footprint
of a few KB and zero background processes:

- **Persistent memory** — facts, preferences and events you tell it ("my
  sister's name is Marla") are stored, de-duplicated, recalled by relevance in
  later conversations, and forgotten on request. Entries decay when never used
  and the store is hard-capped, so it can never balloon.
- **Self-evolving skills** — when Phoenix solves something non-trivial it can
  save the recipe (name + steps). Next time a similar goal appears it reuses
  the saved skill, records whether it worked, and improves the recipe over
  time (versioned, with a win rate). Skills that keep failing decay away.

Commands: `/memory add|find|drop <text>`, `/skills`, `/skill <goal>`, `/mind`
(footprint + top skills). The AI also calls these tools by itself.

### Self-managed brain + auto-failover

- **"Activate the nemotron ultra model"** — just say it in chat. The AI has
  `switch_model` / `switch_provider` tools; loose names are fuzzy-matched to
  real OpenRouter ids (preferring `:free` ones). `/models` lists what's free
  right now.
- **Auto-failover** — if the active free model is overloaded (429/upstream
  busy), Phoenix silently retries up to 4 sibling free models (same family
  first) and tells you it switched; your chosen model is one `/model <name>`
  away to switch back.

### Start the HUD automatically at login

So that `http://127.0.0.1:8055` always answers, even right after a reboot:

```
double-click install_autostart.bat      one time only
```

That creates a Windows Scheduled Task (`PhoenixHUD`) which quietly starts the HUD server at every login. After that, just open (or bookmark) `http://127.0.0.1:8055` — no double-clicking anything.

```
double-click uninstall_autostart.bat    to remove autostart
python webgui_server.py --no-browser    run the server manually, no browser popup
```

Starting the server twice is safe — it detects an already-running instance and exits.

## Modes

Switch with `/mode <name>`, the HUD top-bar selector, or just say
"enter dark phoenix mode". `mode` is saved, so it survives restarts.

| Mode | What it does |
|---|---|
| `normal` | Standard Phoenix. |
| `darkphoenix` | **Ghost protocol.** The HUD turns dark violet; entering it immediately purges saved transcripts, the persistent mind file, saved identity nicknames and Phoenix's private browser profile. Auto-save (`/save`) and AI memory/skill writes are blocked while active - nothing you do leaves a trace. `/purge` wipes again on demand. |
| `voice` | **Full voice.** The HUD collapses into a single floating arc reactor; the mic listens in a loop and replies are spoken. Type (or press any key) to fall back to text. |
| `god` | **Full tool freedom, zero-cost rule.** Phoenix may chain as many tools as a task needs until it is done - but every single action must cost absolutely nothing (free APIs/tiers/local only). Anything paid is refused. |

Modes can overlap in one conversation ("switch to darkphoenix and open
gemini in chrome as dragon"): theme/behavior changes apply instantly.

## Troubleshooting

- **"No API key set"** — run `/setup`, or `/key <provider> <key>`.
- **HTTP 429** — free-tier rate limit; wait ~30-60 s or `/provider groq|gemini|...`.
- **HTTP 401/403** — key wrong or the model name needs updating: `/model <new>`.
- **Search returns nothing** — DuckDuckGo throttled you; retry in a minute.
- **Voice says nothing** — Windows TTS should always work; check your volume.
- **`/listen` missing packages** — run `python -m pip install SpeechRecognition pyaudio` (already done if you used this repo's installers).
- **HUD mic button fails** — it uses the browser's speech recognition, so:
  - use **Chrome or Edge** (Firefox has no speech API),
  - allow the microphone when the browser asks (check the lock icon in the address bar),
  - it needs internet — recognition runs through the browser's speech service,
  - in the same window, web search/AI replies also need internet, so one problem usually means the other.
- Something else — paste the red `!` line into your message; it's self-explanatory.

## Security notes

- API keys live only in `config/config.json`, which is git-ignored.
- The only outbound traffic is: your chosen AI provider, DuckDuckGo (search),
  Google's speech service (only if you use `/listen`), and Microsoft speech
  synthesis (only if you enable `/voice`).
- Never share `config/config.json`.
