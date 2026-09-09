"""Phoenix - personal assistant for modest PCs.

Run:              python main.py                (interactive chat)
One-shot:         python main.py --once "hi"    (single reply, for scripts)
Self test:        python main.py --selftest     (offline, mock, no key)
"""
import argparse
import os
import sys

# Make the Windows console UTF-8-safe regardless of codepage.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import assistant
import config
import ui


def banner():
    print(ui.bold("=" * 60))
    print(ui.bold("  PHOENIX v%s - your personal AI assistant" % config.PHOENIX_VERSION))
    print(ui.dim("  Lightweight edition: thinking runs on free cloud APIs,"
                 " great on 8 GB laptops"))
    print(ui.bold("=" * 60))
    print("  First time?  " + ui.cyan("/setup") + "  connects a free AI "
          "(Gemini, Groq, OpenRouter, ...).")
    print("  " + ui.cyan("/help") + " for commands, or " + ui.cyan(
        "/provider mock") + " to run fully offline.")
    print()


def repl(cfg):
    bot = assistant.Phoenix(cfg)
    banner()
    name = cfg["settings"].get("provider", "mock")
    spec = cfg["providers"].get(name, {})
    if spec.get("requires_key", True) and not spec.get("api_key"):
        print(ui.yellow("[notice] '%s' has no API key yet - run /setup "
                        "(recommended) or /key to go live." % name))
    while True:
        try:
            line = input(ui.cyan("you> ") + " ")
        except (EOFError, KeyboardInterrupt):
            print()
            print(ui.dim("bye!"))
            break
        line = line.strip()
        if not line:
            if cfg["settings"].get("voice_in"):
                heard = _voice_in()
                if heard:
                    line = heard
                else:
                    continue
            else:
                continue
        try:
            reply = bot.handle(line)
        except KeyboardInterrupt:
            print(ui.dim("(interrupted - Ctrl+C again at the prompt to quit)"))
            continue
        if reply is None:
            print(ui.dim("bye!"))
            break
        if reply:
            if bot.last_kind == "ai":
                print(ui.green("phoenix> "))
            print(reply)


def _voice_in():
    import voice
    print(ui.yellow("(listening - press Enter on an empty line to talk, "
                    "or just type)"))
    print(ui.cyan("you> ") + " ", end="")
    try:
        line = input()
    except (EOFError, KeyboardInterrupt):
        return None
    if line.strip():
        return line.strip()
    text = voice.listen()
    if text:
        print(ui.dim("(heard: %s)" % text))
        return text
    return None


def one_shot(cfg, text):
    """Answer one prompt and exit. Exit code 2 if the provider complains."""
    bot = assistant.Phoenix(cfg)
    reply = bot.handle(text)
    if reply is None:
        return 0
    if reply:
        if bot.last_kind == "ai":
            print(ui.green("phoenix> "))
        print(reply)
    return 2 if reply.startswith("!") else 0


def selftest():
    """Offline sanity run: command routing + chat loop on the mock provider."""
    cfg = config.load()
    cfg["settings"]["provider"] = "mock"
    cfg["settings"]["voice_out"] = False
    cfg["settings"]["max_history"] = 4
    bot = assistant.Phoenix(cfg)
    turns = [
        "hello",
        "who are you?",
        "what can you do?",
        "remember my favorite color is blue",
        "remember I like pizza",
        "/notes",
        "/read memory",
        "/time",
        "/sys",
        "tell me something fun",
    ]
    print("[selftest] offline mock run, %d turns..." % len(turns))
    for t in turns:
        reply = bot.handle(t)
        if reply is None:
            print("[selftest] FAIL: unexpected quit on %r" % t)
            return 1
        if not reply.strip():
            print("[selftest] FAIL: empty reply on %r" % t)
            return 1
    # History trimming sanity.
    if len(bot.history) > 9:
        print("[selftest] FAIL: history not trimmed (%d msgs)" % len(bot.history))
        return 1
    saved = bot.handle("/save")
    if "saved to" not in (saved or "").lower():
        print("[selftest] FAIL: /save did not export a transcript")
        return 1
    print("[selftest] OK - modules import, commands route, loop runs, "
          "history trims, transcript exports.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="phoenix",
        description="Phoenix - personal assistant on free cloud AIs")
    parser.add_argument("--selftest", "--demo", action="store_true",
                        help="offline self-test with the mock provider")
    parser.add_argument("--once", metavar="TEXT",
                        help="answer one prompt and exit (exit 2 on error)")
    parser.add_argument("--version", action="store_true",
                        help="print the version and exit")
    args = parser.parse_args()

    if args.version:
        print("Phoenix %s" % config.PHOENIX_VERSION)
        return 0

    cfg = config.load()

    if args.selftest:
        return selftest()
    if args.once:
        return one_shot(cfg, args.once)

    repl(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
