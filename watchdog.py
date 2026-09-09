"""Phoenix Watchdog: background guardian that checks on a schedule and
alerts ONLY when something new is wrong.

Checks (all defensive, all local):
- shield sweep diffs: new suspicious listeners, new hosts tampering,
  new startup entries since the last check
- disk space (C: below 10% free)
- battery (below 20% and discharging)
- HUD-alive self check is implicit (the watchdog runs inside the server)

Alerts: Windows toast notification (PowerShell BurntToast-free native
via Windows.UI.Notifications) + optional TTS through Phoenix's voice
module. Rate-limited: each unique problem alerts once, then goes quiet
until it changes. State persists in notes/watchdog_state.json so
restarts do not re-alarm on known issues.

Everything is configured/damped via /watchdog commands:
  /watchdog            status
  /watchdog on|off     arm or disarm
  /watchdog now        run a check immediately
  /watchdog interval 30   set minutes between checks
"""
import json
import os
import subprocess
import threading
import time

import tools

PROJECT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(PROJECT, "notes", "watchdog_state.json")

DEFAULT_INTERVAL = 60          # minutes
INTERVAL_MINUTES = DEFAULT_INTERVAL

_thread = None
_stop = threading.Event()


def _load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)


def _toast(title, message):
    """Native Windows toast (no dependencies). Silent-fails elsewhere."""
    if os.name != "nt":
        return False
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI."
        "Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::"
        "GetTemplateContent([Windows.UI.Notifications."
        "ToastTemplateType]::ToastText02);"
        "$t.GetElementsByTagName('text').Item(0).InnerText='%s';"
        "$t.GetElementsByTagName('text').Item(1).InnerText='%s';"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        "CreateToastNotifier('Phoenix').Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))"
        % (title.replace("'", ""), message.replace("'", "")[:180]))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", ps],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _speak(message):
    try:
        import voice
        voice.speak(message)
        return True
    except Exception:
        return False


def alert(title, message, speak=True):
    """User-visible alert: toast + voice. Used by checks and by Phoenix."""
    ok = _toast("PHOENIX - " + title, message)
    if speak:
        _speak("%s. %s" % (title, message))
    return {"toast": ok, "spoken": bool(speak)}


# --------------------------------------------------------------------------- #
# individual checks -> list of problem strings ("" = all fine)
# --------------------------------------------------------------------------- #

def _check_disk():
    probs = []
    if os.name != "nt":
        return probs
    try:
        import shutil
        total, used, free = shutil.disk_usage("C:\\")
        pct = 100.0 * free / total if total else 0
        if pct < 10:
            probs.append("C: drive %.1f%% free (%.1f GB left) - clean up"
                         % (pct, free / 1e9))
    except Exception:
        pass
    return probs


def _check_battery():
    probs = []
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-WmiObject Win32_Battery).EstimatedChargeRemaining; "
             "(Get-WmiObject Win32_Battery).BatteryStatus"],
            capture_output=True, text=True, timeout=15)
        vals = [v.strip() for v in (r.stdout or "").splitlines()
                if v.strip()]
        if len(vals) >= 2:
            pct = int(float(vals[0]))
            status = int(float(vals[1]))   # 1=discharging, 2=on AC
            if pct <= 20 and status == 1:
                probs.append("Battery at %d%% and discharging - plug in"
                             % pct)
    except Exception:
        pass
    return probs


def _shield_signature():
    """Compact signature of the current security posture for diffing."""
    import security_shield
    sig = {}
    try:
        net = security_shield.audit_network()
        sig["net"] = net if net.startswith("!") else \
            ("suspicious:" in net.lower() and net or "clean")
    except Exception as exc:
        sig["net"] = "error: %s" % exc
    try:
        hosts = security_shield.hosts_check()
        sig["hosts"] = hosts if hosts.startswith("!") else \
            ("problem" if "problem" in hosts.lower() else "clean")
    except Exception as exc:
        sig["hosts"] = "error: %s" % exc
    try:
        startup = security_shield.startup_audit()
        sig["startup"] = str(len(startup.splitlines()))
    except Exception as exc:
        sig["startup"] = "error: %s" % exc
    return sig


def _check_shield(state):
    probs = []
    sig = _shield_signature()
    prev = state.get("shield_sig") or {}
    if prev:
        if sig.get("net") != prev.get("net") and sig.get("net") not in (
                "clean",):
            probs.append("Network audit changed: %s"
                         % str(sig.get("net"))[:160])
        if sig.get("hosts") == "problem" and prev.get("hosts") != "problem":
            probs.append("Hosts file tampering detected - run /shield hosts")
        if sig.get("startup") != prev.get("startup"):
            probs.append("Startup entries changed (%s -> %s) - run "
                         "/shield startup to review"
                         % (prev.get("startup"), sig.get("startup")))
    state["shield_sig"] = sig
    return probs


def run_checks(speak=True):
    """Run every check; alert on NEW problems; return the report."""
    state = _load_state()
    problems = []
    problems += _check_disk()
    problems += _check_battery()
    problems += _check_shield(state)
    seen = set(state.get("alerted") or [])
    fresh = [p for p in problems if p not in seen]
    # expire alerts for problems that disappeared
    state["alerted"] = [p for p in seen if p in problems]
    for p in fresh:
        alert("Watchdog", p, speak=speak)
    if fresh:
        state["alerted"] = (state.get("alerted") or []) + fresh
    state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["last_problems"] = problems
    _save_state(state)
    return fresh   # only what was NEWLY alerted this round


# --------------------------------------------------------------------------- #
# background loop
# --------------------------------------------------------------------------- #

def _loop():
    global INTERVAL_MINUTES
    # first sweep soon after boot; later sweeps on the full interval
    if not _stop.wait(120):
        try:
            run_checks(speak=False)
        except Exception:
            pass
    while not _stop.wait(INTERVAL_MINUTES * 60):
        try:
            # never speak over an active chat; toasts are enough at night
            run_checks(speak=False)
        except Exception:
            pass


def start(interval_minutes=None):
    global _thread, INTERVAL_MINUTES
    if interval_minutes:
        INTERVAL_MINUTES = max(5, min(720, int(interval_minutes)))
    if _thread and _thread.is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True,
                               name="phoenix-watchdog")
    _thread.start()
    return True


def stop():
    _stop.set()
    return True


def status(_args=None):
    state = _load_state()
    armed = bool(_thread and _thread.is_alive())
    lines = ["WATCHDOG - %s" % ("ARMED (every %d min)" % INTERVAL_MINUTES
                                if armed else "disarmed"),
             "  last run: %s" % state.get("last_run", "never")]
    probs = state.get("last_problems") or []
    if probs:
        lines.append("  open problems (%d):" % len(probs))
        lines += ["    - " + p[:110] for p in probs[:8]]
    else:
        lines.append("  no open problems.")
    return "\n".join(lines)


def watchdog_tool(args):
    """Slash/AI entry: /watchdog [on|off|now|interval <min>]."""
    arg = str(args.get("arg") or "").strip().lower()
    if arg in ("on", "arm", "start"):
        started = start()
        return "Watchdog armed." if started else "Watchdog already armed."
    if arg in ("off", "disarm", "stop"):
        stop()
        return "Watchdog disarmed."
    if arg == "now":
        probs = run_checks(speak=True)
        return ("Watchdog check done. %s"
                % ("NEW problems: " + "; ".join(probs[:3])
                   if probs else "All clear."))
    if arg.startswith("interval"):
        parts = arg.split()
        if len(parts) == 2 and parts[1].isdigit():
            global INTERVAL_MINUTES
            INTERVAL_MINUTES = max(5, min(720, int(parts[1])))
            if _thread and _thread.is_alive():
                start(0)   # no-op restart with new interval
            return "Watchdog interval: %d minutes." % INTERVAL_MINUTES
        return "! interval needs minutes, e.g. /watchdog interval 30"
    return status()
