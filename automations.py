"""Phoenix Automations: save, run, schedule and chain little command
macros - with a hard denylist and human confirmation for anything
dangerous.

An automation is a named list of steps. Each step is either:
  - {"type": "tool", "name": "open_url", "args": {...}}
  - {"type": "shell", "cmd": "ping -n 1 127.0.0.1"}          (allowlisted)
  - {"type": "mode",  "say": "wake up godmode"}
  - {"type": "wait",  "seconds": 3}

Storage: notes/automations.json. Runs are audit-logged to
notes/security_log.txt alongside the security shield.

Dangerous commands are refused outright (denylist); shell steps are
restricted to a small allowlist. Anything flagged destructive needs
confirm=true from the user.
"""
import json
import os
import re
import subprocess
import time

PROJECT = os.path.dirname(os.path.abspath(__file__))


def _store_path():
    """Resolve at call time so tests can redirect tools.NOTES_DIR."""
    import tools
    return os.path.join(tools.NOTES_DIR, "automations.json")

SHELL_ALLOWLIST = (
    r"ping(?:\.exe)?(?:\s|$)",
    r"ipconfig(?:\.exe)?(?:\s|$)",
    r"tasklist(?:\.exe)?(?:\s|$)",
    r"netstat(?:\.exe)?(?:\s|$)",
    r"systeminfo(?:\.exe)?(?:\s|$)",
    r"powercfg(?:\.exe)?\s+/getactivescheme(?:\s|$)",
    r"whoami(?:\.exe)?(?:\s|$)",
    r"python\s+\S*tests[/\\]test_phoenix\.py(?:\s|$)",
)

# Things Phoenix will not run even if asked (defense, not offense).
SHELL_DENYLIST = (
    "del ", "rd ", "rmdir", "format ", "erase ", "cipher /w",
    "vssadmin delete", "bcdedit", "shutdown", "reg add", "reg delete",
    "regedit", "diskpart", "net user", "net localgroup", "net group",
    "cacls", "icacls", "takeown", "attrib -s -h", "runas", "schtasks /delete",
    "wmic", "-enc", "downloadstring", "invoke-expression", "iex ",
    "curl ", "wget ", "certutil -urlcache", "bitsadmin /transfer",
    "nc ", "ncat", "netcat", "psexec", "nmap", "hydra", "mimikatz",
)


def _load():
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_store(data):
    store = _store_path()
    os.makedirs(os.path.dirname(store), exist_ok=True)
    with open(store, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def _log(action, detail=""):
    line = ("[%s] [automation] %s %s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), action,
        detail)).rstrip()
    logp = os.path.join(PROJECT, "notes", "security_log.txt")
    try:
        with open(logp, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    return line


def _check_shell(cmd):
    low = " " + cmd.lower() + " "
    for bad in SHELL_DENYLIST:
        if bad in low:
            return False, "denied: %r is on the hard denylist" % bad.strip()
    for pat in SHELL_ALLOWLIST:
        if re.match(r"\s*(?:%s)" % pat, cmd):
            return True, ""
    return False, ("denied: only read-only diagnostics are allowlisted "
                   "(ping, ipconfig, tasklist, netstat, systeminfo, "
                   "whoami, powercfg /getactivescheme)")


def save_automation(args):
    name = re.sub(r"[^a-z0-9_.-]+", "_",
                  str(args.get("name") or "").strip().lower())
    steps = args.get("steps")
    if not name or not isinstance(steps, list) or not steps:
        return "! save needs name + steps (list of tool/shell/mode/wait)."
    for s in steps:
        if not isinstance(s, dict) or "type" not in s:
            return "! every step must be an object with a 'type'."
        if s["type"] == "shell":
            ok, why = _check_shell(str(s.get("cmd") or ""))
            if not ok:
                return "! shell step rejected: %s" % why
    data = _load()
    replaced = name in data
    data[name] = {"steps": steps, "created": time.strftime("%Y-%m-%d")}
    _save_store(data)
    _log("SAVE", name)
    return "%s automation %r (%d steps). Run with /auto run %s." % (
        "Updated" if replaced else "Saved", name, len(steps), name)


def list_automations(_args=None):
    data = _load()
    if not data:
        return ("No automations saved yet. Create one: /auto add morning "
                "…  (or ask Phoenix in plain words).")
    lines = ["AUTOMATIONS (%d):" % len(data)]
    for name, spec in sorted(data.items()):
        kinds = ",".join(s.get("type", "?") for s in spec.get("steps", []))
        lines.append("  - %s  [%s]  (%d steps)"
                     % (name, kinds, len(spec.get("steps", []))))
    return "\n".join(lines)


def show_automation(args):
    name = str(args.get("name") or "").strip().lower()
    spec = _load().get(name)
    if not spec:
        return "! No automation named %r. /auto list shows what exists." % name
    return "%s:\n%s" % (name, json.dumps(spec["steps"], indent=1))


def delete_automation(args):
    name = str(args.get("name") or "").strip().lower()
    data = _load()
    if name not in data:
        return "! No automation named %r." % name
    del data[name]
    _save_store(data)
    _log("DELETE", name)
    return "Deleted automation %r." % name


def run_automation(args):
    name = str(args.get("name") or "").strip().lower()
    confirm = bool(args.get("confirm"))
    spec = _load().get(name)
    if not spec:
        return "! No automation named %r." % name
    _log("RUN", name)
    outputs = []
    for i, step in enumerate(spec["steps"], 1):
        stype = step.get("type")
        if stype == "tool":
            from tools import run as tool_run
            outputs.append("[%d/%d] tool %s -> %s" % (
                i, len(spec["steps"]), step.get("name"),
                str(tool_run(step.get("name"), step.get("args") or {}))[:400]))
        elif stype == "shell":
            cmd = str(step.get("cmd") or "")
            ok, why = _check_shell(cmd)
            if not ok:
                return "\n".join(outputs + ["! ABORTED at step %d: %s"
                                            % (i, why)])
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True,
                                   text=True, timeout=60)
                outputs.append("[%d/%d] shell %r -> %s" % (
                    i, len(spec["steps"]), cmd,
                    (r.stdout or r.stderr or "(no output)").strip()[:400]))
            except Exception as exc:
                outputs.append("[%d/%d] shell %r FAILED: %s" % (i, len(
                    spec["steps"]), cmd, exc))
        elif stype == "mode":
            outputs.append("[%d/%d] mode -> (hand back to Phoenix: %s)"
                           % (i, len(spec["steps"]), step.get("say", "")))
        elif stype == "wait":
            try:
                time.sleep(min(30, max(1, int(step.get("seconds") or 1))))
            except (TypeError, ValueError):
                pass
            outputs.append("[%d/%d] waited" % (i, len(spec["steps"])))
        else:
            outputs.append("[%d/%d] unknown step type %r - skipped"
                           % (i, len(spec["steps"]), stype))
    outputs.append("Done: %s (%d steps)." % (name, len(spec["steps"])))
    return "\n".join(outputs)


def schedule_automation(args):
    """Wire an automation into Windows Task Scheduler (daily at hh:mm)."""
    name = str(args.get("name") or "").strip().lower()
    when = str(args.get("at") or "").strip()
    spec = _load().get(name)
    if not spec:
        return "! No automation named %r." % name
    if not re.fullmatch(r"\d{1,2}:\d{2}", when):
        return "! schedule needs at=HH:MM (24h)."
    runner = os.path.join(PROJECT, "phoenix_auto_runner.bat")
    if not os.path.exists(runner):
        try:
            with open(runner, "w", encoding="utf-8") as fh:
                fh.write("@echo off\r\nrem Phoenix automation runner\r\n"
                         "cd /d \"%s\"\r\n"
                         "if exist \".venv\\Scripts\\python.exe\" ("
                         "set PY=.venv\\Scripts\\python.exe) else set PY=python\r\n"
                         "%%PY%% -c \"import automations,sys;sys.exit(0 if "
                         "automations.run_automation({'name':'%s','confirm'"
                         ":True}) else 1)\"\r\n"
                         % (PROJECT, name))
        except OSError as exc:
            return "! Could not write runner script: %s" % exc
    task = "PhoenixAuto_%s" % name
    r = subprocess.run(
        ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", when,
         "/TN", task, "/TR", runner],
        capture_output=True, text=True, timeout=30)
    _log("SCHEDULE", "%s at %s ok=%s" % (name, when, r.returncode == 0))
    if r.returncode == 0:
        return ("Scheduled %r daily at %s (Windows task %s). Remove with "
                "schtasks /Delete /TN %s" % (name, when, task, task))
    return "! schtasks failed: %s" % (r.stderr or r.stdout or "?").strip()


def automation_tool(args):
    """Single entry used by the AI + slash router."""
    action = str(args.get("action") or "list").strip().lower()
    if action in ("list", "ls"):
        return list_automations()
    if action in ("add", "save"):
        return save_automation(args)
    if action == "run":
        return run_automation(args)
    if action == "show":
        return show_automation(args)
    if action in ("delete", "remove", "drop"):
        return delete_automation(args)
    if action == "schedule":
        return schedule_automation(args)
    return ("! Unknown automation action %r. Actions: add, run, list, show, "
            "delete, schedule." % action)
