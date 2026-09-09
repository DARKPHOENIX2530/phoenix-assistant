"""Optional voice in/out.

voice_out (text -> speech): uses zero pip dependencies on Windows by driving
the built-in System.Speech engine through PowerShell. If you prefer another
engine it also picks up pyttsx3 when installed.

voice_in  (mic -> text): optional. Needs `pip install SpeechRecognition
pyaudio`. Recognition itself uses Google's free web speech API (no key).

Both degrade gracefully: if a piece is not installed it prints a hint and
returns None/False instead of crashing.
"""
import base64
import os
import subprocess
import sys


def _powershell_encoded(script):
    # UTF-16LE + base64 keeps special characters safe through cmd -> PS.
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _shorten(text, limit=900):
    """Cap spoken text so long answers do not drag on; cut at a word."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(". ,;:") + "..."


def speak(text, voice=None):
    """Speak text aloud. Returns True if something was spoken."""
    if not text or not str(text).strip():
        return False
    text = _shorten(str(text))

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
            engine.say(text)
            engine.runAndWait()
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
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-EncodedCommand", _powershell_encoded(script)],
                capture_output=True, timeout=60)
            return True
        except Exception:
            pass

    # 3) macOS/linux fallback.
    if sys.platform == "darwin":
        try:
            subprocess.run(["say", text], timeout=60)
            return True
        except Exception:
            pass
    return False


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
