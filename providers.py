"""Provider layer: one chat() call, many free backends.

Every keyed provider uses the OpenAI-compatible chat/completions protocol
(Groq, OpenRouter, GitHub Models, Cerebras, Ollama, Gemini's compat endpoint,
and any custom endpoint). That means one code path gives us history,
system prompts AND function/tool calling on every provider. "mock" is a
fully offline provider so the assistant is usable without any key.
"""
import json

import requests

UA = "PhoenixAssistant/1.0 (local desktop assistant)"


class ProviderError(Exception):
    """Friendly error surfaced to the user in the chat loop."""


def _post_json(url, headers, payload, timeout=120):
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError("Could not reach %s - %s" % (url, exc))
    if resp.status_code == 429:
        raise ProviderError(
            "Rate limited (HTTP 429) - free tiers throttle requests. Wait a "
            "moment, or switch provider with /provider <name>.")
    if resp.status_code >= 400:
        snippet = resp.text[:600].replace("\n", " ")
        raise ProviderError("Provider returned HTTP %s: %s" % (resp.status_code, snippet))
    return resp


def _content_text(content):
    """OpenAI-style content may be a string or a list of typed parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits = []
        for part in content:
            if isinstance(part, dict):
                bits.append(part.get("text") or part.get("content") or "")
            elif isinstance(part, str):
                bits.append(part)
        return "".join(bits)
    return str(content)


def _api_messages(system, messages, strip_tools=False):
    """Build the wire-format message list from our stored history."""
    out = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg.get("role", "user")
        if strip_tools and (role == "tool" or msg.get("tool_calls")):
            continue
        clone = {"role": role}
        content = msg.get("content")
        if content is None:
            content = "" if (strip_tools or msg.get("tool_calls")) else ""
        clone["content"] = _content_text(content)
        if msg.get("tool_calls"):
            clone["tool_calls"] = msg["tool_calls"]
        if role == "tool":
            clone["tool_call_id"] = msg.get("tool_call_id", "call_0")
        out.append(clone)
    return out


def _first_message(data, label):
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise ProviderError("%s error: %s" % (label, str(msg)[:500]))
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ProviderError(
            "Unexpected reply from %s - check the model name with /model "
            "(body: %s)" % (label, json.dumps(data)[:500]))


def _mock_reply(messages):
    """Offline stand-in so the assistant works with no internet and no key."""
    last = ""
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            last = _content_text(msg["content"])
            break
    last = last.strip()
    if not last:
        last = "(nothing)"
    low = last.lower()
    if low in ("hello", "hi", "hey", "yo"):
        reply = ("Hello! I am Phoenix running in OFFLINE MOCK mode right now - "
                 "no internet, no API key. I can still run local tools "
                 "(type /sys, /time, /search, /note, /remember, /app). "
                 "To make me think with a real free AI, type /setup or "
                 "/provider <name>.")
    elif low in ("who are you", "what are you"):
        reply = ("I'm Phoenix, a personal AI assistant that runs on "
                 "your own machine (works fine on 8 GB RAM - the heavy AI "
                 "thinking happens on free cloud APIs). Ask me to search, "
                 "remember things, check your system, open apps or the "
                 "browser. Type /help for the command list.")
    elif "help" in low or "what can you do" in low:
        reply = ("[mock] Try: /help  /setup  /search <query>  /sys  /time  "
                 "/remember <fact>  /note <name> <text>  /read <name>  "
                 "/app <name>  /listen  /voice  /say <text>")
    else:
        reply = ("[mock] Offline reply to: " + last[:220] +
                 "\nTip: /setup connects a real free AI (Gemini, Groq, "
                 "OpenRouter, GitHub Models or Cerebras).")
    return reply


def chat(spec, messages, system, tools=None, tool_runner=None):
    """Send messages to the active provider, following tool calls in a loop.

    spec        - the provider dict from config (type/base_url/model/api_key)
    messages    - our history: list of {"role","content"} (+ tool_calls)
    system      - the system prompt text
    tools       - list of OpenAI-style function definitions (or None)
    tool_runner - callable(name, args_dict) -> str result for each tool call
    """
    ptype = spec.get("type", "openai")

    if ptype == "mock":
        return _mock_reply(messages)

    if spec.get("requires_key", True) and not spec.get("api_key"):
        raise ProviderError(
            'No API key set for "%s". Run /setup to add keys, or use '
            "/provider mock for offline mode." % spec.get("label", spec))

    if not spec.get("base_url"):
        raise ProviderError('Provider "%s" has no base_url in config.json.'
                            % spec.get("label", spec))

    label = spec.get("label", "provider")
    headers = {
        "Authorization": "Bearer " + spec.get("api_key", ""),
        "Content-Type": "application/json",
        "User-Agent": UA,
    }
    url = spec["base_url"].rstrip("/") + "/chat/completions"
    model = spec.get("model", "")

    try:
        return _openai_loop(url, headers, model, label, messages, system,
                            tools, tool_runner)
    except ProviderError as exc:
        # Some backends (tiny local models, some free tiers) choke on the
        # tools field or on tool-call history. Retry once, plain text only.
        if tools and "400" in str(exc):
            try:
                return _openai_loop(url, headers, model, label, messages,
                                    system, None, None)
            except ProviderError:
                raise
        raise


def _openai_loop(url, headers, model, label, messages, system, tools,
                 tool_runner):
    wire = _api_messages(system, messages, strip_tools=False)
    if tools is None:
        # Drop tool-call history so tiny local models don't reject it.
        wire = _api_messages(system, messages, strip_tools=True)

    for _ in range(6):
        payload = {"model": model, "messages": wire}
        if tools:
            payload["tools"] = tools
        resp = _post_json(url, headers, payload)
        try:
            data = resp.json()
        except ValueError:
            raise ProviderError("Provider sent non-JSON reply: %s" % resp.text[:300])
        msg = _first_message(data, label)

        content = _content_text(msg.get("content"))
        calls = msg.get("tool_calls")

        if calls and tool_runner and tools:
            wire.append({"role": "assistant", "content": content,
                         "tool_calls": _clean_calls(calls)})
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    parsed = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except ValueError:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                result = tool_runner(name, parsed)
                if not isinstance(result, str):
                    result = str(result)
                if len(result) > 2500:
                    result = result[:2500] + "... [truncated]"
                wire.append({"role": "tool",
                             "tool_call_id": call.get("id", "call_0"),
                             "content": result})
            continue  # let the model read tool output and finish the answer

        if content:
            return content

        # No content and no tool calls: nothing useful happened.
        raise ProviderError("%s returned an empty reply." % label)

    return ("I could not finish that request after several tool calls. "
            "Try rephrasing or run /provider mock for offline mode.")


def _clean_calls(calls):
    cleaned = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        cleaned.append({
            "id": call.get("id") or "call_0",
            "type": "function",
            "function": {
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments") or "{}",
            },
        })
    return cleaned
