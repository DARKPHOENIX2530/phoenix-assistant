"""Phoenix mind: persistent memory + self-evolving skills.

Design goals (per the constraint: tiny storage + RAM footprint):

  - Everything is ONE JSON file (notes/mind.json). Typically a few KB.
  - Zero dependencies beyond the Python standard library.
  - No background threads, no watchers: mind reads and writes only when
    Phoenix is answering a message. Idle RAM cost is a single dict.
  - Memory recall is lexical-relevance scoring (token overlap with a
    tiny stopword filter) - no embeddings, no vector store, no model.
  - Memories decay: entries unused for a long time score lower and the
    oldest weakest ones are pruned automatically, so the file cannot
    grow forever.
  - Skills are saved as parameterized step lists (JSON), versioned with
    a success/fail tally. When a similar goal appears, Phoenix reuses
    and improves the best-known skill instead of re-deriving it.

Public API (used by tools.py / assistant.py):
    memory_put(text, kind)      -> dict
    memory_recall(query, k)     -> list of dicts (best matches first)
    memory_all()                -> list of dicts (newest first)
    memory_forget(match)        -> int (removed count)
    memory_stats()              -> dict
    skill_save(name, description, steps)   -> dict
    skill_match(goal_text, k)   -> list of (name, score) candidates
    skill_get(name)             -> dict or None
    skill_record(name, success) -> dict (tally + evolution)
    skill_list()                -> list of dicts
    skill_forget(name)          -> bool
    context_block(query, budget_chars) -> str for the system prompt
    MIND_FILE                   -> path of the backing JSON
"""
import json
import os
import re
import time

import tools  # reuse tools.NOTES_DIR so tests can redirect it

MIND_FILE = os.path.join(tools.NOTES_DIR, "mind.json")

MAX_MEMORIES = 300          # hard cap; weakest/oldest are pruned first
MAX_SKILLS = 60
MEM_MAX_CHARS = 400         # per memory
SKILL_MAX_STEPS = 12
CONTEXT_CHARS = 1800        # budget for the injected system-prompt block
HALF_LIFE_DAYS = 45.0       # relevance halts halving every ~1.5 months

STOP = set("""
a an and are as at be but by for from has have i if in into is it its of on
or our so that the their them then there these they this to was we were what
when where which who will with you your me my mine do does did done can
could should would just very really about get got make made please tell say
""".split())

WORD_RE = re.compile(r"[a-z0-9]{2,}")


def _now():
    return time.time()


def _load():
    try:
        with open(MIND_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return _empty()
        data.setdefault("memories", [])
        data.setdefault("skills", {})
        return data
    except (OSError, ValueError):
        return _empty()


def _empty():
    return {"memories": [], "skills": {}}


def _save(data):
    os.makedirs(os.path.dirname(MIND_FILE), exist_ok=True)
    tmp = MIND_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, MIND_FILE)      # atomic; no partial files on crash


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #
def _tokens(text):
    return [t for t in WORD_RE.findall(str(text).lower()) if t not in STOP]


def _key_of(text):
    """Loose identity key for dedupe (sorted content words)."""
    return " ".join(sorted(set(_tokens(text)))[:24])


# --------------------------------------------------------------------------- #
# persistent memory
# --------------------------------------------------------------------------- #
def memory_put(text, kind="fact"):
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "empty memory text"}
    text = text[:MEM_MAX_CHARS]
    data = _load()

    key = _key_of(text)
    for m in data["memories"]:
        if m.get("key") == key:
            m["text"] = text                # refresh wording
            m["count"] = m.get("count", 1) + 1
            m["last_used"] = _now()
            _save(data)
            return {"ok": True, "action": "reinforced", "id": m["id"],
                    "text": m["text"]}

    mem = {
        "id": int(_now() * 1000) % 1_000_000_000,
        "text": text,
        "kind": kind if kind in ("fact", "pref", "event", "skill_note")
                else "fact",
        "key": key,
        "created": _now(),
        "last_used": _now(),
        "count": 1,
    }
    data["memories"].append(mem)
    _prune(data)
    _save(data)
    return {"ok": True, "action": "stored", "id": mem["id"], "text": text}


def _score(mem, qtokens, now=None):
    now = now or _now()
    mtokens = set(_tokens(mem.get("text", "")))
    if not mtokens:
        return 0.0
    overlap = len(mtokens.intersection(qtokens))
    base = overlap / (len(mtokens) ** 0.5)          # sublinear length norm
    age_days = max(0.0, (now - mem.get("created", now)) / 86400.0)
    decay = 0.5 ** (age_days / HALF_LIFE_DAYS)
    use = 1.0 + 0.1 * min(10, int(mem.get("count", 1)) - 1)
    return base * decay * use


def memory_recall(query, k=6):
    qtokens = set(_tokens(query))
    if not qtokens:
        return []
    data = _load()
    now = _now()
    scored = []
    for m in data["memories"]:
        s = _score(m, qtokens, now)
        if s > 0:
            scored.append((s, m))
    scored.sort(key=lambda pair: (-pair[0], -pair[1].get("created", 0)))
    results = []
    for s, m in scored[:max(1, int(k))]:
        m["last_used"] = now                # touching counts as use
        m["count"] = m.get("count", 1) + 1
        results.append({"id": m["id"], "text": m["text"],
                        "kind": m.get("kind", "fact"), "score": round(s, 3)})
    if results:
        _save(data)
    return results


def memory_all():
    data = _load()
    out = sorted(data["memories"], key=lambda m: -m.get("created", 0))
    return [{"id": m["id"], "text": m["text"], "kind": m.get("kind", "fact"),
             "count": m.get("count", 1)} for m in out]


def memory_forget(match):
    """Delete memories whose text contains ALL query tokens. Returns count."""
    qtokens = set(_tokens(match))
    data = _load()
    kept, removed = [], 0
    for m in data["memories"]:
        mtokens = set(_tokens(m.get("text", "")))
        if qtokens and qtokens.issubset(mtokens):
            removed += 1
        else:
            kept.append(m)
    if removed:
        data["memories"] = kept
        _save(data)
    return removed


def _prune(data):
    """Hard caps so the file cannot grow forever."""
    mems = data["memories"]
    if len(mems) > MAX_MEMORIES:
        mems.sort(key=lambda m: (m.get("count", 1), m.get("last_used", 0)))
        data["memories"] = mems[-MAX_MEMORIES:]
    if len(data["skills"]) > MAX_SKILLS:
        items = sorted(data["skills"].items(),
                       key=lambda kv: (kv[1].get("uses", 0),
                                       kv[1].get("updated", 0)))
        for name, _ in items[:len(data["skills"]) - MAX_SKILLS]:
            del data["skills"][name]


def memory_stats():
    data = _load()
    kinds = {}
    for m in data["memories"]:
        kinds[m.get("kind", "fact")] = kinds.get(m.get("kind", "fact"), 0) + 1
    return {"memories": len(data["memories"]),
            "skills": len(data["skills"]),
            "kinds": kinds,
            "file_kb": round(os.path.getsize(MIND_FILE) / 1024.0, 1)
            if os.path.exists(MIND_FILE) else 0.0}


# --------------------------------------------------------------------------- #
# self-evolving skills
# --------------------------------------------------------------------------- #
def _norm_name(name):
    name = re.sub(r"[^a-z0-9_]+", "_", str(name or "").lower()).strip("_")
    return name[:40] or "skill"


def skill_save(name, description, steps):
    name = _norm_name(name)
    if not isinstance(steps, list) or not steps:
        return {"ok": False, "error": "steps must be a non-empty list"}
    steps = [str(s).strip() for s in steps if str(s).strip()]
    steps = steps[:SKILL_MAX_STEPS]
    if not description:
        return {"ok": False, "error": "description required"}

    data = _load()
    existing = data["skills"].get(name)
    now = _now()
    if existing:
        existing["description"] = str(description)[:300]
        existing["steps"] = steps
        existing["version"] = existing.get("version", 1) + 1
        existing["updated"] = now
        entry = existing
        action = "improved"
    else:
        entry = {
            "name": name,
            "description": str(description)[:300],
            "steps": steps,
            "version": 1,
            "uses": 0,
            "wins": 0,
            "fails": 0,
            "created": now,
            "updated": now,
            "last_goal": "",
        }
        data["skills"][name] = entry
        action = "created"
    _prune(data)
    _save(data)
    return {"ok": True, "action": action, "name": name,
            "version": entry["version"], "steps": entry["steps"]}


def skill_match(goal_text, k=3):
    qtokens = set(_tokens(goal_text))
    if not qtokens:
        return []
    data = _load()
    scored = []
    for name, sk in data["skills"].items():
        stokens = set(_tokens(name + " " + sk.get("description", "")))
        overlap = len(stokens.intersection(qtokens))
        if not overlap:
            continue
        reliability = ((sk.get("wins", 0) + 1.0) /
                       (sk.get("wins", 0) + sk.get("fails", 0) + 2.0))
        freshness = 0.5 ** (max(0.0, (_now() - sk.get("updated", _now())) /
                                86400.0) / (HALF_LIFE_DAYS * 2))
        scored.append((round(overlap * reliability * freshness, 3), name))
    scored.sort(reverse=True)
    return scored[:max(1, int(k))]


def skill_get(name):
    return _load()["skills"].get(_norm_name(name))


def skill_record(name, success):
    name = _norm_name(name)
    data = _load()
    sk = data["skills"].get(name)
    if not sk:
        return {"ok": False, "error": "no skill named %s" % name}
    if success:
        sk["wins"] = sk.get("wins", 0) + 1
    else:
        sk["fails"] = sk.get("fails", 0) + 1
    sk["uses"] = sk.get("uses", 0) + 1
    sk["updated"] = _now()
    _save(data)
    return {"ok": True, "name": name, "uses": sk["uses"],
            "wins": sk["wins"], "fails": sk["fails"],
            "version": sk["version"]}


def skill_list():
    data = _load()
    out = []
    for name, sk in data["skills"].items():
        out.append({"name": name, "description": sk.get("description", ""),
                    "version": sk.get("version", 1),
                    "uses": sk.get("uses", 0),
                    "win_rate": ("%.0f%%" % (100.0 * sk.get("wins", 0) /
                                             max(1, sk.get("uses", 0) or 1))),
                    })
    out.sort(key=lambda s: -s["uses"])
    return out


def skill_forget(name):
    name = _norm_name(name)
    data = _load()
    if name in data["skills"]:
        del data["skills"][name]
        _save(data)
        return True
    return False


# --------------------------------------------------------------------------- #
# system-prompt context block
# --------------------------------------------------------------------------- #
def context_block(query, budget_chars=CONTEXT_CHARS):
    """What Phoenix should 'know' right now, as a compact text block."""
    parts = []

    recalled = memory_recall(query, k=6) if query and query.strip() else []
    if recalled:
        lines = ["RELEVANT MEMORIES:"]
        for m in recalled:
            lines.append("- (%s) %s" % (m["kind"], m["text"]))
        parts.append("\n".join(lines))

    matches = skill_match(query or "", k=2) if query and query.strip() else []
    if matches:
        lines = ["KNOWN SKILLS (reuse these; record success/failure after "
                 "running):"]
        for score, name in matches:
            sk = skill_get(name)
            if not sk:
                continue
            steps = " -> ".join(sk["steps"][:SKILL_MAX_STEPS])
            lines.append("- %s (v%d, %d uses): %s. Steps: %s"
                         % (name, sk["version"], sk.get("uses", 0),
                            sk["description"], steps))
        parts.append("\n".join(lines))

    data = _load()
    prefs = [m for m in data["memories"] if m.get("kind") == "pref"]
    if prefs:
        recent = sorted(prefs, key=lambda m: -m.get("last_used", 0))[:5]
        parts.append("USER PREFERENCES:\n" +
                     "\n".join("- " + m["text"] for m in recent))

    block = "\n\n".join(parts)
    if len(block) > budget_chars:
        block = block[:budget_chars - 3].rstrip() + "..."
    return block
