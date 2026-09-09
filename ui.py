"""Tiny console helpers: optional ANSI colors, zero third-party deps.

Colors are only emitted when stdout is a real terminal AND the user has not
set NO_COLOR, so piping output or running in an old console stays clean.
"""
import os
import sys


def color_enabled():
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


_ENABLED = color_enabled()


def paint(text, code):
    return "\x1b[%sm%s\x1b[0m" % (code, text) if _ENABLED else text


def bold(text):
    return paint(text, "1")


def dim(text):
    return paint(text, "2")


def red(text):
    return paint(text, "31")


def green(text):
    return paint(text, "1;32")


def yellow(text):
    return paint(text, "33")


def cyan(text):
    return paint(text, "36")
