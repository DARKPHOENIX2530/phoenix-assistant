"""Browser identity awareness for Phoenix.

The user may have several Google accounts spread across several browsers
(Chrome profiles, Edge profiles, ...). Phoenix needs to understand things
like "open dragon's Gmail in Chrome" and act on THAT browser + THAT
profile - not just "a browser".

How it works:
- Real Chrome/Edge/Santa installs keep profiles in
  <user-data-dir>/<Profile-name> folders, and an index JSON called
  "Local State" maps folder names to human display names and the Google
  accounts signed into each profile (from "profile.info_cache").
- We read that index (read-only - no browsing history is touched), build
  identity records like:
      chrome | Profile 3 | dragon@gmail.com | Dragon Work
  and let the user (or the AI) refer to them by email, nickname or
  profile name.
- To OPEN a specific identity we launch the real browser exe with
  --profile-directory="<folder>" so the actual logged-in session is used.
  If the browser is already running without that profile dir, we first
  ask it to exit politely (a graceful WM_CLOSE), then relaunch pinned
  to the right profile.
- Nickname aliases (e.g. "dragon" -> chrome/Profile 3) are saved in
  notes/browser_identities.json so "open dragon gmail" keeps working.

Zero new dependencies: stdlib json + subprocess only.
"""
import json
import os
import re
import subprocess
import time

PROJECT = os.path.dirname(os.path.abspath(__file__))
ALIASES_PATH = os.path.join(PROJECT, "notes", "browser_identities.json")

# --------------------------------------------------------------------------- #
# Browser discovery
# --------------------------------------------------------------------------- #

# (key, display, [candidate exe paths], user-data dir or None for default)
_BROWSER_DEFS = [
    ("chrome", "Chrome",
     [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
      r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
      os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")],
     None),
    ("edge", "Edge",
     [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
      r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"],
     None),
    ("santa", "Santa",
     [os.path.expandvars(r"%LOCALAPPDATA%\Santa\Application\santa.exe"),
      r"C:\Program Files\Santa\Application\santa.exe",
      r"C:\Program Files (x86)\Santa\Application\santa.exe"],
     None),
]

# Chromium defaults the user-data dir to this when no --user-data-dir is used
_CHROMIUM_DEFAULT_UDD = os.path.expandvars(r"%LOCALAPPDATA%")


def _first_existing(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def installed_browsers():
    """[(key, display, exe_path, user_data_dir)] for every browser found."""
    out = []
    for key, display, exes, udd in _BROWSER_DEFS:
        exe = _first_existing(exes)
        if exe:
            out.append((key, display, exe, udd))
    return out


def _user_data_dir(key):
    """Chromium's default user-data directory for a given browser key."""
    base = os.path.expandvars(r"%LOCALAPPDATA%") if os.name == "nt" \
        else os.path.expanduser("~/.config")
    if os.name != "nt":
        if key == "chrome":
            return os.path.expanduser("~/.config/google-chrome")
        if key == "edge":
            return os.path.expanduser("~/.config/microsoft-edge")
        return os.path.join(base, key)
    # Windows: Chromium keeps profiles under <base>\<vendor>\<app>\User Data
    if key == "chrome":
        return os.path.join(base, "Google", "Chrome", "User Data")
    if key == "edge":
        return os.path.join(base, "Microsoft", "Edge", "User Data")
    if key == "santa":
        return os.path.join(base, "Santa", "User Data")
    return os.path.join(base, key, "User Data")


# --------------------------------------------------------------------------- #
# Profile / account extraction (read-only)
# --------------------------------------------------------------------------- #

def _read_local_state(udd):
    path = os.path.join(udd, "Local State")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _emails_from_cache(entry):
    """Pull every gaia email found in one profile's info_cache entry."""
    out = []
    for key in ("user_name", "email", "gaia_given_name", "gaia_name"):
        val = entry.get(key)
        if isinstance(val, str) and "@" in val:
            out.append(val.strip().lower())
    for acc in (entry.get("gaia_accounts") or {}):
        pass  # gaia_accounts is a dict of gaia-id -> list, values opaque
    return sorted(set(out))


def browser_profiles(key):
    """[(folder, display_name, [emails])] for one installed browser."""
    udd = _user_data_dir(key)
    state = _read_local_state(udd)
    if not state:
        return []
    cache = (state.get("profile") or {}).get("info_cache") or {}
    out = []
    for folder, entry in cache.items():
        if not isinstance(entry, dict):
            continue
        display = entry.get("name") or entry.get("gaia_name") or folder
        emails = _emails_from_cache(entry)
        out.append((folder, display, emails))
    return out


def load_aliases():
    """{"chrome::Profile 3": ["dragon", ...]} from notes/browser_identities.json."""
    try:
        with open(ALIASES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def identities(refresh=False):
    """All identities across all installed browsers.

    Returns a list of dicts:
      {"browser": "chrome", "browser_display": "Chrome", "folder":
       "Profile 3", "profile_name": "Dragon Work", "emails":
       ["dragon@gmail.com"], "aliases": ["dragon"]}
    Aliases come from the saved nickname map (see save_alias).
    """
    alias_map = load_aliases()
    out = []
    for key, display, _exe, _udd in installed_browsers():
        for folder, pname, emails in browser_profiles(key):
            al = alias_map.get(alias_key(key, folder), [])
            out.append({
                "browser": key,
                "browser_display": display,
                "folder": folder,
                "profile_name": pname,
                "emails": emails,
                "aliases": al,
            })
    return out


def resolve(query):
    """Best-effort match of a free-text mention to an identity.

    Matches, in order: exact alias -> exact email -> email prefix
    ("dragon@gmail.com" matches "dragon") -> profile name substring ->
    folder name. Returns the identity dict or None.
    """
    q = (query or "").strip().lower()
    if not q:
        return None
    qlist = [q]
    # "dragon gmail" / "dragon gmail in chrome" -> also try "dragon"
    qlist += [w for w in re.split(r"[^a-z0-9._@+-]+", q) if w]
    best = None
    for ident in identities():
        hay_alias = [a.lower() for a in ident["aliases"]]
        hay_email = [e.lower() for e in ident["emails"]]
        email_prefixes = [e.split("@")[0] for e in hay_email]
        names = [ident["profile_name"].lower(),
                 ident["folder"].lower()]
        for probe in qlist:
            if probe in hay_alias:
                return ident                      # exact alias wins
            if probe in hay_email:
                return ident                      # exact email
            if probe in email_prefixes and len(probe) >= 3:
                best = best or ident
            if any(probe and probe in n for n in names) and len(probe) >= 3:
                best = best or ident
    return best


def alias_key(browser_key, folder):
    return "%s::%s" % (browser_key, folder)


def save_alias(browser_key, folder, alias):
    """Remember that user's nickname 'alias' means this browser profile."""
    os.makedirs(os.path.dirname(ALIASES_PATH), exist_ok=True)
    data = load_aliases()
    key = alias_key(browser_key, folder)
    lst = data.setdefault(key, [])
    a = (alias or "").strip().lower()
    if a and a not in lst:
        lst.append(a)
    try:
        with open(ALIASES_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
    except OSError:
        pass
    return a


# --------------------------------------------------------------------------- #
# Launching a specific identity
# --------------------------------------------------------------------------- #

_BROWSERS_PROCS = {"chrome": "chrome.exe", "edge": "msedge.exe",
                   "santa": "santa.exe"}


def open_url_in_default_chrome(url):
    """Open a URL in Chrome WITHOUT pinning a profile - a normal tab in
    the running instance if Chrome is already up. Chrome is Phoenix's
    default browser (never the OS default, which may be Edge)."""
    for key, _disp, exe, _udd in installed_browsers():
        if key == "chrome" and exe and os.path.isfile(exe):
            try:
                flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                subprocess.Popen(
                    [exe, url], creationflags=flags,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
                return True
            except OSError:
                break
    try:
        import webbrowser
        return webbrowser.open(url)
    except Exception:
        return False


def close_running_browser(browser_key):
    """Politely close the running browser so we can relaunch it pinned to
    a specific profile (Chromium reuses an existing process otherwise and
    the --profile-directory flag would be ignored)."""
    exe = _BROWSERS_PROCS.get(browser_key)
    if not exe or os.name != "nt":
        return False
    try:
        subprocess.run(
            ["taskkill", "/IM", exe],
            capture_output=True, timeout=15)
    except Exception:
        return False
    time.sleep(1.5)
    return True


def launch(browser_key, folder, url, wait_seconds=0):
    """Launch the real browser pinned to a specific profile folder.

    Returns (ok, message). The URL opens inside THAT profile, i.e. with
    THAT Google account's cookies - "dragon's Gmail in Chrome" works.
    """
    for key, _disp, exe, _udd in installed_browsers():
        if key == browser_key:
            break
    else:
        return False, "! %s is not installed on this PC." % browser_key

    running = is_process_running(_BROWSERS_PROCS.get(browser_key, ""))
    if running:
        close_running_browser(browser_key)

    cmd = [exe, "--profile-directory=%s" % folder]
    if url:
        cmd.append(url)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    try:
        subprocess.Popen(
            cmd, creationflags=flags,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except OSError as exc:
        return False, "! Could not launch %s: %s" % (browser_key, exc)
    if wait_seconds:
        time.sleep(min(30, max(1, wait_seconds)))
    return True, "Launched %s pinned to profile %r%s" % (
        browser_key, folder, " at %s" % url if url else "")


def is_process_running(image_name):
    if not image_name or os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI",
                              "IMAGENAME eq %s" % image_name],
                             capture_output=True, text=True,
                             timeout=10).stdout.lower()
        return image_name.lower() in out
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Playwright help: reuse the SAME identity for automated reading
# --------------------------------------------------------------------------- #

def playwright_launch_args(browser_key, folder):
    """(executable_path, user_data_dir, extra_args) that make Playwright's
    launch_persistent_context drive the REAL browser with THAT profile
    (same cookies as the user's daily browser - the exact Google identity
    they asked for). Requires the real browser to be closed first; use
    close_running_browser() before launching."""
    if browser_key == "phoenix" or not browser_key:
        return None, None, []
    for key, _disp, exe, _udd in installed_browsers():
        if key == browser_key:
            return exe, _user_data_dir(key), ["--profile-directory=%s" % folder]
    return None, None, []
