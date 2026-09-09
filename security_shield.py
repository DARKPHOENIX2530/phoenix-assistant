"""Phoenix OS Shield: DEFENSIVE security tools + admin utilities.

Philosophy: Phoenix helps you PROTECT this PC - audit, detect, alert,
harden, and block attacking IPs at the Windows firewall. It does NOT
help attack other people's systems: no hacking back, no scanning hosts
you do not own, no credential attacks. Retaliation hacking is illegal
even against an attacker; blocking + evidence preservation is the
playbook here.

Capabilities (all actions are audit-logged to notes/security_log.txt):
- network audit: suspicious listeners + remote-connected processes
- firewall: add/remove Windows Firewall rules to block hostile IPs
- hosts guard: detect (and optionally restore) hosts-file tampering
- persistence audit: review + remove suspicious startup entries
- failed logons: count and inspect brute-force attempts (Event 4625)
- process kill: terminate a malicious process by PID/name
- full sweep: one command that runs every check and reports findings

Admin-privileged operations (firewall, hosts restore, some startup
removals) use PowerShell auto-elevation; Windows shows one UAC prompt.
"""
import os
import subprocess
import time

PROJECT = os.path.dirname(os.path.abspath(__file__))


def _log_path():
    """Resolve at call time so tests can redirect tools.NOTES_DIR."""
    import tools
    return os.path.join(tools.NOTES_DIR, "security_log.txt")

SECURITY_BANNER = (
    "DEFENSIVE ONLY: Phoenix protects THIS pc. It never attacks other "
    "systems - no hacking back, no scanning hosts you don't own, no "
    "credential attacks. Block, log, alert, harden - that's the arsenal.")

# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #

def _ps(command, timeout=60):
    """Run a PowerShell command, return (ok, stdout, stderr)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             command],
            capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout, r.stderr
    except Exception as exc:
        return False, "", str(exc)


def _ps_admin(command, timeout=90):
    """Run a PowerShell command elevated (one UAC prompt per batch)."""
    b64 = _ps_b64(command)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
             "-ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand',"
             "'%s'" % b64],
            capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


def _ps_b64(command):
    import base64
    return base64.b64encode(command.encode("utf-16-le")).decode()


def audit_log(action, detail=""):
    logp = _log_path()
    os.makedirs(os.path.dirname(logp), exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = ("[%s] %s %s" % (stamp, action, detail)).rstrip()
    try:
        with open(logp, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    return line


# --------------------------------------------------------------------------- #
# 1. network audit
# --------------------------------------------------------------------------- #

_SUSPICIOUS_PORTS = {4444: "metasploit default", 5555: "adb/adware common",
                     31337: "back orifice", 1337: "elite/leh common",
                     9999: "common trojan", 5900: "vnc (check if intended)",
                     3389: "rdp (check exposure)"}


def audit_network(_args=None):
    issues = []
    ok, out, _ = _ps(
        "Get-NetTCPConnection -State Listen | Select-Object "
        "OwningProcess,LocalAddress,LocalPort | ConvertTo-Csv -NoTypeInformation")
    if not ok:
        return "! Could not read TCP table: %s" % _last_err()
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(out)))
    pid_names = {}
    seen_pids = set(r["OwningProcess"] for r in rows)
    if seen_pids:
        ok2, out2, _ = _ps(
            "Get-Process | Select-Object Id,ProcessName,Path | "
            "ConvertTo-Csv -NoTypeInformation")
        if ok2:
            for row in csv.DictReader(io.StringIO(out2)):
                pid_names[row["Id"]] = (row["ProcessName"], row.get("Path", ""))
    for r in rows:
        try:
            port = int(r["LocalPort"])
        except ValueError:
            continue
        pid = r["OwningProcess"]
        name, path = pid_names.get(pid, ("?", ""))
        reason = None
        if port in _SUSPICIOUS_PORTS:
            reason = _SUSPICIOUS_PORTS[port]
        if r["LocalAddress"] in ("0.0.0.0", "::") and port > 0 and name in (
                "powershell", "cmd", "python", "wscript", "mshta"):
            reason = (reason or "script host listening on ALL interfaces")
        if reason:
            issues.append("  ! port %d LISTEN by %s (pid %s) - %s"
                          % (port, name, pid, reason))
    # established remote connections from script hosts
    ok3, out3, _ = _ps(
        "Get-NetTCPConnection -State Established | "
        "Where-Object {$_.RemoteAddress -notmatch "
        "'^(127\\.|::1|10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)'} | "
        "Select-Object OwningProcess,RemoteAddress,RemotePort | "
        "ConvertTo-Csv -NoTypeInformation")
    if ok3:
        for r in csv.DictReader(io.StringIO(out3)):
            name, _p = pid_names.get(r["OwningProcess"], ("?", ""))
            if name in ("powershell", "cmd", "wscript", "mshta", "rundll32"):
                issues.append("  ! %s (pid %s) connected to %s:%s - "
                              "script host talking to the internet"
                              % (name, r["OwningProcess"],
                                 r["RemoteAddress"], r["RemotePort"]))
    # external IP discovery (defensive: show what this machine exposes)
    lines = (["NETWORK AUDIT - %d finding(s)" % len(issues)] + issues
             if issues else ["NETWORK AUDIT - nothing suspicious in "
                             "listeners/connections."])
    audit_log("NETWORK_AUDIT", "%d findings" % len(issues))
    return "\n".join(lines)


def _last_err():
    return "(see notes/security_log.txt or run again elevated)"


# --------------------------------------------------------------------------- #
# 2. firewall blocks
# --------------------------------------------------------------------------- #

def firewall_block(args):
    ip = str(args.get("ip") or "").strip()
    note = str(args.get("note") or "phoenix-shield").strip()[:40]
    import re as _re
    octets = ip.split(".") if ip else []
    if not (len(octets) == 4 and all(o.isdigit() and 0 <= int(o) <= 255
                                     for o in octets)):
        return "! Give a valid IPv4 address (e.g. 203.0.113.7)."
    ok = _ps_admin(
        "New-NetFirewallRule -DisplayName 'PhoenixBlock %s' "
        "-Direction Outbound -Action Block -RemoteAddress %s | "
        "New-NetFirewallRule -DisplayName 'PhoenixBlock %s in' "
        "-Direction Inbound -Action Block -RemoteAddress %s"
        % (note, ip, note, ip))
    audit_log("FIREWALL_BLOCK", "%s (%s) admin=%s" % (ip, note, ok))
    if not ok:
        return ("! Could not add the firewall block (needs your approval "
                "on the UAC prompt). Attacker IP noted in the log.")
    return ("BLOCKED %s at the Windows firewall (in+outbound, rule "
            "'PhoenixBlock %s'). Logged." % (ip, note))


def firewall_unblock(args):
    ip = str(args.get("ip") or "").strip()
    ok, out, _ = _ps(
        "Get-NetFirewallRule -DisplayName 'PhoenixBlock*' | "
        "Where-Object { $_.DisplayName -like '*%s*' } | "
        "Remove-NetFirewallRule" % ip if ip else
        "Get-NetFirewallRule -DisplayName 'PhoenixBlock*' | "
        "Remove-NetFirewallRule")
    audit_log("FIREWALL_UNBLOCK", ip or "ALL phoenix blocks")
    if not ok:
        return "! Remove failed - try again and approve the UAC prompt."
    return "Removed Phoenix firewall blocks%s." % (" for " + ip if ip else "")


def firewall_list(_args=None):
    ok, out, _ = _ps(
        "Get-NetFirewallRule -DisplayName 'PhoenixBlock*' | Select-Object "
        "DisplayName,Enabled,Action | ConvertTo-Csv -NoTypeInformation")
    return ("Active Phoenix blocks:\n" + (out.strip() or "(none)")
            if ok and out.strip() else "No Phoenix firewall blocks active.")


# --------------------------------------------------------------------------- #
# 3. hosts-file guard
# --------------------------------------------------------------------------- #

_HOSTS = r"C:\Windows\System32\drivers\etc\hosts"


def _baseline_path():
    import tools
    return os.path.join(tools.NOTES_DIR, "hosts_baseline.txt")


def hosts_check(_args=None):
    try:
        with open(_HOSTS, "r", encoding="utf-8", errors="replace") as fh:
            current = fh.read()
    except OSError as exc:
        return "! Cannot read hosts file: %s" % exc
    problems = []
    for line in current.splitlines():
        s = line.strip()
        if s.startswith("#") or not s:
            continue
        parts = s.split()
        if len(parts) >= 2 and not s.startswith("#"):
            dom = parts[1].lower()
            if any(d in dom for d in ("google.com", "facebook.com",
                                      "microsoft.com", "antivirus",
                                      "windowsupdate", "virusTotal".lower())
                   ) and parts[0].lower() in ("0.0.0.0", "127.0.0.1"):
                problems.append("  ! hosts redirects %s -> %s (classic "
                                "security-site blocking)" % (dom, parts[0]))
        if len(parts) >= 2 and "pay" in s.lower():
            problems.append("  ! suspicious hosts line: %r" % s[:80])
    baseline = _baseline_path()
    if not os.path.exists(baseline):
        try:
            with open(baseline, "w", encoding="utf-8") as fh:
                fh.write(current)
            audit_log("HOSTS_BASELINE_SAVED")
        except OSError:
            pass
    else:
        try:
            with open(baseline, "r", encoding="utf-8") as fh:
                base = fh.read()
        except OSError:
            base = ""
        if base and base != current:
            import difflib
            diff = [l for l in difflib.unified_diff(
                base.splitlines(), current.splitlines(), lineterm="")][2:8]
            problems.append("  ! hosts file changed since baseline:\n    "
                            + "\n    ".join(diff[:6]))
    if problems:
        audit_log("HOSTS_TAMPER", "%d problems" % len(problems))
        return "HOSTS GUARD - %d problem(s):\n%s" % (len(problems),
                                                     "\n".join(problems))
    return "HOSTS GUARD - clean. Baseline saved/verified."


def hosts_restore(_args=None):
    baseline = _baseline_path()
    if not os.path.exists(baseline):
        return "! No baseline saved yet - run hosts_check first."
    ok = _ps_admin(
        "Copy-Item '%s' '%s' -Force" % (baseline.replace("'", "''"),
                                        _HOSTS))
    audit_log("HOSTS_RESTORE", "admin=%s" % ok)
    return ("Hosts file restored from baseline." if ok
            else "! Restore failed (UAC declined?).")


# --------------------------------------------------------------------------- #
# 4. persistence audit (startup folders + Run keys + tasks)
# --------------------------------------------------------------------------- #

def startup_audit(_args=None):
    findings = []
    ok, out, _ = _ps(
        "$p=@();"
        "$p+=Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\"
        "CurrentVersion\\Run' -ErrorAction SilentlyContinue;"
        "$p+=Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\"
        "CurrentVersion\\Run' -ErrorAction SilentlyContinue;"
        "$p | Select-Object * -Exclude PS* | ConvertTo-Csv -NoTypeInformation")
    if ok and out.strip():
        import csv
        import io
        for row in csv.DictReader(io.StringIO(out)):
            for _k, v in row.items():
                if v and v not in (row.get("PSPath", ""),):
                    findings.append("  - %s" % str(v)[:110])
    startup_dir = os.path.expandvars(
        r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
    if os.path.isdir(startup_dir):
        for f in os.listdir(startup_dir):
            findings.append("  - [startup folder] %s" % f)
    ok2, out2, _ = _ps(
        "Get-ScheduledTask | Where-Object {$_.State -ne 'Disabled' -and "
        "$_.TaskPath -notlike '\\Microsoft*'} | Select-Object TaskName,"
        "TaskPath | ConvertTo-Csv -NoTypeInformation")
    if ok2 and out2.strip():
        import csv
        import io
        for row in csv.DictReader(io.StringIO(out2)):
            findings.append("  - [task] %s%s"
                            % (row.get("TaskPath", ""), row.get("TaskName", "")))
    audit_log("STARTUP_AUDIT", "%d entries" % len(findings))
    if not findings:
        return "STARTUP AUDIT - no non-Microsoft startup entries found."
    return ("STARTUP AUDIT - %d non-Microsoft startup entr(ies). "
            "Remove shady ones with startup_remove:\n%s"
            % (len(findings), "\n".join(findings[:25])))


def startup_remove(args):
    target = str(args.get("name") or "").strip()
    if not target:
        return "! Give the startup entry name (from startup_audit)."
    low = target.lower()
    if low in ("phoenixhud",) or "phoenix" in low:
        return "! That looks like Phoenix's own autostart - refusing."
    results = []
    r1, _o, _e = _ps_admin(
        "Remove-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\"
        "CurrentVersion\\Run' -Name '%s' -ErrorAction SilentlyContinue"
        % target.replace("'", "''"))
    results.append("HKCU Run: %s" % ("removed" if r1 else "not found/denied"))
    r2, _o, _e = _ps_admin(
        "Unregister-ScheduledTask -TaskName '%s' -Confirm:$false "
        "-ErrorAction SilentlyContinue" % target.replace("'", "''"))
    results.append("Scheduled task: %s"
                   % ("removed" if r2 else "not found/denied"))
    startup_dir = os.path.expandvars(
        r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup")
    removed_file = False
    if os.path.isdir(startup_dir):
        for f in os.listdir(startup_dir):
            if target.lower() in f.lower():
                try:
                    os.remove(os.path.join(startup_dir, f))
                    removed_file = True
                except OSError:
                    pass
    if removed_file:
        results.append("Startup folder file: removed")
    audit_log("STARTUP_REMOVE", target)
    return "Attempted removal of %r:\n  " % target + "\n  ".join(results)


# --------------------------------------------------------------------------- #
# 5. failed logons (brute force detection)
# --------------------------------------------------------------------------- #

def logon_attacks(args):
    hours = 24
    try:
        hours = max(1, min(168, int(args.get("hours") or 24)))
    except (TypeError, ValueError):
        pass
    ok, out, _ = _ps(
        "try { Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625;"
        "StartTime=(Get-Date).AddHours(-%d)} -MaxEvents 200 -ErrorAction Stop"
        " | Group-Object {$_.Properties[13].Value} | Sort-Object Count -"
        "Descending | Select-Object -First 10 Count,Name | "
        "ConvertTo-Csv -NoTypeInformation } catch { '' }" % hours)
    if not ok or not out.strip():
        return ("No failed logon attempts recorded in the last %dh "
                "(or audit needs admin to read - that is normal on "
                "home machines)." % hours)
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(out)))
    top = "\n".join("  %sx failed logon from/source: %s"
                    % (r["Count"], r["Name"]) for r in rows[:10])
    worst = max(int(r["Count"]) for r in rows)
    advice = ""
    if worst >= 20:
        advice = ("\n  ! %d failures is brute-force territory. Consider: "
                  "firewall_block the source, or disable exposed RDP."
                  % worst)
    audit_log("LOGON_ATTACKS", "%d sources, worst=%d" % (len(rows), worst))
    return "FAILED LOGONS (last %dh):\n%s%s" % (hours, top, advice)


# --------------------------------------------------------------------------- #
# 6. process kill
# --------------------------------------------------------------------------- #

def kill_process(args):
    target = str(args.get("target") or "").strip()
    if not target:
        return "! Give a PID or process name (from audit_network)."
    import re as _re
    if _re.fullmatch(r"\d+", target):
        ok, _o, _e = _ps("Stop-Process -Id %s -Force" % target)
    else:
        if not _re.fullmatch(r"[A-Za-z0-9_.\- ]{1,64}", target):
            return "! Bad process name."
        ok, _o, _e = _ps("Stop-Process -Name '%s' -Force" % target)
    audit_log("KILL_PROCESS", "%s ok=%s" % (target, ok))
    return ("Killed %r." % target) if ok else \
        ("! Could not kill %r (maybe already gone or needs admin)."
         % target)


# --------------------------------------------------------------------------- #
# 7. full sweep + admin utilities
# --------------------------------------------------------------------------- #

def full_sweep(_args=None):
    sections = [SECURITY_BANNER, "", audit_network(), "", hosts_check(), "",
                startup_audit(), "", logon_attacks({})]
    audit_log("FULL_SWEEP")
    return "\n".join(sections)


# --------------------------------------------------------------------------- #
# OS editing powers (your own machine, audited)
# --------------------------------------------------------------------------- #

def os_admin(args):
    """Scoped OS-editing utilities. Every action is audit-logged."""
    op = str(args.get("op") or "").strip().lower()
    value = str(args.get("value") or "").strip()

    if op == "env_set":
        name, val = value.split("=", 1) if "=" in value else ("", "")
        if not name or not val:
            return "! env_set needs 'NAME=value'."
        ok, _o, _e = _ps(
            "[Environment]::SetEnvironmentVariable('%s','%s','User')"
            % (name.replace("'", ""), val.replace("'", "")))
        audit_log("OS env_set", "%s ok=%s" % (name, ok))
        return ("User env var %s set." % name) if ok else \
            "! env_set failed."
    if op == "power":
        plan = {"high": "SCHEME_MIN", "balanced": "SCHEME_BALANCED",
                "saver": "SCHEME_MAX"}.get(value.lower())
        if not plan:
            return "! power needs: high | balanced | saver"
        ok, _o, _e = _ps("powercfg /setactive %s" % plan)
        audit_log("OS power", value)
        return ("Power plan: %s." % value) if ok else "! powercfg failed."
    if op == "reg_read":
        ok, out, _e = _ps(
            "Get-ItemProperty '%s' -ErrorAction SilentlyContinue | "
            "ConvertTo-Json -Compress" % value.replace(";", "\\"))
        return out.strip() or "! No such registry key (or access denied)."
    if op == "process_list":
        return _ps("Get-Process | Sort-Object CPU -Descending | "
                   "Select-Object -First 15 Id,ProcessName,CPU | "
                   "Format-Table -AutoSize | Out-String")[1]
    if op == "service":
        action = str(args.get("arg") or "")
        if action not in ("start", "stop"):
            return "! service needs arg=start|stop and value=<name>"
        ok, _o, _e = _ps("%s-Service -Name '%s'" % (
            action.capitalize(), value.replace("'", "")))
        audit_log("OS service", "%s %s ok=%s" % (value, action, ok))
        return ("%s %s." % (value, "started" if action == "start"
                            else "stopped")) if ok else "! service failed."
    return ("! Unknown op %r. Ops: env_set, power, reg_read, "
            "process_list, service" % op)
