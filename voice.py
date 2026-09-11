"""Optional voice in/out.

voice_out (text -> speech): uses zero pip dependencies on Windows by driving
the built-in System.Speech engine through PowerShell. If you prefer another
engine it also picks up pyttsx3 when installed.

voice_in  (mic -> text): optional. Needs `pip install SpeechRecognition
pyaudio`. Recognition itself uses Google's free web speech API (no key).

Both degrade gracefully: if a piece is not installed it prints a hint and
returns None/False instead of crashing.

voice mode (full voice experience): the AI talks back, listens continuously,
and keeps the conversation flowing naturally - like talking to a person,
not issuing commands to a tool.
"""
import base64
import os
import subprocess
import sys
import threading
import time


# --------------------------------------------------------------------------- #
# Speaking state (shared across threads / GUI)
# --------------------------------------------------------------------------- #
_SPEAKING = {"active": False, "cancel": False}


def is_speaking():
    return _SPEAKING["active"]


def cancel_speech():
    """Signal the current speech to stop early (next call to speak())."""
    _SPEAKING["cancel"] = True


def _find_voice_by_gender(gender):
    """Find a voice id for the given gender via pyttsx3 or Windows voices.

    gender: 'male', 'female', or a specific voice name/id.
    Returns a voice id string or None.
    """
    gender = (gender or "").strip().lower()
    # Try pyttsx3
    try:
        import pyttsx3
        engine = pyttsx3.init()
        voices = engine.getProperty("voices") or []
        if len(voices) == 1:
            return voices[0].id
        # specific name match
        for v in voices:
            vn = (v.name or "").lower()
            if gender in vn or vn in gender:
                return v.id
        # gender keyword match
        if gender in ("female", "woman", "girl"):
            for v in voices:
                vn = (v.name or "").lower()
                if any(kw in vn for kw in ("zira", "samantha", "female", "mary", "harmony", "moira", "tessa", "microsoft")):
                    continue
                if v.gender and "female" in str(v.gender).lower():
                    return v.id
        if gender in ("male", "man", "boy"):
            for v in voices:
                vn = (v.name or "").lower()
                if v.gender and "male" in str(v.gender).lower():
                    return v.id
        # fallback: first voice
        return voices[0].id if voices else None
    except Exception:
        pass

    # Windows System.Speech: pick by name
    if os.name == "nt":
        name_map = {
            "female": "Microsoft Zira Desktop",
            "male": "Microsoft David Desktop",
        }
        if gender in name_map:
            return name_map[gender]
        # try a partial match on installed voices
        try:
            import subprocess
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                 "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"],
                capture_output=True, text=True, timeout=10)
            installed = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            if gender in installed:
                return gender
            for name in installed:
                if gender in name.lower():
                    return name
            if gender in ("female", "woman"):
                for name in installed:
                    if "zira" in name.lower():
                        return name
            if gender in ("male", "man"):
                for name in installed:
                    if "david" in name.lower() or "mark" in name.lower():
                        return name
        except Exception:
            pass
    return None


def _clear_speaking():
    _SPEAKING["active"] = False
    _SPEAKING["cancel"] = False



def _powershell_encoded(script):
    # UTF-16LE + base64 keeps special characters safe through cmd -> PS.
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _shorten(text, limit=None):
    """Cap spoken text so long answers do not drag on; cut at a sentence
    boundary when possible, falling back to a word boundary.

    Voice mode lives or dies on pacing: cut too early and Phoenix sounds
    robotic and clipped, cut too late and the user has waited through 30 s
    of monologue before they can speak again. Prefer a natural pause.
    """
    text = text.strip()
    if not limit:
        # Default: keep it speakable - roughly 15-20 seconds of speech.
        limit = 750
    if len(text) <= limit:
        return text
    # Try a sentence boundary first (., !, ? followed by space or end).
    cut = text[:limit]
    for sep in (". ", "? ", "! ", "\n"):
        pos = cut.rfind(sep)
        if pos > limit * 0.5:
            cut = cut[:pos + 1]
            break
    else:
        # Word boundary.
        space = cut.rfind(" ")
        if space > limit * 0.6:
            cut = cut[:space]
    # Strip trailing punctuation, re-add a single natural ending.
    cut = cut.rstrip(". ,;:\n")
    if not cut.endswith((".", "!", "?")):
        cut += "."
    return cut


def speak(text, voice=None, interrupt=False):
    """Speak text aloud. Returns True if something was spoken.

    If interrupt is True, any in-progress speech is cancelled first so the
    new text starts immediately (used when a new reply arrives while Phoenix
    is still talking - voice mode feels responsive, not queued).
    """
    if not text or not str(text).strip():
        return False
    text = _shorten(str(text))

    if interrupt:
        _SPEAKING["cancel"] = True
        _clear_speaking()

    # 1) pyttsx3, if the user installed it (nicer voices / offline).
    try:
        import pyttsx3  # noqa
    except ImportError:
        pyttsx3 = None
    if pyttsx3 is not None:
        try:
            engine = pyttsx3.init()
            if voice:
                engine.setProperty("voice", voice)
            _SPEAKING["active"] = True
            _SPEAKING["cancel"] = False
            engine.say(text)
            engine.runAndWait()
            _clear_speaking()
            return True
        except Exception:
            pass  # fall through to the Windows engine

    # 2) Windows built-in System.Speech (no install needed).
    if os.name == "nt":
        escaped = text.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$s.Speak('" + escaped + "')"
        )
        _SPEAKING["active"] = True
        _SPEAKING["cancel"] = False
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-EncodedCommand", _powershell_encoded(script)],
                capture_output=True, timeout=60)
            _clear_speaking()
            return True
        except Exception:
            _clear_speaking()
            pass

    # 3) macOS/linux fallback.
    if sys.platform == "darwin":
        try:
            _SPEAKING["active"] = True
            _SPEAKING["cancel"] = False
            subprocess.run(["say", text], timeout=60)
            _clear_speaking()
            return True
        except Exception:
            _clear_speaking()
            pass
    _clear_speaking()
    return False


def _find_voice_by_gender(gender):
    """Find a voice id/name for the given gender.

    gender: 'male' | 'female' | a specific voice name/id.
    Returns a voice id string or None.
    """
    gender = (gender or "").strip().lower()

    # Try pyttsx3 first (gender-aware voices).
    pyttsx3 = None
    try:
        import pyttsx3
        pyttsx3 = pyttsx3
    except ImportError:
        pass
    if pyttsx3 is not None:
        try:
            engine = pyttsx3.init()
            voices = engine.getProperty("voices") or []
            if len(voices) == 1:
                return voices[0].id
            for v in voices:
                vn = (v.name or "").lower()
                if gender in vn or vn in gender:
                    return v.id
            if gender in ("female", "woman", "girl"):
                for v in voices:
                    vn = (v.name or "").lower()
                    if any(kw in vn for kw in ("zira", "samantha", "female", "mary",
                                                  "harmony", "moira", "tessa", "microsoft")):
                        continue
                    try:
                        g = v.gender
                        if g and "female" in str(g).lower():
                            return v.id
                    except Exception:
                        pass
            if gender in ("male", "man", "boy"):
                for v in voices:
                    try:
                        g = v.gender
                        if g and "male" in str(g).lower():
                            return v.id
                    except Exception:
                        pass
            return voices[0].id if voices else None
        except Exception:
            pass

    # Windows System.Speech voice selection.
    if os.name == "nt":
        name_map = {
            "female": "Microsoft Zira Desktop",
            "male": "Microsoft David Desktop",
        }
        if gender in name_map:
            return name_map[gender]
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                 "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"],
                capture_output=True, text=True, timeout=10)
            installed = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            if gender in installed:
                return gender
            for name in installed:
                if gender in name.lower():
                    return name
            if gender in ("female", "woman"):
                for name in installed:
                    if "zira" in name.lower():
                        return name
            if gender in ("male", "man"):
                for name in installed:
                    if "david" in name.lower() or "mark" in name.lower():
                        return name
        except Exception:
            pass
    return None


def speak_with_gender(text, gender="female", interrupt=False):
    """Speak text aloud using a voice of the given gender.

    gender: 'male' | 'female' | a specific voice name/id.
    Returns the voice name actually used, or None if speech failed.
    """
    if not text or not str(text).strip():
        return None
    text = _shorten(str(text))

    if interrupt:
        _SPEAKING["cancel"] = True
        _clear_speaking()

    # 1) pyttsx3 (preferred: nicer voices, gender-aware).
    pyttsx3 = None
    try:
        import pyttsx3
        pyttsx3 = pyttsx3
    except ImportError:
        pass
    if pyttsx3 is not None:
        try:
            engine = pyttsx3.init()
            voice_id = _find_voice_by_gender(gender)
            if voice_id:
                engine.setProperty("voice", voice_id)
            _SPEAKING["active"] = True
            _SPEAKING["cancel"] = False
            engine.say(text)
            engine.runAndWait()
            _clear_speaking()
            return voice_id or "pyttsx3-default"
        except Exception:
            pass

    # 2) Windows System.Speech with gender-specific voice.
    if os.name == "nt":
        voice_name = _find_voice_by_gender(gender)
        escaped = text.replace("'", "''")
        if voice_name:
            script = (
                "Add-Type -AssemblyName System.Speech;"
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                "$s.SelectVoice('" + voice_name.replace("'", "''") + "');"
                "$s.Speak('" + escaped + "')"
            )
        else:
            script = (
                "Add-Type -AssemblyName System.Speech;"
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                "$s.Speak('" + escaped + "')"
            )
        _SPEAKING["active"] = True
        _SPEAKING["cancel"] = False
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-EncodedCommand", _powershell_encoded(script)],
                capture_output=True, timeout=60)
            _clear_speaking()
            return voice_name or "system-default"
        except Exception:
            _clear_speaking()
            pass

    return None


# --------------------------------------------------------------------------- #
# mic device handling
# --------------------------------------------------------------------------- #
_MIC_SILENCE_RMS = 15      # below this a device counts as dead/muted
_LAST_GOOD = {"name": None}


def _rms(data):
    """Root-mean-square loudness of 16-bit mono PCM frames (no audioop)."""
    import struct
    n = len(data) // 2
    if not n:
        return 0
    samples = struct.unpack("<%dh" % n, data[:n * 2])
    total = 0
    for val in samples:
        total += val * val
    return int((total / n) ** 0.5)


def _input_devices():
    """All usable input devices: [{index, name}] (empty if no pyaudio)."""
    try:
        import pyaudio
    except ImportError:
        return []
    out = []
    p = pyaudio.PyAudio()
    try:
        for i in range(p.get_device_count()):
            try:
                d = p.get_device_info_by_index(i)
                if d.get("maxInputChannels", 0) > 0:
                    out.append({"index": i, "name": str(d["name"])})
            except Exception:
                continue
        try:
            default = p.get_default_input_device_info()
            for d in out:
                d["is_default"] = (d["index"] == default["index"])
        except Exception:
            pass
    finally:
        p.terminate()
    return out


def _sample_rms(index, ms=500):
    """Open a device briefly and measure loudness. -1 on failure."""
    try:
        import pyaudio
    except ImportError:
        return -1
    p = pyaudio.PyAudio()
    try:
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                        input=True, input_device_index=index,
                        frames_per_buffer=1024)
        frames = []
        n_chunks = max(1, int(ms / 1000.0 * 16000 / 1024))
        for _ in range(n_chunks):
            frames.append(stream.read(1024, exception_on_overflow=False))
        stream.close()
        return _rms(b"".join(frames))
    except Exception:
        return -1
    finally:
        p.terminate()


def _pick_device(preferred=None):
    """Choose an input device index.

    Order: explicit user choice (index or name fragment) -> last device
    that worked this session -> Windows default IF it carries signal ->
    the loudest other input device (auto-fallback for dead/muted mics).

    Returns (index, name).
    """
    devices = _input_devices()
    if not devices:
        return None, "(no mic devices found)"

    by_name = lambda frag: next(
        (d for d in devices if frag.lower() in d["name"].lower()), None)

    # 1) explicit user preference
    if preferred not in (None, "", "auto", 0):
        if isinstance(preferred, int) or str(preferred).isdigit():
            idx = int(preferred)
            if any(d["index"] == idx for d in devices):
                return idx, next(d["name"] for d in devices
                                 if d["index"] == idx)
        else:
            hit = by_name(str(preferred))
            if hit:
                return hit["index"], hit["name"]

    # 2) remembered device from earlier this session (fast path)
    if _LAST_GOOD["name"]:
        hit = by_name(_LAST_GOOD["name"])
        if hit and _sample_rms(hit["index"], ms=250) >= _MIC_SILENCE_RMS:
            return hit["index"], hit["name"]

    # 3) Windows default - but only if it actually carries signal
    default = next((d for d in devices if d.get("is_default")), devices[0])
    if _sample_rms(default["index"]) >= _MIC_SILENCE_RMS:
        return default["index"], default["name"]

    # 4) auto-fallback: probe every input device, use a live one.
    #    Some drivers expose several endpoints per mic where only one
    #    carries signal (e.g. Realtek default endpoint returns zeros).
    #    Prefer a live endpoint of the SAME hardware as the Windows
    #    default, then the loudest live device.
    default_name = default["name"].lower()
    live = []
    for d in devices:
        r = _sample_rms(d["index"])
        if r >= _MIC_SILENCE_RMS:
            live.append((r, d))
    if live:
        live.sort(key=lambda pair: -pair[0])
        same_hw = next((d for r, d in live
                        if d["name"].lower() == default_name), None)
        pick = same_hw or live[0][1]
        if pick["index"] != default["index"]:
            print("(default mic endpoint is silent - using %s instead)"
                  % pick["name"])
        return pick["index"], pick["name"]

    return default["index"], default["name"]


def list_mics(sample_ms=400):
    """Diagnostics: every input device with a live loudness reading."""
    out = []
    for d in _input_devices():
        rms = _sample_rms(d["index"], ms=sample_ms)
        out.append({"index": d["index"], "name": d["name"],
                    "rms": rms,
                    "alive": rms >= _MIC_SILENCE_RMS,
                    "is_default": bool(d.get("is_default"))})
    return out


def has_mic():
    """Can Phoenix do voice input at all? (pyaudio + SpeechRecognition)."""
    try:
        import pyaudio  # noqa
        import speech_recognition as sr  # noqa
        return True
    except ImportError:
        return False


def can_speak():
    """Can Phoenix do voice output? (pyttsx3 or Windows System.Speech)."""
    try:
        import pyttsx3  # noqa
        return True
    except ImportError:
        pass
    if os.name == "nt":
        return True
    if sys.platform == "darwin":
        return True
    return False


def listen(timeout=8, device=None):
    """Listen on the mic for one phrase and return its text.

    device: None (auto: default mic, but fall back to a live one if it is
    silent), a device index, or a case-insensitive name fragment such as
    "airpods". Returns the transcript, or None on silence/error.
    """
    try:
        import speech_recognition as sr
    except ImportError:
        print("Mic input needs one-time install: "
              "python -m pip install SpeechRecognition pyaudio")
        return None
    try:
        idx, name = _pick_device(device)
        if idx is None:
            print("Mic problem: %s" % name)
            return None
        _LAST_GOOD["name"] = name
        recognizer = sr.Recognizer()
        with sr.Microphone(device_index=idx) as source:
            print("(listening on %s - speak now...)" % name)
            recognizer.adjust_for_ambient_noise(source, duration=0.4)
            audio = recognizer.listen(source, timeout=timeout,
                                      phrase_time_limit=15)
        print("(heard you - recognizing...)")
        return recognizer.recognize_google(audio)
    except ImportError:
        print("Mic input needs the 'pyaudio' package too: "
              "python -m pip install pyaudio")
        return None
    except sr.WaitTimeoutError:
        print("(nothing heard in time)" % ())
        return None
    except sr.UnknownValueError:
        print("(heard audio but could not understand it)")
        return None
    except sr.RequestError as exc:
        print("Speech recognition service unreachable: %s" % exc)
        return None
    except Exception as exc:
        print("Mic problem: %s" % exc)
        return None


# --------------------------------------------------------------------------- #
# Always-listening / wake-word mode
# --------------------------------------------------------------------------- #

# Shared state for the always-listening thread.
_WHISPER = {
    "running": False,
    "thread": None,
    "device": None,
    "on_heard": None,        # callback(text) when something is recognized
    "on_listening": None,    # callback() when mic is actively capturing
    "on_idle": None,         # callback() when we are waiting for wake word
    "wake_word": "phoenix",  # case-insensitive substring to trigger capture
    "_last_text": None,
}


def whisper_state():
    """Current always-listening configuration + running state."""
    return {
        "running": _WHISPER["running"],
        "wake_word": _WHISPER["wake_word"],
        "device": _WHISPER["device"],
    }


def whisper_start(device=None, wake_word="phoenix", on_heard=None,
                  on_listening=None, on_idle=None):
    """Start an always-listening background thread.

    While running the mic is LIVE at all times. The thread listens for a
    wake word (default 'phoenix') and, once heard, hands the full phrase
    to on_heard(text). Use on_listening/on_idle to drive a GUI indicator
    (e.g. pulsing mic icon while capturing, dimmed while waiting).

    Returns True if the thread started, False if the install is missing or
    a thread is already running.
    """
    if _WHISPER["running"]:
        return False
    try:
        import speech_recognition as sr  # noqa
    except ImportError:
        print("Always-listening needs: python -m pip install "
              "SpeechRecognition pyaudio")
        return False
    _WHISPER["device"] = device
    _WHISPER["wake_word"] = wake_word.lower()
    _WHISPER["on_heard"] = on_heard
    _WHISPER["on_listening"] = on_listening
    _WHISPER["on_idle"] = on_idle
    _WHISPER["running"] = True
    t = threading.Thread(target=_whisper_loop, daemon=True)
    t.start()
    _WHISPER["thread"] = t
    return True


def whisper_stop():
    """Stop the always-listening thread."""
    _WHISPER["running"] = False
    _WHISPER["on_heard"] = None
    _WHISPER["on_listening"] = None
    _WHISPER["on_idle"] = None
    _WHISPER["_last_text"] = None


def _whisper_loop():
    """Background thread: listen for wake word, then capture the phrase."""
    try:
        import speech_recognition as sr
    except ImportError:
        _WHISPER["running"] = False
        return
    try:
        idx, name = _pick_device(_WHISPER["device"])
        if idx is None:
            _WHISPER["running"] = False
            return
        _LAST_GOOD["name"] = name
        recognizer = sr.Recognizer()
        # Slightly more aggressive ambient adjustment for always-on.
        with sr.Microphone(device_index=idx) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.6)
            while _WHISPER["running"]:
                # Idle: listening for wake word.
                if _WHISPER["on_idle"]:
                    _WHISPER["on_idle"]()
                try:
                    audio = recognizer.listen(
                        source, timeout=1.5, phrase_time_limit=3)
                except sr.WaitTimeoutError:
                    continue
                except Exception:
                    time.sleep(0.3)
                    continue
                if not _WHISPER["running"]:
                    break
                # We heard something - capture a full phrase.
                if _WHISPER["on_listening"]:
                    _WHISPER["on_listening"]()
                try:
                    text = recognizer.recognize_google(audio)
                except sr.UnknownValueError:
                    # Unintelligible - back to idle.
                    continue
                except sr.RequestError:
                    time.sleep(1)
                    continue
                except Exception:
                    time.sleep(0.3)
                    continue
                if not _WHISPER["running"]:
                    break
                if text and _WHISPER["wake_word"] in text.lower():
                    # Wake word heard - listen for the full command.
                    _WHISPER["_last_text"] = text
                    try:
                        phrase = recognizer.listen(
                            source, timeout=8, phrase_time_limit=15)
                    except sr.WaitTimeoutError:
                        # Wake word with no follow-up - treat the wake word
                        # utterance itself as the message.
                        if _WHISPER["on_heard"]:
                            _WHISPER["on_heard"](text)
                        continue
                    except Exception:
                        if _WHISPER["on_heard"]:
                            _WHISPER["on_heard"](text)
                        continue
                    if not _WHISPER["running"]:
                        break
                    try:
                        phrase_text = recognizer.recognize_google(phrase)
                    except sr.UnknownValueError:
                        phrase_text = None
                    except sr.RequestError:
                        phrase_text = None
                    except Exception:
                        phrase_text = None
                    # Combine: wake word utterance + follow-up if any.
                    combined = text
                    if phrase_text:
                        combined = text + " " + phrase_text
                    if _WHISPER["on_heard"]:
                        _WHISPER["on_heard"](combined)
                else:
                    # Something was said but no wake word - ignore in
                    # wake-word mode; but forward it anyway if the user
                    # wants a fully-open mic (no wake word = "phoenix").
                    if _WHISPER["wake_word"] == "":
                        if _WHISPER["on_heard"]:
                            _WHISPER["on_heard"](text)
    except Exception:
        _WHISPER["running"] = False
