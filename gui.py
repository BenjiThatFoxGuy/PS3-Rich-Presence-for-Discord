# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "bs4",
#     "networkscan",
#     "pypresence",
#     "requests",
#     "Pillow",
# ]
# ///
"""PS3 Rich Presence for Discord — GUI

A polished tkinter interface with a dark Discord-like theme, live Discord
preview cards, connection management and scrolling log.
"""

import sys
import os
import threading
import tkinter as tk
from tkinter import ttk, font as tkfont
from io import BytesIO
from time import time

# Ensure the repo directory is on sys.path so we can import the engine
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from PS3RPD import PS3RPDEngine, default_config  # noqa: E402

# Try to import Pillow for image loading; fall back gracefully
try:
    from PIL import Image, ImageTk
    import requests as _requests

    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# ---------------------------------------------------------------------------
# Colour palette (Discord dark theme)
# ---------------------------------------------------------------------------
BG = "#2b2d31"
BG_SECONDARY = "#1e1f22"
BG_TERTIARY = "#232428"
TEXT = "#dcddde"
TEXT_MUTED = "#949ba4"
TEXT_HEADER = "#f2f3f5"
BLURPLE = "#5865f2"
GREEN = "#3ba55d"
RED = "#ed4245"
YELLOW = "#faa61a"
CARD_BG = "#1a1c2e"
CARD_BG2 = "#111214"

# ---------------------------------------------------------------------------
# Font helper
# ---------------------------------------------------------------------------

def _pick_font(size=10, bold=False):
    weight = "bold" if bold else "normal"
    for family in ("Segoe UI", "SF Pro Display", "Helvetica Neue", "Helvetica"):
        if family in tkfont.families():
            return (family, size, weight)
    return ("TkDefaultFont", size, weight)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PS3 Rich Presence for Discord")
        self.configure(bg=BG)
        self.minsize(1060, 660)
        self.geometry("1120x720")

        # Try to set the window icon
        icon_path = os.path.join(_HERE, "img", "ps32025.png")
        if os.path.isfile(icon_path):
            try:
                icon = tk.PhotoImage(file=icon_path)
                self.iconphoto(True, icon)
            except Exception:
                pass

        self._engine = None
        self._engine_thread = None
        self._running = False
        self._art_photo = None  # prevent GC
        self._art_loading = False

        self._status = {
            "connected_discord": False,
            "connected_ps3": False,
            "game_name": None,
            "title_id": None,
            "thermals": None,
            "image_url": None,
            "game_kind": None,
            "elapsed_seconds": None,
        }
        self._start_time = None

        self._build_ui()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Tick the elapsed timer display
        self._tick_elapsed()

    # ---- UI construction ----------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=0, minsize=260)
        self.columnconfigure(1, weight=3, minsize=420)
        self.columnconfigure(2, weight=0, minsize=280)
        self.rowconfigure(0, weight=1)

        self._build_left_panel()
        self._build_center_panel()
        self._build_right_panel()

    # -- left: settings --

    def _build_left_panel(self):
        frame = tk.Frame(self, bg=BG_SECONDARY, width=260)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 1))
        frame.grid_propagate(False)

        lbl = tk.Label(frame, text="Settings", font=_pick_font(13, True),
                       bg=BG_SECONDARY, fg=TEXT_HEADER, anchor="w")
        lbl.pack(fill="x", padx=14, pady=(14, 8))

        # -- PS3 IP
        self._add_label(frame, "PS3 IP Address")
        ip_row = tk.Frame(frame, bg=BG_SECONDARY)
        ip_row.pack(fill="x", padx=14, pady=(0, 6))
        self._ip_var = tk.StringVar()
        self._ip_entry = self._make_entry(ip_row, self._ip_var)
        self._ip_entry.pack(side="left", fill="x", expand=True)
        self._test_btn = self._make_button(ip_row, "Test", self._on_test_ip, width=5)
        self._test_btn.pack(side="right", padx=(6, 0))

        # -- Client ID
        self._add_label(frame, "Discord Client ID")
        self._cid_var = tk.StringVar()
        self._make_entry(frame, self._cid_var).pack(fill="x", padx=14, pady=(0, 6))

        # -- SteamGridDB
        self._add_label(frame, "SteamGridDB API Key (optional)")
        self._sgdb_var = tk.StringVar()
        e = self._make_entry(frame, self._sgdb_var)
        e.configure(show="•")
        e.pack(fill="x", padx=14, pady=(0, 6))

        # -- checkboxes
        self._temp_var = tk.BooleanVar(value=True)
        self._timer_var = tk.BooleanVar(value=True)
        self._appname_var = tk.BooleanVar(value=False)

        for text, var in [
            ("Show Temperature", self._temp_var),
            ("Show Timer", self._timer_var),
            ("Use App Name", self._appname_var),
        ]:
            cb = tk.Checkbutton(
                frame, text=text, variable=var,
                bg=BG_SECONDARY, fg=TEXT, selectcolor=BG_TERTIARY,
                activebackground=BG_SECONDARY, activeforeground=TEXT,
                font=_pick_font(10), anchor="w", highlightthickness=0,
                bd=0,
            )
            cb.pack(fill="x", padx=14, pady=2)

        # -- wait seconds
        self._add_label(frame, "Wait Seconds")
        self._wait_var = tk.IntVar(value=35)
        spin = tk.Spinbox(
            frame, from_=15, to=600, textvariable=self._wait_var,
            bg=BG_TERTIARY, fg=TEXT, insertbackground=TEXT,
            buttonbackground=BG_TERTIARY, font=_pick_font(10),
            highlightthickness=0, bd=1, relief="flat", width=6,
        )
        spin.pack(anchor="w", padx=14, pady=(0, 10))

        # -- save
        self._save_btn = self._make_button(frame, "Save Settings", self._on_save,
                                           accent=True)
        self._save_btn.pack(fill="x", padx=14, pady=(6, 14))

    # -- center: Discord preview --

    def _build_center_panel(self):
        frame = tk.Frame(self, bg=BG)
        frame.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=3)
        frame.rowconfigure(1, weight=1)

        lbl = tk.Label(frame, text="Discord Preview", font=_pick_font(13, True),
                       bg=BG, fg=TEXT_HEADER, anchor="w")
        lbl.pack(fill="x", pady=(0, 8))

        # Profile card (large) - needs enough height for art + text
        self._profile_canvas = tk.Canvas(frame, bg=BG, highlightthickness=0, height=260)
        self._profile_canvas.pack(fill="both", expand=True, pady=(0, 8))
        self._profile_canvas.bind("<Configure>", lambda e: self._draw_profile_card())

        # Sidebar card (compact)
        self._sidebar_canvas = tk.Canvas(frame, bg=BG, highlightthickness=0, height=64)
        self._sidebar_canvas.pack(fill="x")
        self._sidebar_canvas.bind("<Configure>", lambda e: self._draw_sidebar_card())

    # -- right: status + log --

    def _build_right_panel(self):
        frame = tk.Frame(self, bg=BG_SECONDARY, width=280)
        frame.grid(row=0, column=2, sticky="nsew", padx=(1, 0))
        frame.grid_propagate(False)

        lbl = tk.Label(frame, text="Status", font=_pick_font(13, True),
                       bg=BG_SECONDARY, fg=TEXT_HEADER, anchor="w")
        lbl.pack(fill="x", padx=14, pady=(14, 8))

        # Connection indicators
        ind_frame = tk.Frame(frame, bg=BG_SECONDARY)
        ind_frame.pack(fill="x", padx=14, pady=(0, 4))

        self._ps3_dot = tk.Canvas(ind_frame, width=12, height=12,
                                  bg=BG_SECONDARY, highlightthickness=0)
        self._ps3_dot.pack(side="left")
        self._ps3_dot.create_oval(2, 2, 10, 10, fill=RED, outline="", tags="dot")
        self._ps3_label = tk.Label(ind_frame, text="PS3: not connected",
                                   font=_pick_font(10), bg=BG_SECONDARY, fg=TEXT_MUTED)
        self._ps3_label.pack(side="left", padx=(4, 0))

        ind_frame2 = tk.Frame(frame, bg=BG_SECONDARY)
        ind_frame2.pack(fill="x", padx=14, pady=(0, 8))

        self._dc_dot = tk.Canvas(ind_frame2, width=12, height=12,
                                 bg=BG_SECONDARY, highlightthickness=0)
        self._dc_dot.pack(side="left")
        self._dc_dot.create_oval(2, 2, 10, 10, fill=RED, outline="", tags="dot")
        self._dc_label = tk.Label(ind_frame2, text="Discord: not connected",
                                  font=_pick_font(10), bg=BG_SECONDARY, fg=TEXT_MUTED)
        self._dc_label.pack(side="left", padx=(4, 0))

        # Buttons
        btn_frame = tk.Frame(frame, bg=BG_SECONDARY)
        btn_frame.pack(fill="x", padx=14, pady=(0, 8))

        self._discord_btn = self._make_button(btn_frame, "Connect to Discord",
                                              self._on_discord_connect, accent=True)
        self._discord_btn.pack(fill="x", pady=(0, 4))

        self._toggle_btn = self._make_button(btn_frame, "▶  Start",
                                             self._on_toggle)
        self._toggle_btn.pack(fill="x")

        # Log
        lbl2 = tk.Label(frame, text="Log", font=_pick_font(11, True),
                        bg=BG_SECONDARY, fg=TEXT_HEADER, anchor="w")
        lbl2.pack(fill="x", padx=14, pady=(8, 4))

        log_frame = tk.Frame(frame, bg=CARD_BG2)
        log_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self._log_text = tk.Text(
            log_frame, bg=CARD_BG2, fg=TEXT_MUTED,
            font=("Consolas", 9) if sys.platform == "win32" else ("Menlo", 9),
            wrap="word", state="disabled", bd=0, highlightthickness=0,
            insertbackground=TEXT_MUTED,
        )
        scrollbar = tk.Scrollbar(log_frame, command=self._log_text.yview,
                                 bg=BG_SECONDARY, troughcolor=CARD_BG2)
        self._log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._log_text.pack(side="left", fill="both", expand=True)

    # ---- widget helpers -----------------------------------------------------

    def _add_label(self, parent, text):
        lbl = tk.Label(parent, text=text, font=_pick_font(9),
                       bg=BG_SECONDARY, fg=TEXT_MUTED, anchor="w")
        lbl.pack(fill="x", padx=14, pady=(6, 2))

    def _make_entry(self, parent, var):
        e = tk.Entry(
            parent, textvariable=var,
            bg=BG_TERTIARY, fg=TEXT, insertbackground=TEXT,
            font=_pick_font(10), highlightthickness=0, bd=1, relief="flat",
        )
        return e

    def _make_button(self, parent, text, command, accent=False, width=None):
        bg = BLURPLE if accent else BG_TERTIARY
        btn = tk.Button(
            parent, text=text, command=command,
            bg=bg, fg="white", activebackground=bg,
            activeforeground="white", font=_pick_font(10, True),
            bd=0, highlightthickness=0, cursor="hand2",
            padx=10, pady=6,
        )
        if width:
            btn.configure(width=width)
        return btn

    # ---- settings load/save -------------------------------------------------

    def _load_settings(self):
        engine = PS3RPDEngine()
        engine.load_config()
        c = engine.config
        self._ip_var.set(c.get("ip", ""))
        self._cid_var.set(str(c.get("client_id", default_config["client_id"])))
        self._sgdb_var.set(c.get("steamgriddb_api_key", ""))
        self._temp_var.set(c.get("show_temp", True))
        self._timer_var.set(c.get("show_timer", True))
        self._appname_var.set(c.get("use_appname", False))
        self._wait_var.set(c.get("wait_seconds", 35))

    def _on_save(self):
        engine = PS3RPDEngine()
        engine.load_config()
        engine.config["ip"] = self._ip_var.get().strip()
        try:
            engine.config["client_id"] = int(self._cid_var.get().strip())
        except ValueError:
            pass
        engine.config["steamgriddb_api_key"] = self._sgdb_var.get().strip()
        engine.config["show_temp"] = self._temp_var.get()
        engine.config["show_timer"] = self._timer_var.get()
        engine.config["use_appname"] = self._appname_var.get()
        engine.config["wait_seconds"] = max(15, self._wait_var.get())
        engine.save_config()
        self._append_log("Settings saved.")
        # Propagate to running engine
        if self._engine is not None:
            self._engine.config = dict(engine.config)

    def _on_test_ip(self):
        ip = self._ip_var.get().strip()
        if not ip:
            self._append_log("Enter an IP address first.")
            return
        self._append_log(f"Testing {ip}...")

        def _test():
            ok = PS3RPDEngine.test_for_webman(ip)
            self.after(0, lambda: self._append_log(
                f"{ip}: webmanMOD {'found' if ok else 'NOT found'}."
            ))
            if ok:
                self.after(0, lambda: self._update_ps3_indicator(True))

        threading.Thread(target=_test, daemon=True).start()

    # ---- Discord connect/reconnect ------------------------------------------

    def _on_discord_connect(self):
        if self._engine is None:
            self._init_engine()
        self._discord_btn.configure(state="disabled", text="Connecting...")

        def _connect():
            ok = self._engine.connect_discord()
            self.after(0, lambda: self._discord_btn.configure(
                state="normal",
                text="Reconnect Discord" if not ok else "Reconnect Discord"
            ))

        threading.Thread(target=_connect, daemon=True).start()

    # ---- start/stop ---------------------------------------------------------

    def _on_toggle(self):
        if self._running:
            self._stop_engine()
        else:
            self._start_engine()

    def _init_engine(self):
        self._engine = PS3RPDEngine(
            log_callback=self._log_from_engine,
            status_callback=self._status_from_engine,
        )
        self._engine.load_config()
        # Apply GUI settings on top
        self._engine.config["ip"] = self._ip_var.get().strip()
        try:
            self._engine.config["client_id"] = int(self._cid_var.get().strip())
        except ValueError:
            pass
        self._engine.config["steamgriddb_api_key"] = self._sgdb_var.get().strip()
        self._engine.config["show_temp"] = self._temp_var.get()
        self._engine.config["show_timer"] = self._timer_var.get()
        self._engine.config["use_appname"] = self._appname_var.get()
        self._engine.config["wait_seconds"] = max(15, self._wait_var.get())

    def _start_engine(self):
        if self._engine is None:
            self._init_engine()

        if not self._engine.config.get("ip"):
            self._append_log("Set a PS3 IP address first.")
            return

        # Connect Discord first if needed
        if not self._engine._discord_connected:
            self._append_log("Connecting to Discord...")
            ok = False
            try:
                ok = self._engine.connect_discord()
            except Exception as e:
                self._append_log(f"Discord error: {e}")
            if not ok:
                self._append_log("Could not connect to Discord. Start anyway? Retrying in background.")

        self._engine._stop.clear()
        self._running = True
        self._start_time = time()
        self._toggle_btn.configure(text="⏹  Stop", bg=RED)
        self._append_log("Engine started.")

        self._engine_thread = threading.Thread(target=self._engine.run, daemon=True)
        self._engine_thread.start()

    def _stop_engine(self):
        if self._engine is not None:
            self._engine.stop()
        self._running = False
        self._toggle_btn.configure(text="▶  Start", bg=BG_TERTIARY)
        self._append_log("Engine stopped.")
        if self._engine is not None:
            self._engine.disconnect_discord()
        self._start_time = None
        self._status["connected_discord"] = False
        self._status["connected_ps3"] = False
        self._status["game_name"] = None
        self._update_indicators()
        self._draw_profile_card()
        self._draw_sidebar_card()

    # ---- callbacks from engine (called from engine thread) -------------------

    def _log_from_engine(self, msg):
        self.after(0, lambda: self._append_log(msg))

    def _status_from_engine(self, status):
        self.after(0, lambda: self._apply_status(status))

    def _apply_status(self, status):
        old_url = self._status.get("image_url")
        self._status = status
        self._update_indicators()
        self._draw_profile_card()
        self._draw_sidebar_card()

        # Load art if URL changed
        new_url = status.get("image_url")
        if new_url and new_url != old_url and new_url.startswith("http"):
            self._load_art_async(new_url)

    def _update_indicators(self):
        s = self._status
        # PS3
        ps3 = s.get("connected_ps3", False)
        self._update_ps3_indicator(ps3)
        # Discord
        dc = s.get("connected_discord", False)
        self._dc_dot.itemconfig("dot", fill=GREEN if dc else RED)
        self._dc_label.configure(
            text=f"Discord: {'connected' if dc else 'not connected'}",
            fg=TEXT if dc else TEXT_MUTED,
        )
        # Update reconnect button
        if not dc:
            self._discord_btn.configure(state="normal", text="Reconnect Discord")
        else:
            self._discord_btn.configure(state="disabled", text="Discord Connected")

    def _update_ps3_indicator(self, connected):
        ip = self._ip_var.get().strip()
        self._ps3_dot.itemconfig("dot", fill=GREEN if connected else RED)
        self._ps3_label.configure(
            text=f"PS3: {ip if connected else 'not connected'}",
            fg=TEXT if connected else TEXT_MUTED,
        )

    # ---- log ----------------------------------------------------------------

    def _append_log(self, msg):
        if not msg or msg.isspace():
            return
        self._log_text.configure(state="normal")
        self._log_text.insert("end", msg.rstrip() + "\n")
        # Trim to ~500 lines
        line_count = int(self._log_text.index("end-1c").split(".")[0])
        if line_count > 500:
            self._log_text.delete("1.0", f"{line_count - 500}.0")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ---- art loading --------------------------------------------------------

    def _load_art_async(self, url):
        if not _HAS_PIL or self._art_loading:
            return
        self._art_loading = True

        def _load():
            try:
                resp = _requests.get(url, timeout=15)
                img = Image.open(BytesIO(resp.content))
                img.thumbnail((120, 120), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.after(0, lambda: self._set_art(photo))
            except Exception:
                self.after(0, lambda: self._set_art(None))
            finally:
                self._art_loading = False

        threading.Thread(target=_load, daemon=True).start()

    def _set_art(self, photo):
        self._art_photo = photo
        self._draw_profile_card()

    # ---- canvas drawing -----------------------------------------------------

    def _draw_profile_card(self):
        c = self._profile_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return

        pad = 16
        card_x, card_y = pad, pad
        card_w, card_h = w - 2 * pad, h - 2 * pad
        if card_w < 50 or card_h < 50:
            return

        # Card background with rounded corners
        self._draw_rounded_rect(c, card_x, card_y, card_x + card_w, card_y + card_h,
                                radius=12, fill=CARD_BG)

        s = self._status
        game = s.get("game_name")
        kind = s.get("game_kind")

        # Header
        header_text = "PLAYING A GAME" if game and kind else "NOT PLAYING"
        header_color = GREEN if game and kind else TEXT_MUTED
        c.create_text(card_x + 20, card_y + 20, text=header_text,
                      font=_pick_font(9, True), fill=header_color, anchor="nw")

        # Art area
        art_x = card_x + 20
        art_y = card_y + 46
        art_size = min(120, card_h - 80)

        if self._art_photo is not None:
            c.create_image(art_x, art_y, image=self._art_photo, anchor="nw")
        else:
            # Placeholder rectangle
            colors = {"ps3": BLURPLE, "retro": "#9b59b6", "xmb": "#2ecc71"}
            color = colors.get(kind, BG_TERTIARY)
            c.create_rectangle(art_x, art_y, art_x + art_size, art_y + art_size,
                               fill=color, outline="")
            if game:
                # Abbreviation inside placeholder
                abbr = game[:3].upper()
                c.create_text(art_x + art_size // 2, art_y + art_size // 2,
                              text=abbr, font=_pick_font(16, True),
                              fill="white", anchor="center")

        # Text area (to the right of art)
        text_x = art_x + art_size + 16
        text_y = art_y + 4

        if game:
            c.create_text(text_x, text_y, text=game,
                          font=_pick_font(14, True), fill=TEXT_HEADER,
                          anchor="nw", width=card_w - art_size - 60)
            text_y += 24

            tid = s.get("title_id")
            if tid:
                c.create_text(text_x, text_y, text=tid,
                              font=_pick_font(9), fill=TEXT_MUTED, anchor="nw")
                text_y += 18

            thermals = s.get("thermals")
            if thermals:
                c.create_text(text_x, text_y, text=thermals,
                              font=_pick_font(10), fill=TEXT_MUTED, anchor="nw")
                text_y += 20

            elapsed = s.get("elapsed_seconds")
            if elapsed is not None and elapsed >= 0:
                mins, secs = divmod(int(elapsed), 60)
                hrs, mins = divmod(mins, 60)
                if hrs > 0:
                    elapsed_str = f"{hrs:02d}:{mins:02d}:{secs:02d} elapsed"
                else:
                    elapsed_str = f"{mins:02d}:{secs:02d} elapsed"
                c.create_text(text_x, text_y, text=elapsed_str,
                              font=_pick_font(10), fill=TEXT_MUTED, anchor="nw")
        else:
            c.create_text(text_x, text_y, text="Waiting for game data...",
                          font=_pick_font(11), fill=TEXT_MUTED, anchor="nw")

    def _draw_sidebar_card(self):
        c = self._sidebar_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return

        pad = 8
        self._draw_rounded_rect(c, pad, pad, w - pad, h - pad, radius=8, fill=CARD_BG2)

        s = self._status
        game = s.get("game_name")

        # Small icon
        icon_x, icon_y = pad + 12, pad + 8
        icon_size = h - 2 * pad - 16
        colors = {"ps3": BLURPLE, "retro": "#9b59b6", "xmb": "#2ecc71"}
        color = colors.get(s.get("game_kind"), BG_TERTIARY)
        c.create_oval(icon_x, icon_y, icon_x + icon_size, icon_y + icon_size,
                      fill=color, outline="")

        # Text
        text_x = icon_x + icon_size + 10
        if game:
            c.create_text(text_x, h // 2, text=f"Playing {game}",
                          font=_pick_font(10), fill=TEXT, anchor="w")
        else:
            c.create_text(text_x, h // 2, text="Not playing",
                          font=_pick_font(10), fill=TEXT_MUTED, anchor="w")

    def _draw_rounded_rect(self, canvas, x1, y1, x2, y2, radius=10, **kwargs):
        """Draw a rounded rectangle on a canvas."""
        r = radius
        canvas.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90,
                          style="pieslice", outline="", **kwargs)
        canvas.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90,
                          style="pieslice", outline="", **kwargs)
        canvas.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90,
                          style="pieslice", outline="", **kwargs)
        canvas.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90,
                          style="pieslice", outline="", **kwargs)
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, outline="", **kwargs)
        canvas.create_rectangle(x1, y1 + r, x2, y2 - r, outline="", **kwargs)

    # ---- elapsed timer tick -------------------------------------------------

    def _tick_elapsed(self):
        if self._running and self._start_time is not None:
            self._status["elapsed_seconds"] = time() - self._start_time
            self._draw_profile_card()
        self.after(1000, self._tick_elapsed)

    # ---- shutdown -----------------------------------------------------------

    def _on_close(self):
        if self._engine is not None:
            self._engine.stop()
            self._engine.disconnect_discord()
        if self._engine_thread is not None:
            self._engine_thread.join(timeout=3)
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
