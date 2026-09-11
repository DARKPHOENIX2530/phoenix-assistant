"""Phoenix config: defaults + user data (API keys, providers, settings).

Everything is stored in a single JSON file at config/config.json so a
beginner can see and edit it. The file is in .gitignore so keys are not
committed to any repository by accident.
"""
import copy
import json
import os

PHOENIX_VERSION = "1.1.0"

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(ROOT, "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Built-in providers. Free tiers; only an API key is needed (or Ollama).
# Every keyed provider speaks the OpenAI-compatible chat-completions wire
# protocol (Gemini exposes it too), so ONE code path serves them all and
# function/tool calling works everywhere. "mock" runs fully offline.
DEFAULT_PROVIDERS = {
    "gemini": {
        "type": "openai",
        "label": "Google Gemini (free tier)",
        "model": "gemini-2.5-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key": "",
        "requires_key": True,
    },
    "groq": {
        "type": "openai",
        "label": "Groq (free tier, very fast)",
        "model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "",
        "requires_key": True,
    },
    "openrouter": {
        "type": "openai",
        "label": "OpenRouter (free models)",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "requires_key": True,
    },
    "github": {
        "type": "openai",
        "label": "GitHub Models (free, GPT-4o mini etc.)",
        "model": "gpt-4o-mini",
        "base_url": "https://models.github.ai/inference/v1",
        "api_key": "",
        "requires_key": True,
    },
    "cerebras": {
        "type": "openai",
        "label": "Cerebras (free tier, very fast)",
        "model": "llama-3.3-70b",
        "base_url": "https://api.cerebras.ai/v1",
        "api_key": "",
        "requires_key": True,
    },
    "ollama": {
        "type": "openai",
        "label": "Ollama (local, optional - needs separate install)",
        "model": "qwen2.5:1.5b",
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "requires_key": False,
    },
    "freellmapi": {
        "type": "openai",
        "label": "FreeLLMAPI (local router - unlimited free models)",
        "model": "auto",
        "base_url": "http://127.0.0.1:31415/v1",
        "api_key": "",
        "requires_key": True,
    },
    "mock": {
        "type": "mock",
        "label": "Mock (offline demo, no internet needed)",
        "model": "offline-mock",
        "base_url": "",
        "api_key": "",
        "requires_key": False,
    },
}

DEFAULT_SETTINGS = {
    "provider": "freellmapi",   # active provider name (FreeLLMAPI local router)
    "max_history": 16,      # how many past turns are sent to the model
    "voice_in": False,      # listen through the microphone after a blank line
    "voice_out": False,     # speak replies with text-to-speech
}


def defaults():
    return {
        "settings": copy.deepcopy(DEFAULT_SETTINGS),
        "providers": copy.deepcopy(DEFAULT_PROVIDERS),
    }


def load():
    """Load config, merging in any newly added defaults."""
    cfg = defaults()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            saved_settings = saved.get("settings", {})
            cfg["settings"].update(saved_settings)
            for name, spec in saved.get("providers", {}).items():
                if name in DEFAULT_PROVIDERS:
                    merged = dict(DEFAULT_PROVIDERS[name])
                    merged.update(spec)  # keep user values (keys, models...)
                    cfg["providers"][name] = merged
                else:
                    # custom provider saved by the user
                    base = {
                        "type": spec.get("type", "openai"),
                        "label": spec.get("label", name),
                        "model": spec.get("model", ""),
                        "base_url": spec.get("base_url", ""),
                        "api_key": spec.get("api_key", ""),
                        "requires_key": spec.get("requires_key", True),
                    }
                    cfg["providers"][name] = base
        except Exception as exc:  # corrupt file -> start fresh, keep backup
            backup = CONFIG_FILE + ".bak"
            try:
                os.replace(CONFIG_FILE, backup)
            except OSError:
                pass
            print("Config could not be read (%s) - starting fresh. Old file "
                  "kept at %s" % (exc, backup))
    return cfg


def save(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)
