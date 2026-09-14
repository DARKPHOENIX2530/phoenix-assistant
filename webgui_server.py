"""Phoenix HUD server - zero dependencies beyond `requests`.

Serves webgui/index.html and bridges the browser HUD to your existing
assistant.Phoenix core (same brain as the CLI). Run:

    python webgui_server.py          ->  http://127.0.0.1:8055

Endpoints:
    GET  /                -> the HUD page
    GET  /api/status      -> provider/model/system telemetry (JSON)
    POST /api/chat        -> {text} => {reply, error}  (slash commands work)
    POST /api/provider    -> {name} => switch AI brain
"""

import json
import os
import platform
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import tools

PROJECT = os.path.dirname(os.path.abspath(__file__))
WEBGUI = os.path.join(PROJECT, "webgui")
HOST, PORT = "127.0.0.1", 8055

_lock = threading.Lock()          # serialize access to the Phoenix instance
_listen_lock = threading.Lock()   # the mic can only listen to one thing
_bot = None                       # lazy-created Phoenix instance

# HUD conversation transcript (user + phoenix + system lines) for replay
# after a page reload. Mirrors what the chat panel shows. Plain list of
# {"kind": "user"|"ai"|"sys"|"err", "text": str}.
_history = []
_history_lock = threading.Lock()
_HISTORY_MAX = 50


def _hist_add(kind, text):
    with _history_lock:
        _history.append({"kind": kind, "text": text})
        del _history[:-_HISTORY_MAX]      # keep only the newest N


def _hist_snapshot():
    with _history_lock:
        return list(_history)


def get_bot():
    global _bot
    with _lock:
        if _bot is None:
            import assistant
            cfg = config.load()
            _bot = assistant.Phoenix(cfg)
        return _bot


def _on_heard_from_mic(text):
    """Wake-word callback (HUD whisper mode): serialize the heard phrase
    through the chat lock and mirror it into the HUD transcript."""
    text = (text or "").strip()
    if not text:
        return
    # strip a leading wake word so the HUD shows the actual command
    low = text.lower()
    for w in ("phoenix,", "phoenix"):
        if low.startswith(w):
            text = text[len(w):].strip(" ,.!") or text
            break
    _hist_add("user", text)
    bot = get_bot()
    try:
        with _lock:
            reply = bot.handle(text)
    except Exception as exc:
        reply = "! %s" % exc
    if reply is not None:
        _hist_add("err" if str(reply).startswith("!") else "ai", reply)


def _system_payload():
    uname = platform.uname()
    info = {
        "host": uname.node,
        "os": "%s %s" % (uname.system,
                         (tools.windows_release()
                          if hasattr(tools, "windows_release")
                          else uname.release)),
        "cpu_cores": os.cpu_count(),
        "ram": "",
        "ram_free_gb": None,
        "ram_total_gb": None,
    }
    try:
        text = tools.system_info()
        for line in text.splitlines():
            if line.startswith("RAM:"):
                info["ram"] = line[4:].strip()
                # "12.3 GB total, 4.5 GB free"
                try:
                    parts = line.replace("GB", "").split(",")
                    info["ram_total_gb"] = float(parts[0].split()[1])
                    if len(parts) > 1:
                        info["ram_free_gb"] = float(parts[1].split()[0])
                except (ValueError, IndexError):
                    pass
                break
    except Exception:
        pass
    return info


class Handler(BaseHTTPRequestHandler):
    # ---- helpers ------------------------------------------------------- #
    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt, *args):
        pass  # silence default request logging

    # ---- routes -------------------------------------------------------- #
    def do_GET(self):
        if self.path == "/api/history":
            self._send_json({"messages": _hist_snapshot()})
        elif self.path in ("/", "/index.html"):
            self._send_file(os.path.join(WEBGUI, "index.html"),
                            "text/html; charset=utf-8")
        elif self.path == "/api/status":
            bot = get_bot()
            name, spec = bot.active()
            has_key = bool(spec and spec.get("api_key"))
            providers = sorted(bot.cfg["providers"].keys())
            payload = {
                "provider": name,
                "model": (spec or {}).get("model", "?"),
                "has_key": has_key,
                "providers": providers,
                "modes": bot.modes(),
                "voice_gender": bot.cfg["settings"].get("voice_gender",
                                                          "female"),
                "whisper": bot.voice_status().get("whisper", {}),
                **_system_payload(),
            }
            self._send_json(payload)
        else:
            self._send_json({"error": "unknown endpoint"}, 404)

    def do_POST(self):
        if self.path == "/api/chat/stream":
            """Server-Sent-Events chat: streams tokens + tool activity
            live to the HUD, then a final {type:'done'} frame."""
            data = self._read_body()
            text = str(data.get("text") or "").strip()
            if not text:
                self._send_json({"error": "empty text"}, 400)
                return
            _hist_add("user", text)
            bot = get_bot()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def emit(frame):
                try:
                    self.wfile.write(("data: %s\n\n"
                                      % json.dumps(frame)).encode("utf-8"))
                    self.wfile.flush()
                except (OSError, ValueError):
                    pass  # client gone; keep draining silently

            try:
                with _lock:
                    reply = bot.handle(text, on_event=emit)
            except Exception as exc:
                emit({"type": "error", "text": "! Internal error: %s" % exc})
                reply = "! Internal error: %s" % exc
            if reply is None:               # /quit typed in the HUD
                reply = "(Phoenix core says goodbye - close the HUD to exit.)"
            _hist_add("err" if str(reply).startswith("!") else "ai", reply)
            emit({"type": "done", "reply": reply})
        elif self.path == "/api/chat":
            data = self._read_body()
            text = str(data.get("text") or "").strip()
            if not text:
                self._send_json({"error": "empty text"}, 400)
                return
            _hist_add("user", text)
            bot = get_bot()
            try:
                with _lock:
                    reply = bot.handle(text)
            except Exception as exc:
                self._send_json({"reply": "! Internal error: %s" % exc,
                                 "error": True})
                _hist_add("err", "! Internal error: %s" % exc)
                return
            if reply is None:               # /quit typed in the HUD
                reply = "(Phoenix core says goodbye - close the HUD to exit.)"
            _hist_add("err" if str(reply).startswith("!") else "ai", reply)
            self._send_json({"reply": reply, "error": reply.startswith("!")})
        elif self.path == "/api/mode":
            """Toggle a mode on/off: {"mode": "darkphoenix"}"""
            data = self._read_body()
            mode = str(data.get("mode") or "").strip().lower()
            if not mode:
                self._send_json({"error": "missing mode"}, 400)
                return
            bot = get_bot()
            try:
                with _lock:
                    reply = bot.switch_mode(mode)
                _hist_add("sys", reply)
                self._send_json({"ok": True, "reply": reply,
                                 "modes": bot.modes()})
            except Exception as exc:
                self._send_json({"ok": False, "reply": str(exc)})
        elif self.path == "/api/voice-gender":
            """{"gender": "male"|"female"} -> switch spoken voice."""
            data = self._read_body()
            gender = str(data.get("gender") or "").strip().lower()
            if gender not in ("male", "female"):
                self._send_json({"ok": False,
                                 "error": "gender must be male/female"})
                return
            bot = get_bot()
            try:
                with _lock:
                    reply = bot.handle("/voice " + gender)
                _hist_add("sys", reply)
                self._send_json({"ok": True, "reply": reply,
                                 "gender": gender})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)})
        elif self.path == "/api/whisper":
            """{"on": true|false} -> wake-word always-listening toggle."""
            data = self._read_body()
            want = bool(data.get("on"))
            bot = get_bot()
            try:
                with _lock:
                    if want:
                        reply = bot._cmd_whisper("on",
                                                 on_heard=_on_heard_from_mic)
                    else:
                        reply = bot._cmd_whisper("off")
                running = bot.voice_status().get("whisper", {}).get("running")
                self._send_json({"ok": True, "reply": reply,
                                 "running": bool(running)})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)})
        elif self.path == "/api/provider":
            data = self._read_body()
            name = str(data.get("name") or "").strip()
            bot = get_bot()
            try:
                with _lock:
                    reply = bot.handle("/provider " + name)
                ok = not reply.startswith("!")
                self._send_json({"ok": ok, "reply": reply})
            except Exception as exc:
                self._send_json({"ok": False, "reply": str(exc)})
        elif self.path == "/api/listen":
            """One listen cycle on Phoenix's own mic stack.

            Runs OUTSIDE the chat lock so the HUD stays responsive; the
            mic is exclusive, so a separate listen lock serializes us.
            """
            import voice
            try:
                with _listen_lock:
                    bot = get_bot()
                    device = bot.cfg["settings"].get("mic_device") or None
                    text = voice.listen(timeout=7,
                                        device=None if device in ("auto",)
                                        else device)
            except Exception as exc:
                self._send_json({"ok": False, "text": "",
                                 "error": "Mic error: %s" % exc})
                return
            if text:
                self._send_json({"ok": True, "text": text})
            else:
                self._send_json({"ok": False, "text": "",
                                 "error": "Didn't catch anything - try "
                                          "again, a bit closer to the mic."})
        elif self.path == "/api/speak":
            """Speak text aloud: {"text": "...", "gender": "male"|"female"}

            Speech runs in a BACKGROUND thread - the endpoint returns
            immediately instead of blocking for the whole utterance."""
            data = self._read_body()
            text = str(data.get("text") or "").strip()
            if not text:
                self._send_json({"error": "empty text"}, 400)
                return
            import voice as _voice
            gender = str(data.get("gender") or "").strip().lower()
            if not gender:
                bot = get_bot()
                gender = bot.cfg["settings"].get("voice_gender", "female")
            try:
                threading.Thread(
                    target=_voice.speak_with_gender,
                    args=(text,), kwargs={"gender": gender},
                    daemon=True).start()
                self._send_json({"ok": True, "spoke": True, "voice": gender,
                                 "async": True})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)})
        else:
            self._send_json({"error": "unknown endpoint"}, 404)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _hud_already_running(port):
    """True if something already serves the HUD on this port."""
    try:
        import urllib.request
        urllib.request.urlopen("http://%s:%s/" % (HOST, port), timeout=1.5)
        return True
    except Exception:
        return False


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Phoenix HUD web server")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not open the browser automatically "
                         "(useful for autostart)")
    ap.add_argument("--port", type=int, default=PORT,
                    help="port to listen on (default %d)" % PORT)
    args = ap.parse_args()

    port = args.port
    if _hud_already_running(port):
        print("Phoenix HUD is already running at http://%s:%s" % (HOST, port))
        return

    srv = Server((HOST, port), Handler)
    url = "http://%s:%s" % (HOST, port)
    print("=" * 60)
    print("  PHOENIX HUD")
    print("  " + url + "   <- open this in your browser")
    print("  Ctrl+C to stop.  The CLI (python main.py) still works.")
    print("=" * 60)
    if not args.no_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nHUD server stopped.")


if __name__ == "__main__":
    main()
