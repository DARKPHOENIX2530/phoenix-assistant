"""
Phoenix Desktop - Futuristic GUI frontend for the Phoenix assistant.

Design direction (researched from GitHub + CustomTkinter docs + glassmorphism
trends 2024/2025):

  - CustomTkinter by TomSchimansky (github.com/TomSchimansky/CustomTkinter) -
    modern dark widgets, rounded corners, system-theme awareness
  - Glassmorphism panels: translucent dark surfaces, subtle borders, frosted
    feel via layered frames
  - AI chat-native layout: message stream, user/AI bubble distinction,
    auto-scroll, status bar with live provider/model state

If customtkinter is not installed the app falls back to plain tkinter with a
dark theme so it is still usable.

Run:  python futuristic_gui.py
       python futuristic_gui.py --selftest   (offline self-check)
"""

import json
import os
import sys
import threading
import time

# --------------------------------------------------------------------------- #
# Backend detection
# --------------------------------------------------------------------------- #
try:
    import customtkinter as ctk

    _HAS_CTK = True
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("dark-blue")
except ImportError:
    _HAS_CTK = False
    ctk = None  # type: ignore

# Phoenix core.
import assistant
import config
import tools
import voice
import ui

# --------------------------------------------------------------------------- #
# Shared constants
# --------------------------------------------------------------------------- #
FONT_FAMILY = "Segoe UI"
FONT_SIZE_MSG = 13
BG_DARK = "#0a0e14"
PANEL_BG = "#111820"
PANEL_BORDER = "#1e2a36"
TEXT_PRIMARY = "#e2e8f0"
TEXT_SECONDARY = "#8899aa"
ACCENT_GLOW = "#ffc94d"       # golden yellow accent
USER_BLUE = "#d4a537"
AI_BG = "#2a2210"
USER_BG = "#332a12"

# =========================================================================== #
#  Raw tkinter helper classes (always available)
# =========================================================================== #
from tkinter import (Button as TkButton, Canvas as TkCanvas,
                     Entry as TkEntry, Frame as TkFrame, Label as TkLabel,
                     Scrollbar as TkScrollbar, Tk)


class _RawChatScrollView(TkFrame):
    """Scrollable message list (raw tkinter)."""

    def __init__(self, master, **kw):
        kw.setdefault("bg", BG_DARK)
        super().__init__(master, **kw)
        self.pack(fill="both", expand=True)
        self._msgs = []
        self._canvas = TkCanvas(self, bg=BG_DARK, highlightthickness=0, bd=0)
        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar = TkScrollbar(self, orient="vertical",
                                command=self._canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._inner = TkFrame(self._canvas, bg=BG_DARK)
        self._canvas.create_window((0, 0), window=self._inner, anchor="nw",
                                   tags="inner")
        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind_all("<Button-4>", self._on_mousewheel)
        self._canvas.bind_all("<Button-5>", self._on_mousewheel)

    def _on_inner_configure(self, event=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event=None):
        self._canvas.itemconfig("inner", width=event.width)

    def _on_mousewheel(self, event=None):
        if event.num == 4:
            self._canvas.yview("scroll", -1, "units")
        elif event.num == 5:
            self._canvas.yview("scroll", 1, "units")
        else:
            self._canvas.yview("scroll", -1 * (event.delta / 120), "units")

    def add_message(self, text, kind):
        bubble = _RawMessageBubble(self._inner, text, kind)
        self._msgs.append(bubble)
        self.scroll_bottom()

    def scroll_bottom(self):
        try:
            self._canvas.yview_moveto(1)
        except Exception:
            pass

    def clear(self):
        for m in self._msgs:
            try:
                m.destroy()
            except Exception:
                pass
        self._msgs.clear()
        for child in list(self._inner.children.values()):
            try:
                child.destroy()
            except Exception:
                pass


class _RawMessageBubble(TkFrame):
    def __init__(self, master, text, kind, **kw):
        bg = AI_BG if kind == "ai" else USER_BG
        super().__init__(master, bg=bg, padx=10, pady=8, **kw)
        self.text = text
        self.kind = kind
        dot = TkLabel(self, text="\u25cf", font=(FONT_FAMILY, 9),
                      bg=self["bg"],
                      fg=ACCENT_GLOW if self.kind == "ai" else USER_BLUE)
        dot.pack(side="left", anchor="n", padx=(0, 6))
        label = TkLabel(self, text=self.text,
                        font=(FONT_FAMILY, FONT_SIZE_MSG),
                        bg=self["bg"], fg=TEXT_PRIMARY,
                        justify="left", wraplength=560,
                        padx=4, pady=2)
        label.pack(side="left", fill="x", expand=True)


class _RawSidebar(TkFrame):
    def __init__(self, master, phoenix, **kw):
        kw.setdefault("bg", PANEL_BG)
        super().__init__(master, **kw)
        self.phoenix = phoenix
        self.chat = None
        self.status_bar = None
        self.root = None
        self._build()

    def _build(self):
        pad = 16
        logo = TkLabel(self, text="\u26a1 PHOENIX",
                       font=(FONT_FAMILY, 22, "bold"),
                       bg=self["bg"], fg=ACCENT_GLOW)
        logo.pack(pady=(pad, 0), padx=pad, anchor="w")
        sub = TkLabel(self, text="desktop assistant",
                      font=(FONT_FAMILY, 11),
                      bg=self["bg"], fg=TEXT_SECONDARY)
        sub.pack(padx=pad, anchor="w")
        TkFrame(self, height=2, bg=PANEL_BORDER).pack(fill="x", padx=pad,
                                                       pady=8)
        self.lbl_provider = TkLabel(self, text="", font=(FONT_FAMILY, 12,
                                     "bold"), bg=self["bg"], fg=TEXT_PRIMARY)
        self.lbl_provider.pack(padx=pad, anchor="w")
        self.lbl_model = TkLabel(self, text="", font=(FONT_FAMILY, 11),
                                  bg=self["bg"], fg=TEXT_SECONDARY)
        self.lbl_model.pack(padx=pad, anchor="w")
        self.lbl_status = TkLabel(self, text="", font=(FONT_FAMILY, 11),
                                   bg=self["bg"], fg=TEXT_SECONDARY)
        self.lbl_status.pack(padx=pad, anchor="w")
        TkFrame(self, height=2, bg=PANEL_BORDER).pack(fill="x", padx=pad,
                                                       pady=8)
        self.voice_btn = TkButton(self, text="\U0001f3aa  Voice OFF",
                                  font=(FONT_FAMILY, 12),
                                  bg="#1a1f26", fg=TEXT_PRIMARY,
                                  activebackground="#2a3a44",
                                  relief="flat", command=self._toggle_voice)
        self.voice_btn.pack(fill="x", padx=pad, pady=8)
        self.btn_new = TkButton(self, text="\u271a  New chat",
                                font=(FONT_FAMILY, 12),
                                bg="#2a2210", fg=TEXT_PRIMARY,
                                activebackground="#453617",
                                relief="flat", command=self._new_chat)
        self.btn_new.pack(fill="x", padx=pad, pady=4)
        self.btn_save = TkButton(self, text="\U0001f4be  Save chat",
                                  font=(FONT_FAMILY, 12),
                                  bg="#2a2210", fg=TEXT_PRIMARY,
                                  activebackground="#453617",
                                  relief="flat", command=self._save_chat)
        self.btn_save.pack(fill="x", padx=pad, pady=4)
        self.btn_quit = TkButton(self, text="\u2715  Quit",
                                  font=(FONT_FAMILY, 12),
                                  bg="#2a1a1a", fg=TEXT_PRIMARY,
                                  activebackground="#3a2222",
                                  relief="flat", command=self._quit)
        self.btn_quit.pack(fill="x", padx=pad, pady=(4, pad))

    def refresh_info(self):
        name, spec = self.phoenix.active()
        label = spec.get("label", name) if spec else name
        model = spec.get("model", "?") if spec else "?"
        self.lbl_provider.configure(text=label)
        self.lbl_model.configure(text="model: " + model)
        has_key = bool(spec and spec.get("api_key"))
        status = "connected" if has_key else "no key set"
        self.lbl_status.configure(text=status)
        self.voice_btn.configure(
            text="\U0001f3aa  Voice ON" if self.phoenix._speak_flag
            else "\U0001f3aa  Voice OFF")

    def _toggle_voice(self):
        self.phoenix._speak_flag = not self.phoenix._speak_flag
        self.phoenix.cfg["settings"]["voice_out"] = self.phoenix._speak_flag
        self.phoenix._save()
        self.refresh_info()
        if self.phoenix._speak_flag:
            voice.speak("Voice enabled")

    def _new_chat(self):
        self.phoenix.history = []
        self.chat.clear()
        self.status_bar.update_text("Conversation cleared")

    def _save_chat(self):
        reply = self.phoenix.handle("/save")
        if reply:
            self.status_bar.update_text(reply)

    def _quit(self):
        self.root.quit()
        self.root.destroy()


class _RawStatusBar(TkFrame):
    def __init__(self, master, **kw):
        kw.setdefault("bg", PANEL_BG)
        super().__init__(master, **kw)
        self._lbl = TkLabel(self, text="", font=(FONT_FAMILY, 10),
                            bg=self["bg"], fg=TEXT_SECONDARY)
        self._lbl.pack(side="left", padx=12, pady=4)

    def update_text(self, text):
        self._lbl.configure(text=text)


class _RawInputBar(TkFrame):
    def __init__(self, master, on_send, on_voice, **kw):
        kw.setdefault("bg", PANEL_BG)
        super().__init__(master, **kw)
        self.on_send = on_send
        self.on_voice = on_voice
        self._build()

    def _build(self):
        self.entry = TkEntry(self, font=(FONT_FAMILY, 14),
                             fg=TEXT_PRIMARY, bg="#0d1520",
                             insertbackground=ACCENT_GLOW,
                             relief="flat", bd=1, highlightthickness=1,
                             highlightbackground=PANEL_BORDER)
        self.entry.pack(side="left", fill="x", padx=12, pady=8, ipady=6)
        self.entry.bind("<Return>", lambda e: self._send())
        self.send_btn = TkButton(self, text="\u27a4", font=(FONT_FAMILY, 14),
                                 bg=ACCENT_GLOW, fg=BG_DARK,
                                 activebackground="#ffd97a",
                                 relief="flat", command=self._send)
        self.send_btn.pack(side="right", padx=(0, 8), pady=8)
        self.voice_btn = TkButton(self, text="\U0001f3aa",
                                   font=(FONT_FAMILY, 14),
                                   bg="#1a2740", fg=TEXT_PRIMARY,
                                   activebackground="#2a3a44",
                                   relief="flat", command=self._voice)
        self.voice_btn.pack(side="right", padx=(0, 12), pady=8)

    def _send(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.on_send(text)

    def _voice(self):
        self.on_voice()

    def set_sending(self, sending):
        state = "disabled" if sending else "normal"
        self.send_btn.configure(state=state)
        self.entry.configure(state=state)


# =========================================================================== #
#  CustomTkinter helper classes (only defined when CTk is available)
# =========================================================================== #
if _HAS_CTK:

    class _CTkChatScrollView(ctk.CTkScrollableFrame):
        def __init__(self, master, **kw):
            kw.setdefault("fg_color", "transparent")
            super().__init__(master, **kw)
            self.grid(row=0, column=0, sticky="nsew")
            self._msgs = []

        def add_message(self, text, kind):
            bubble = _CTkMessageBubble(self, text, kind)
            self._msgs.append(bubble)
            self.scroll_bottom()

        def scroll_bottom(self):
            try:
                self._parent_canvas.yview_moveto(1)
            except Exception:
                pass

        def clear(self):
            for m in self._msgs:
                try:
                    m.destroy()
                except Exception:
                    pass
            self._msgs.clear()


    class _CTkMessageBubble(ctk.CTkFrame):
        def __init__(self, master, text, kind, **kw):
            super().__init__(master, **kw)
            self.text = text
            self.kind = kind
            self._build()

        def _build(self):
            self.grid(padx=4, pady=(2, 2), sticky="ew")
            bg = "#1a2740" if self.kind == "ai" else "#0d2137"
            frame = ctk.CTkFrame(self, corner_radius=12, fg_color=bg)
            frame.grid(row=0, column=0 if self.kind == "ai" else 1,
                       padx=(8 if self.kind == "ai" else 0), pady=6,
                       sticky="nsew")
            dot = ctk.CTkLabel(frame, text="\u25cf", font=(FONT_FAMILY, 9),
                               text_color=ACCENT_GLOW
                               if self.kind == "ai" else USER_BLUE)
            dot.grid(row=0, column=0, sticky="nsw", padx=(0, 6))
            label = ctk.CTkLabel(frame, text=self.text,
                                 font=(FONT_FAMILY, FONT_SIZE_MSG),
                                 text_color=TEXT_PRIMARY, wraplength=580,
                                 justify="left")
            label.grid(row=0, column=1, sticky="nsew")
            frame.grid_columnconfigure(1, weight=1)
            self.grid_columnconfigure(0, weight=1)
            self.grid_columnconfigure(1, weight=0)


    class _CTkSidebar(ctk.CTkFrame):
        def __init__(self, master, phoenix, **kw):
            kw.setdefault("fg_color", PANEL_BG)
            super().__init__(master, **kw)
            self.phoenix = phoenix
            self.chat = None
            self.status_bar = None
            self.root = None
            self._build()

        def _build(self):
            pad = 16
            logo = ctk.CTkLabel(self, text="\u26a1 PHOENIX",
                                font=(FONT_FAMILY, 22, "bold"),
                                text_color=ACCENT_GLOW)
            logo.grid(row=0, column=0, padx=pad, pady=(pad, 4), sticky="w")
            sub = ctk.CTkLabel(self, text="desktop assistant",
                               font=(FONT_FAMILY, 11),
                               text_color=TEXT_SECONDARY)
            sub.grid(row=1, column=0, padx=pad, pady=(0, pad), sticky="w")

            def sep(row, pady):
                s = ctk.CTkFrame(self, height=2, fg_color=PANEL_BORDER)
                s.grid(row=row, column=0, padx=pad, pady=pady, sticky="ew")

            sep(2, 8)
            self.lbl_provider = ctk.CTkLabel(self, text="", font=(FONT_FAMILY,
                                      12, "bold"), text_color=TEXT_PRIMARY)
            self.lbl_provider.grid(row=3, column=0, padx=pad, pady=(8, 2),
                                   sticky="w")
            self.lbl_model = ctk.CTkLabel(self, text="", font=(FONT_FAMILY, 11),
                                          text_color=TEXT_SECONDARY)
            self.lbl_model.grid(row=4, column=0, padx=pad, pady=(0, 2),
                                sticky="w")
            self.lbl_status = ctk.CTkLabel(self, text="", font=(FONT_FAMILY, 11),
                                            text_color=TEXT_SECONDARY)
            self.lbl_status.grid(row=5, column=0, padx=pad, pady=(0, 8),
                                 sticky="w")
            sep(6, 8)

            self.voice_btn = ctk.CTkButton(
                self, text="\U0001f3aa  Voice OFF",
                font=(FONT_FAMILY, 12),
                fg_color=("#3a3014" if self.phoenix._speak_flag else "#1a1f26"),
                hover_color="#2a3a44", corner_radius=8,
                command=self._toggle_voice)
            self.voice_btn.grid(row=7, column=0, padx=pad, pady=8, sticky="ew")
            self.btn_new = ctk.CTkButton(
                self, text="\u271a  New chat",
                font=(FONT_FAMILY, 12),
                fg_color="#1a2740", hover_color="#223350",
                corner_radius=8, command=self._new_chat)
            self.btn_new.grid(row=8, column=0, padx=pad, pady=4, sticky="ew")
            self.btn_save = ctk.CTkButton(
                self, text="\U0001f4be  Save chat",
                font=(FONT_FAMILY, 12),
                fg_color="#1a2740", hover_color="#223350",
                corner_radius=8, command=self._save_chat)
            self.btn_save.grid(row=9, column=0, padx=pad, pady=4, sticky="ew")
            self.btn_quit = ctk.CTkButton(
                self, text="\u2715  Quit",
                font=(FONT_FAMILY, 12),
                fg_color="#2a1a1a", hover_color="#3a2222",
                corner_radius=8, command=self._quit)
            self.btn_quit.grid(row=10, column=0, padx=pad, pady=(4, pad),
                               sticky="ew")
            self.grid_columnconfigure(0, weight=1)

        def refresh_info(self):
            name, spec = self.phoenix.active()
            label = spec.get("label", name) if spec else name
            model = spec.get("model", "?") if spec else "?"
            self.lbl_provider.configure(text=label)
            self.lbl_model.configure(text="model: " + model)
            has_key = bool(spec and spec.get("api_key"))
            status = "connected" if has_key else "no key set"
            self.lbl_status.configure(text=status)
            self.voice_btn.configure(
                text="\U0001f3aa  Voice ON" if self.phoenix._speak_flag
                else "\U0001f3aa  Voice OFF")

        def _toggle_voice(self):
            self.phoenix._speak_flag = not self.phoenix._speak_flag
            self.phoenix.cfg["settings"]["voice_out"] = self.phoenix._speak_flag
            self.phoenix._save()
            self.refresh_info()
            if self.phoenix._speak_flag:
                voice.speak("Voice enabled")

        def _new_chat(self):
            self.phoenix.history = []
            self.chat.clear()
            self.status_bar.update_text("Conversation cleared")

        def _save_chat(self):
            reply = self.phoenix.handle("/save")
            if reply:
                self.status_bar.update_text(reply)

        def _quit(self):
            self.root.quit()
            self.root.destroy()


    class _CTkStatusBar(ctk.CTkFrame):
        def __init__(self, master, **kw):
            kw.setdefault("fg_color", PANEL_BG)
            super().__init__(master, **kw)
            self._lbl = ctk.CTkLabel(self, text="", font=(FONT_FAMILY, 10),
                                     text_color=TEXT_SECONDARY)
            self._lbl.grid(row=0, column=0, padx=12, pady=4, sticky="w")
            self.grid_columnconfigure(0, weight=1)

        def update_text(self, text):
            self._lbl.configure(text=text)


    class _CTkInputBar(ctk.CTkFrame):
        def __init__(self, master, on_send, on_voice, **kw):
            kw.setdefault("fg_color", PANEL_BG)
            super().__init__(master, **kw)
            self.on_send = on_send
            self.on_voice = on_voice
            self._build()

        def _build(self):
            self.entry = ctk.CTkEntry(self,
                                      placeholder_text="Ask Phoenix...",
                                      font=(FONT_FAMILY, 14),
                                      fg_color="#0d1520",
                                      border_color=PANEL_BORDER,
                                      corner_radius=10,
                                      text_color=TEXT_PRIMARY,
                                      placeholder_text_color=TEXT_SECONDARY,
                                      height=44)
            self.entry.grid(row=0, column=0, padx=12, pady=8, sticky="ew",
                            ipady=4)
            self.entry.bind("<Return>", lambda e: self._send())
            send_btn = ctk.CTkButton(self, text="\u27a4",
                                     font=(FONT_FAMILY, 16),
                                     fg_color=ACCENT_GLOW,
                                     hover_color="#ffd97a",
                                     corner_radius=10, width=44, height=44,
                                     command=self._send)
            send_btn.grid(row=0, column=1, padx=(0, 8), pady=8, sticky="ns")
            voice_btn = ctk.CTkButton(self, text="\U0001f3aa",
                                      font=(FONT_FAMILY, 16),
                                      fg_color="#1a2740",
                                      hover_color="#2a3a44",
                                      corner_radius=10, width=44, height=44,
                                      command=self._voice)
            voice_btn.grid(row=0, column=2, padx=(0, 12), pady=8, sticky="ns")
            self.grid_columnconfigure(0, weight=1)

        def _send(self):
            text = self.entry.get().strip()
            if not text:
                return
            self.entry.delete(0, "end")
            self.on_send(text)

        def _voice(self):
            self.on_voice()

        def set_sending(self, sending):
            state = "disabled" if sending else "normal"
            self.send_btn.configure(state=state)
            self.entry.configure(state=state)


# =========================================================================== #
#  Class selection
# =========================================================================== #
if _HAS_CTK:
    ChatScrollView = _CTkChatScrollView
    SidebarCls = _CTkSidebar
    StatusBarCls = _CTkStatusBar
    InputBarCls = _CTkInputBar
    MainWindowBase = ctk.CTk
else:
    ChatScrollView = _RawChatScrollView
    SidebarCls = _RawSidebar
    StatusBarCls = _RawStatusBar
    InputBarCls = _RawInputBar
    MainWindowBase = Tk


# =========================================================================== #
#  MainWindow
# =========================================================================== #
class MainWindow(MainWindowBase):
    def __init__(self, phoenix, **kw):
        super().__init__(**kw)
        self.title("Phoenix Desktop")
        self.geometry("960x640")
        self.minsize(720, 480)
        if _HAS_CTK:
            self.configure(fg_color=BG_DARK)
            self.grid_rowconfigure(0, weight=1)
            self.grid_columnconfigure(1, weight=1)
            self.grid_rowconfigure(1, weight=0)
            self.grid_rowconfigure(2, weight=0)
        else:
            self.configure(bg=BG_DARK)
        self.phoenix = phoenix
        self._sending = False
        self._build()
        self._setup_tray()
        self._refresh_sidebar()
        self._update_status("Ready \u2014 type a message or use /commands")

    def _build(self):
        # Chat area (centre) - create first so sidebar can reference it
        self.chat = ChatScrollView(self)
        if _HAS_CTK:
            self.chat.grid(row=0, column=1, sticky="nsew")
        else:
            self.chat.pack(fill="both", expand=True)

        self.status_bar = StatusBarCls(self)
        if _HAS_CTK:
            self.status_bar.grid(row=1, column=1, sticky="ew")
        else:
            self.status_bar.pack(side="bottom", fill="x")

        self.input_bar = InputBarCls(self, self._on_send, self._on_voice)
        if _HAS_CTK:
            self.input_bar.grid(row=2, column=1, sticky="ew")
        else:
            self.input_bar.pack(side="bottom", fill="x",
                                before=self.status_bar)

        # Sidebar (left) - now has chat/status_bar to reference
        self.sidebar = SidebarCls(self, self.phoenix)
        self.sidebar.chat = self.chat
        self.sidebar.status_bar = self.status_bar
        self.sidebar.root = self
        if _HAS_CTK:
            self.sidebar.grid(row=0, column=0, sticky="ns")
        else:
            self.sidebar.pack(side="left", fill="y")

    def _setup_tray(self):
        try:
            import pystray  # noqa
            from PIL import Image  # noqa
            img = Image.new("RGB", (16, 16), ACCENT_GLOW)
            menu = pystray.Menu(
                pystray.MenuItem("Show", self._toggle_show),
                pystray.MenuItem("Quit", self._quit_tray),
            )
            self.tray_icon = pystray.Icon(
                "phoenix", img, "Phoenix Desktop", menu=menu)
            self.tray_icon.run_detached()
        except Exception:
            self.tray_icon = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.withdraw()
        self._update_status(
            "Minimized to tray \u2014 right-click tray icon to quit")

    def _toggle_show(self):
        if self.winfo_viewable():
            self.withdraw()
        else:
            self.deiconify()
            self.lift()
        self._refresh_sidebar()

    def _quit_tray(self, icon=None, item=None):
        if self.tray_icon:
            self.tray_icon.stop()
        self.quit()
        self.destroy()

    def _refresh_sidebar(self):
        self.sidebar.refresh_info()

    def _update_status(self, text):
        self.status_bar.update_text(text)

    def _on_send(self, text, sync=False):
        """Send a message.  If sync=True the reply is computed inline (used
        by the selftest); otherwise it is dispatched on a background thread
        so the GUI stays responsive."""
        if self._sending:
            return
        self._sending = True
        self.input_bar.set_sending(True)
        self._update_status("thinking...")
        self.chat.add_message(text, "user")
        self.chat.scroll_bottom()

        if sync:
            reply = self.phoenix.handle(text)
            self._on_reply(reply, text)
        else:
            def work():
                reply = self.phoenix.handle(text)
                try:
                    self.after(0, lambda: self._on_reply(reply, text))
                except RuntimeError:
                    # mainloop not running (selftest) - ignore
                    pass

            t = threading.Thread(target=work, daemon=True)
            t.start()

    def _on_reply(self, reply, user_text):
        self._sending = False
        self.input_bar.set_sending(False)
        if reply is None:
            self._update_status("bye!")
            try:
                self.after(800, lambda: self.destroy())
            except RuntimeError:
                pass
            return
        if reply:
            if reply.startswith("!"):
                self._update_status(reply)
            else:
                self._update_status("ready")
            self.chat.add_message(reply, "ai")
            if self.phoenix._speak_flag and not reply.startswith("!"):
                voice.speak(reply)
        self.chat.scroll_bottom()
        self._refresh_sidebar()

    def _on_voice(self):
        self._update_status("listening...")
        try:
            heard = voice.listen()
        except Exception as exc:
            self._update_status("voice error: %s" % exc)
            return
        self._update_status("ready")
        if heard:
            txt = heard.strip()
            try:
                self.input_bar.entry.delete(0, "end")
                self.input_bar.entry.insert(0, txt)
            except Exception:
                pass
            self._on_send(txt)


# =========================================================================== #
#  Entry points
# =========================================================================== #
def selftest():
    """Offline sanity check: window creation + mock chat flow."""
    from tkinter import TclError
    cfg = config.load()
    cfg["settings"]["provider"] = "mock"
    cfg["settings"]["voice_out"] = False
    bot = assistant.Phoenix(cfg)
    app = MainWindow(bot)
    app._on_send("hello", sync=True)
    # Give the UI a moment to render the reply
    for _ in range(20):
        try:
            app.update_idletasks()
            app.update()
        except TclError:
            break
        time.sleep(0.03)
    found = any(
        m.kind == "ai" and "mock" in (getattr(m, "text", "") or "").lower()
        for m in app.chat._msgs
    )
    app.destroy()
    if found:
        print("[GUI selftest] OK - window created, mock chat flow works.")
        return 0
    print("[GUI selftest] FAIL - no mock reply found.")
    return 1


def main():
    cfg = config.load()
    bot = assistant.Phoenix(cfg)
    app = MainWindow(bot)
    app.mainloop()
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
