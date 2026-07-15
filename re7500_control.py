#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jovy RE-7500 Control Room — Linux
=================================
Kontrola SMD rework stanice Jovy RE-7500 preko FTDI D2XX (pyftdi/libusb).
Protokol reverse-engineerovan iz originalnog .NET softvera.

Zavisnosti: pyftdi, pyusb, matplotlib, tkinter
Pokretanje:  sudo python3 re7500_control.py   (sudo zbog libusb pristupa)

Protokol
--------
Konekcija:  115200 8N1, RTS/CTS flow control, latency 1
Komanda:    AA | checksum | 01 | cmd     (cmd = 0xF0 + key, checksum = (1+cmd)&0xFF)
Status:     AA 02 01 01  ->  10B odgovor (header 0x55):
              [4]|[5]<<8 = temperatura °C
              [6] = LHStatus (donji grejac)   1=OFF 2=Preheat 3=Reflow 4=FastReflow
              [7] = UHStatus (gornji grejac)  1=OFF 2=Reflow 3=FastReflow
              [8] = MachineMode               1=NORMAL 2=PARK
              [9] = FanMode                   0=OFF 1=ON
Tasteri (GetKey): UH-Up=1 UH-Down=2 LH-Up=3 LH-Down=4 Fan=6 Buzzer=7
"""
import os
import sys
import json
import time
import socket
import queue
import threading
import webbrowser
from collections import deque

SOCK_PATH = "/tmp/re7500.sock"

# ── Protokol konstante ────────────────────────────────────────────────────────
VID, PID = 0x0403, 0x6001
KEY_UH_UP, KEY_UH_DOWN = 1, 2
KEY_LH_UP, KEY_LH_DOWN = 3, 4
KEY_FAN, KEY_BUZZER    = 6, 7

# Nivoi grejaca (vrednost == status bajt iz stanice)
UH_MODES = {1: "OFF", 2: "Reflow", 3: "Fast Reflow"}
LH_MODES = {1: "OFF", 2: "Preheat", 3: "Reflow", 4: "Fast Reflow"}
UH_NAME2LVL = {v: k for k, v in UH_MODES.items()}
LH_NAME2LVL = {v: k for k, v in LH_MODES.items()}

MODE_NORMAL, MODE_PARK = 1, 2

# ── Profili ────────────────────────────────────────────────────────────────
PROFILES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles.json")

# Kombinovani nivoi snage (lh_lvl, uh_lvl) rastuce — koristi se za bang-bang
# regulaciju temperature preko diskretnih nivoa grejaca (stanica nema
# kontinualni setpoint, samo Up/Down komande po nivoima).
POWER_LEVELS = [
    (1, 1),  # LH OFF          UH OFF
    (2, 1),  # LH Preheat      UH OFF
    (3, 1),  # LH Reflow       UH OFF
    (3, 2),  # LH Reflow       UH Reflow
    (4, 2),  # LH Fast Reflow  UH Reflow
    (4, 3),  # LH Fast Reflow  UH Fast Reflow  (max)
]
REACH_TOL   = 2     # °C — smatra se da je korak dostignut
HOLD_TOL    = 4     # °C — dozvoljeno odstupanje tokom drzanja pre korekcije
LEVEL_CHANGE_MIN_INTERVAL = 4.0   # sekunde izmedju automatskih promena nivoa
COOLDOWN_SEC = 30    # trajanje "preheating" faze posle profila/prekida
GRAPH_WINDOW_SEC = 60  # klizeci prozor grafika temperature

# ── Prevodi (sr/en) ──────────────────────────────────────────────────────────
TEXTS = {
    "sr": {
        "connected":        "● POVEZANO",
        "temp_title":       "TEMPERATURA",
        "status_title":     "STATUS",
        "row_mode":         "Mod stanice",
        "row_uh":           "Gornji grejač",
        "row_lh":           "Donji grejač",
        "row_fan":          "Ventilator",
        "mode_park":        "PARKING",
        "mode_normal":      "NORMALNI",
        "fan_on":           "UKLJUČEN",
        "fan_off":          "isključen",
        "profile_title":    "PROFIL",
        "profile_none":     "— nije izabran —",
        "profile_btn":      "Profil…",
        "start_btn":        "▶ Pokreni",
        "stop_btn":         "■ Zaustavi",
        "uh_card_title":    "GORNJI GREJAČ",
        "lh_card_title":    "DONJI GREJAČ",
        "fan_btn":          "🌀  VENTILATOR",
        "graph_xlabel":     "vreme (s)",
        "profile_ready":          "{n} koraka — spreman za start",
        "profile_step_heat":      "Korak {i}/{n} — greje na {t}°C",
        "profile_step_hold":      "Korak {i}/{n} — drži {t}°C ({s}s)",
        "profile_interrupted":    "Prekinuto (prebačeno u Parking) — hlađenje {s}s",
        "profile_done_cooldown":  "Profil završen — hlađenje {s}s",
        "profile_cooling":        "Hlađenje… {s}s",
        "profile_wait_park":      "Prebaci prekidač u PARKING mod da se upali ventilator",
        "profile_finished":       "Završeno.",
        "profile_stopped":        "Zaustavljeno ručno.",
        "profile_need_normal_title": "Profil",
        "profile_need_normal_msg":   "Stanica mora biti u NORMALNOM modu da bi profil krenuo.",
        "editor_title":     "Profil reflow",
        "saved_profiles":   "Sačuvani profili",
        "load_btn":         "Učitaj",
        "delete_btn":       "Obriši",
        "steps_title":      "KORACI",
        "step_label":       "Korak {i}",
        "hold_label":       "°C   drži",
        "add_step_btn":     "+ Dodaj korak",
        "profile_name_label": "Naziv profila",
        "save_btn":         "💾 Sačuvaj",
        "use_btn":          "✓ Koristi ovaj profil",
        "close_btn":        "Zatvori",
        "warn_title":       "Profil",
        "warn_need_name":   "Unesi naziv i bar jedan korak.",
        "info_saved":       "Profil '{name}' sačuvan.",
        "warn_need_step":   "Dodaj bar jedan korak.",
        "unsaved_label":    "(nesačuvan)",
    },
    "en": {
        "connected":        "● CONNECTED",
        "temp_title":       "TEMPERATURE",
        "status_title":     "STATUS",
        "row_mode":         "Machine mode",
        "row_uh":           "Upper heater",
        "row_lh":           "Lower heater",
        "row_fan":          "Fan",
        "mode_park":        "PARKING",
        "mode_normal":      "NORMAL",
        "fan_on":           "ON",
        "fan_off":          "off",
        "profile_title":    "PROFILE",
        "profile_none":     "— none selected —",
        "profile_btn":      "Profile…",
        "start_btn":        "▶ Start",
        "stop_btn":         "■ Stop",
        "uh_card_title":    "UPPER HEATER",
        "lh_card_title":    "LOWER HEATER",
        "fan_btn":          "🌀  FAN",
        "graph_xlabel":     "time (s)",
        "profile_ready":          "{n} steps — ready to start",
        "profile_step_heat":      "Step {i}/{n} — heating to {t}°C",
        "profile_step_hold":      "Step {i}/{n} — holding {t}°C ({s}s)",
        "profile_interrupted":    "Interrupted (switched to Parking) — cooling {s}s",
        "profile_done_cooldown":  "Profile finished — cooling {s}s",
        "profile_cooling":        "Cooling… {s}s",
        "profile_wait_park":      "Switch to PARKING mode to turn on the fan",
        "profile_finished":       "Done.",
        "profile_stopped":        "Stopped manually.",
        "profile_need_normal_title": "Profile",
        "profile_need_normal_msg":   "The station must be in NORMAL mode for the profile to start.",
        "editor_title":     "Reflow profile",
        "saved_profiles":   "Saved profiles",
        "load_btn":         "Load",
        "delete_btn":       "Delete",
        "steps_title":      "STEPS",
        "step_label":       "Step {i}",
        "hold_label":       "°C   hold",
        "add_step_btn":     "+ Add step",
        "profile_name_label": "Profile name",
        "save_btn":         "💾 Save",
        "use_btn":          "✓ Use this profile",
        "close_btn":        "Close",
        "warn_title":       "Profile",
        "warn_need_name":   "Enter a name and at least one step.",
        "info_saved":       "Profile '{name}' saved.",
        "warn_need_step":   "Add at least one step.",
        "unsaved_label":    "(unsaved)",
    },
}


def load_profiles():
    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_profiles(profiles):
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
#  FTDI sloj (radi kao ROOT daemon — libusb zahteva privilegije za USB reset)
# ══════════════════════════════════════════════════════════════════════════════
class RE7500:
    def __init__(self):
        from pyftdi.ftdi import Ftdi
        self.ftdi = Ftdi()
        self.ftdi.open(vendor=VID, product=PID)
        self.ftdi.set_baudrate(115200)
        self.ftdi.set_line_property(8, 1, 'N')
        self.ftdi.set_flowctrl('hw')          # RTS/CTS — stanica to zahteva
        self.ftdi.set_latency_timer(1)
        self.ftdi.purge_buffers()
        time.sleep(0.1)

    def _send(self, pkt):
        for b in pkt:
            self.ftdi.write_data(bytes([b]))

    def send_key(self, key):
        cmd = (0xF0 + key) & 0xFF
        chk = (0x01 + cmd) & 0xFF
        self.ftdi.purge_buffers()
        self._send(bytes([0xAA, chk, 0x01, cmd]))
        time.sleep(0.2)
        self.ftdi.purge_buffers()             # očisti ACK potpuno (spreči desinhronizaciju)

    def read_status(self, timeout=1.5):
        self.ftdi.purge_buffers()
        self._send(bytes([0xAA, 0x02, 0x01, 0x01]))
        buf = bytearray(); t0 = time.time()
        while len(buf) < 10 and time.time() - t0 < timeout:
            c = self.ftdi.read_data(10 - len(buf))
            if c: buf += c
            else: time.sleep(0.01)
        if len(buf) >= 10 and buf[0] == 0x55:
            return {
                "temp": buf[4] | (buf[5] << 8),
                "lh":   buf[6], "uh": buf[7],
                "mode": buf[8], "fan": buf[9],
            }
        return None

    def close(self):
        try: self.ftdi.close()
        except Exception: pass


def run_daemon():
    """ROOT proces: drži FTDI, izlaže ga preko unix socket-a za GUI (port korisnik)."""
    try:
        dev = RE7500()
    except Exception as e:
        sys.stderr.write(f"FTDI greška: {e}\n"); sys.exit(1)

    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chmod(SOCK_PATH, 0o666)               # da GUI (port) može da se poveže
    srv.listen(1)
    sys.stderr.write("daemon spreman\n"); sys.stderr.flush()

    lock = threading.Lock()
    while True:
        conn, _ = srv.accept()
        f = conn.makefile("rwb", buffering=0)
        try:
            for line in f:
                try:
                    req = json.loads(line)
                except Exception:
                    continue
                with lock:
                    if req.get("cmd") == "status":
                        resp = dev.read_status() or {}
                    elif req.get("cmd") == "key":
                        dev.send_key(int(req["key"])); resp = {"ok": True}
                    else:
                        resp = {"err": "unknown"}
                f.write((json.dumps(resp) + "\n").encode())
        except Exception:
            pass
        finally:
            conn.close()


class RE7500Client:
    """GUI strana: priča sa root daemon-om preko socket-a. Isti API kao RE7500."""
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(SOCK_PATH)
        self.f = self.sock.makefile("rwb", buffering=0)
        self.lock = threading.Lock()

    def _rpc(self, req):
        with self.lock:
            self.f.write((json.dumps(req) + "\n").encode())
            line = self.f.readline()
        return json.loads(line) if line else {}

    def read_status(self):
        r = self._rpc({"cmd": "status"})
        return r if r.get("temp") is not None else None

    def send_key(self, key):
        self._rpc({"cmd": "key", "key": key})

    def close(self):
        try: self.sock.close()
        except Exception: pass


# ── Worker thread: jedini koji dira FTDI ──────────────────────────────────────
class Worker(threading.Thread):
    def __init__(self, dev, cmd_q, status_q):
        super().__init__(daemon=True)
        self.dev = dev
        self.cmd_q = cmd_q
        self.status_q = status_q
        self.running = True

    def _set_heater(self, which, target):
        """Salje Up/Down komande dok status ne dostigne ciljni nivo."""
        up, down = (KEY_UH_UP, KEY_UH_DOWN) if which == "uh" else (KEY_LH_UP, KEY_LH_DOWN)
        for _ in range(6):
            st = self.dev.read_status()
            if not st: break
            cur = st[which]
            if cur == target:
                break
            self.dev.send_key(up if cur < target else down)
            time.sleep(0.2)

    def run(self):
        while self.running:
            # Obradi sve komande na cekanju
            try:
                while True:
                    cmd = self.cmd_q.get_nowait()
                    action = cmd[0]
                    if action == "uh":
                        self._set_heater("uh", cmd[1])
                    elif action == "lh":
                        self._set_heater("lh", cmd[1])
                    elif action == "fan":
                        self.dev.send_key(KEY_FAN)
                    elif action == "buzzer":
                        self.dev.send_key(KEY_BUZZER)
                    elif action == "stop":
                        self.running = False
            except queue.Empty:
                pass
            # Periodicno citaj status
            st = self.dev.read_status()
            if st:
                self.status_q.put(st)
            time.sleep(0.5)


# ── Editor profila (poseban prozor) ─────────────────────────────────────────
class ProfileEditor:
    BG = "#0f1117"; CARD = "#1a1d27"; FG = "#e2e4ed"; MUT = "#5a6072"; ACC = "#00e5ff"

    def __init__(self, parent, app):
        self.app = app
        self.tr = app.tr
        self.steps = [dict(s) for s in app.current_profile_steps] or [
            {"temp": 150, "hold": 30}
        ]
        self.win = tk.Toplevel(parent)
        self.win.title(self.tr("editor_title"))
        self.win.configure(bg=self.BG)
        self.win.geometry("440x560")
        self.win.transient(parent)
        self.win.grab_set()
        self._build()

    def _build(self):
        tr = self.tr
        w = self.win
        top = tk.Frame(w, bg=self.BG); top.pack(fill="x", padx=16, pady=(16, 8))
        tk.Label(top, text=tr("saved_profiles"), bg=self.BG, fg=self.MUT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        row = tk.Frame(top, bg=self.BG); row.pack(fill="x", pady=(4, 0))
        self.saved_var = tk.StringVar()
        names = sorted(load_profiles().keys())
        self.saved_combo = ttk.Combobox(row, textvariable=self.saved_var,
                                        values=names, state="readonly")
        self.saved_combo.pack(side="left", fill="x", expand=True)
        tk.Button(row, text=tr("load_btn"), command=self._load_selected,
                  bg="#252a38", fg=self.FG, relief="flat", bd=0,
                  padx=10).pack(side="left", padx=(6, 0))
        tk.Button(row, text=tr("delete_btn"), command=self._delete_selected,
                  bg="#252a38", fg="#ef4444", relief="flat", bd=0,
                  padx=10).pack(side="left", padx=(6, 0))

        tk.Label(w, text=tr("steps_title"), bg=self.BG, fg=self.MUT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(14, 4))

        self.steps_frame = tk.Frame(w, bg=self.BG)
        self.steps_frame.pack(fill="x", padx=16)
        self.row_widgets = []
        self._render_steps()

        tk.Button(w, text=tr("add_step_btn"), command=self._add_step,
                  bg="#252a38", fg=self.ACC, relief="flat", bd=0,
                  padx=10, pady=6).pack(anchor="w", padx=16, pady=(8, 14))

        save_row = tk.Frame(w, bg=self.BG); save_row.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(save_row, text=tr("profile_name_label"), bg=self.BG, fg=self.MUT,
                 font=("Segoe UI", 9)).pack(anchor="w")
        name_row = tk.Frame(save_row, bg=self.BG); name_row.pack(fill="x", pady=(4, 0))
        self.name_var = tk.StringVar(value=self.app.current_profile_name or "")
        tk.Entry(name_row, textvariable=self.name_var, bg="#12151d", fg=self.FG,
                 insertbackground=self.FG, relief="flat").pack(side="left", fill="x",
                                                               expand=True, ipady=5)
        tk.Button(name_row, text=tr("save_btn"), command=self._save,
                  bg="#252a38", fg=self.FG, relief="flat", bd=0,
                  padx=10).pack(side="left", padx=(6, 0))

        btn_row = tk.Frame(w, bg=self.BG); btn_row.pack(fill="x", padx=16, pady=(6, 16))
        tk.Button(btn_row, text=tr("use_btn"), command=self._use,
                  bg=self.ACC, fg="#000", relief="flat", bd=0,
                  font=("Segoe UI", 10, "bold"), padx=12, pady=8).pack(side="left")
        tk.Button(btn_row, text=tr("close_btn"), command=self.win.destroy,
                  bg="#252a38", fg=self.FG, relief="flat", bd=0,
                  padx=12, pady=8).pack(side="right")

    def _render_steps(self):
        for r in self.row_widgets:
            for wdg in r["widgets"]:
                wdg.destroy()
        self.row_widgets = []
        for i, step in enumerate(self.steps):
            row = tk.Frame(self.steps_frame, bg=self.BG)
            row.pack(fill="x", pady=3)
            lbl = tk.Label(row, text=self.tr("step_label", i=i+1), bg=self.BG, fg=self.FG,
                          font=("Segoe UI", 9, "bold"), width=8, anchor="w")
            lbl.pack(side="left")
            temp_var = tk.StringVar(value=str(step["temp"]))
            temp_e = tk.Entry(row, textvariable=temp_var, width=6, bg="#12151d",
                              fg=self.FG, insertbackground=self.FG, relief="flat")
            temp_e.pack(side="left", ipady=4, padx=(4, 2))
            tk.Label(row, text=self.tr("hold_label"), bg=self.BG, fg=self.MUT,
                    font=("Segoe UI", 9)).pack(side="left")
            hold_var = tk.StringVar(value=str(step["hold"]))
            hold_e = tk.Entry(row, textvariable=hold_var, width=6, bg="#12151d",
                              fg=self.FG, insertbackground=self.FG, relief="flat")
            hold_e.pack(side="left", ipady=4, padx=(4, 2))
            tk.Label(row, text="s", bg=self.BG, fg=self.MUT,
                    font=("Segoe UI", 9)).pack(side="left")
            rm = tk.Button(row, text="✕", command=lambda idx=i: self._remove_step(idx),
                          bg=self.BG, fg="#ef4444", relief="flat", bd=0, padx=8)
            rm.pack(side="right")
            self.row_widgets.append({
                "widgets": [row], "temp_var": temp_var, "hold_var": hold_var,
            })

    def _sync_from_widgets(self):
        steps = []
        for r in self.row_widgets:
            try:
                temp = int(float(r["temp_var"].get()))
                hold = int(float(r["hold_var"].get()))
            except ValueError:
                continue
            steps.append({"temp": temp, "hold": hold})
        self.steps = steps

    def _add_step(self):
        self._sync_from_widgets()
        last = self.steps[-1] if self.steps else {"temp": 100, "hold": 10}
        self.steps.append({"temp": last["temp"], "hold": 10})
        self._render_steps()

    def _remove_step(self, idx):
        self._sync_from_widgets()
        if len(self.steps) <= 1:
            return
        del self.steps[idx]
        self._render_steps()

    def _load_selected(self):
        name = self.saved_var.get()
        if not name:
            return
        profiles = load_profiles()
        self.steps = [dict(s) for s in profiles.get(name, [])] or self.steps
        self.name_var.set(name)
        self._render_steps()

    def _delete_selected(self):
        name = self.saved_var.get()
        if not name:
            return
        profiles = load_profiles()
        profiles.pop(name, None)
        save_profiles(profiles)
        self.saved_combo["values"] = sorted(profiles.keys())
        self.saved_var.set("")

    def _save(self):
        self._sync_from_widgets()
        name = self.name_var.get().strip()
        if not name or not self.steps:
            messagebox.showwarning(self.tr("warn_title"), self.tr("warn_need_name"))
            return
        profiles = load_profiles()
        profiles[name] = self.steps
        save_profiles(profiles)
        self.saved_combo["values"] = sorted(profiles.keys())
        messagebox.showinfo(self.tr("warn_title"), self.tr("info_saved", name=name))

    def _use(self):
        self._sync_from_widgets()
        if not self.steps:
            messagebox.showwarning(self.tr("warn_title"), self.tr("warn_need_step"))
            return
        self.app.apply_profile(
            self.name_var.get().strip() or self.tr("unsaved_label"), self.steps)
        self.win.destroy()


# ── GUI ───────────────────────────────────────────────────────────────────────
class App:
    BG   = "#0f1117"; CARD = "#1a1d27"; FG = "#e2e4ed"; MUT = "#5a6072"
    ACC  = "#00e5ff"; OK = "#22c55e"; WARN = "#f59e0b"; ERR = "#ef4444"; HOT = "#ff6b3d"

    def __init__(self, root, dev):
        self.root = root
        self.dev  = dev
        self.cmd_q = queue.Queue()
        self.status_q = queue.Queue()
        self.worker = Worker(dev, self.cmd_q, self.status_q)

        self.t0 = time.time()
        self.times = deque(maxlen=600)
        self.temps = deque(maxlen=600)
        self.graphing = False
        self.fan_off_sent = False        # da auto-gašenje ventilatora pošalje samo jednom
        self.last = {"temp": 0, "lh": 1, "uh": 1, "mode": MODE_PARK, "fan": 0}

        # ── Profil (reflow) stanje ──
        self.current_profile_name  = None
        self.current_profile_steps = []
        self.profile_running   = False
        self.profile_phase     = None   # 'ramp' | 'hold' | 'cooldown' | 'wait_park'
        self.profile_step_idx  = 0
        self.profile_hold_t0   = 0.0
        self.profile_cooldown_t0 = 0.0
        self.profile_power_lvl = 0
        self.profile_last_lvl_change = 0.0
        self.profile_park_warned = False

        self.lang = "sr"
        self._i18n_widgets = []   # [(widget, key), ...] — staticki tekst za jezicki toggle

        root.title("Jovy RE-7500 Control Room — Linux")
        root.configure(bg=self.BG)
        root.geometry("980x680")
        try:
            root.state("zoomed")           # vecina Linux WM-ova
        except tk.TclError:
            root.attributes("-zoomed", True)  # fallback (npr. neki X11 WM-ovi)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build()
        self.worker.start()
        self.root.after(200, self._poll)

    # ---- Prevodi ----
    def tr(self, key, **kw):
        text = TEXTS[self.lang][key]
        return text.format(**kw) if kw else text

    def _reg_i18n(self, widget, key):
        self._i18n_widgets.append((widget, key))
        return widget

    def toggle_lang(self):
        self.lang = "en" if self.lang == "sr" else "sr"
        self.lang_btn.config(text="SR" if self.lang == "en" else "EN")
        for widget, key in self._i18n_widgets:
            widget.config(text=self.tr(key))
        # profile_name_lbl nosi ime profila (nije za prevod) sem ako nista nije izabrano
        if not self.current_profile_name:
            self.profile_name_lbl.config(text=self.tr("profile_none"))
        self.ax.set_xlabel(self.tr("graph_xlabel"), color=self.MUT, fontsize=8)
        self.canvas.draw_idle()
        self._update(self.last)   # osvezi i dinamicki tekst (status/profil) odmah

    # ---- UI izgradnja ----
    def _card(self, parent, **kw):
        return tk.Frame(parent, bg=self.CARD, highlightbackground="#2a2e3c",
                        highlightthickness=1, **kw)

    def _build(self):
        tr = self.tr
        top = tk.Frame(self.root, bg=self.BG); top.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(top, text="🔥 RE-7500", bg=self.BG, fg=self.FG,
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        self.lang_btn = tk.Button(top, text="EN", command=self.toggle_lang,
                                  bg="#252a38", fg=self.ACC, relief="flat", bd=0,
                                  font=("Segoe UI", 9, "bold"), padx=10, pady=3)
        self.lang_btn.pack(side="right", padx=(0, 10))
        self.conn_lbl = self._reg_i18n(
            tk.Label(top, text=tr("connected"), bg=self.BG, fg=self.OK,
                     font=("Segoe UI", 10, "bold")), "connected")
        self.conn_lbl.pack(side="right")

        footer = tk.Frame(self.root, bg=self.BG); footer.pack(side="bottom", fill="x", pady=(2, 8))
        link = tk.Label(footer, text="www.servisport.rs", bg=self.BG, fg=self.ACC,
                        font=("Segoe UI", 9, "underline"), cursor="hand2")
        link.pack()
        link.bind("<Button-1>", lambda e: webbrowser.open("https://www.servisport.rs"))

        body = tk.Frame(self.root, bg=self.BG); body.pack(fill="both", expand=True, padx=14, pady=6)

        # Leva kolona — temperatura + grafik
        left = tk.Frame(body, bg=self.BG); left.pack(side="left", fill="both", expand=True)

        tempcard = self._card(left); tempcard.pack(fill="x", pady=(0, 8))
        self._reg_i18n(tk.Label(tempcard, text=tr("temp_title"), bg=self.CARD, fg=self.MUT,
                 font=("Segoe UI", 9, "bold")), "temp_title").pack(anchor="w", padx=16, pady=(10, 0))
        self.temp_lbl = tk.Label(tempcard, text="--°C", bg=self.CARD, fg=self.ACC,
                                 font=("Consolas", 46, "bold"))
        self.temp_lbl.pack(anchor="w", padx=14, pady=(0, 10))

        graphcard = self._card(left); graphcard.pack(fill="both", expand=True)
        self.fig = Figure(figsize=(5, 3), dpi=90, facecolor=self.CARD)
        self.ax = self.fig.add_subplot(111, facecolor="#12151d")
        self._style_axes()
        self.line, = self.ax.plot([], [], color=self.HOT, linewidth=2)
        self.canvas = FigureCanvasTkAgg(self.fig, master=graphcard)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)

        # Desna kolona — status + kontrole
        right = tk.Frame(body, bg=self.BG, width=320); right.pack(side="right", fill="y", padx=(10, 0))
        right.pack_propagate(False)

        stat = self._card(right); stat.pack(fill="x", pady=(0, 8))
        self._reg_i18n(tk.Label(stat, text=tr("status_title"), bg=self.CARD, fg=self.MUT,
                 font=("Segoe UI", 9, "bold")), "status_title").pack(anchor="w", padx=16, pady=(10, 4))
        self.st_mode = self._status_row(stat, "row_mode")
        self.st_uh   = self._status_row(stat, "row_uh")
        self.st_lh   = self._status_row(stat, "row_lh")
        self.st_fan  = self._status_row(stat, "row_fan")
        tk.Frame(stat, bg=self.CARD, height=8).pack()

        # Profil (reflow)
        prof_card = self._card(right); prof_card.pack(fill="x", pady=(0, 8))
        self._reg_i18n(tk.Label(prof_card, text=tr("profile_title"), bg=self.CARD, fg=self.MUT,
                 font=("Segoe UI", 9, "bold")), "profile_title").pack(anchor="w", padx=16, pady=(10, 4))
        self.profile_name_lbl = tk.Label(
            prof_card, text=tr("profile_none"),
            bg=self.CARD, fg=self.FG, font=("Segoe UI", 11, "bold"))
        self.profile_name_lbl.pack(anchor="w", padx=16)
        self.profile_status_lbl = tk.Label(prof_card, text="", bg=self.CARD, fg=self.MUT,
                                           font=("Segoe UI", 9), wraplength=260, justify="left")
        self.profile_status_lbl.pack(anchor="w", padx=16, pady=(2, 8))
        prof_btns = tk.Frame(prof_card, bg=self.CARD); prof_btns.pack(fill="x", padx=12, pady=(0, 12))
        self._reg_i18n(tk.Button(prof_btns, text=tr("profile_btn"), command=self.open_profile_editor,
                  bg="#252a38", fg=self.FG, relief="flat", bd=0,
                  padx=10, pady=6), "profile_btn").pack(side="left")
        self.profile_start_btn = self._reg_i18n(tk.Button(
            prof_btns, text=tr("start_btn"), command=self.start_profile,
            bg="#252a38", fg=self.OK, relief="flat", bd=0,
            padx=10, pady=6, state="disabled"), "start_btn")
        self.profile_start_btn.pack(side="left", padx=(6, 0))
        self.profile_stop_btn = self._reg_i18n(tk.Button(
            prof_btns, text=tr("stop_btn"), command=self.stop_profile,
            bg="#252a38", fg=self.ERR, relief="flat", bd=0,
            padx=10, pady=6, state="disabled"), "stop_btn")
        self.profile_stop_btn.pack(side="left", padx=(6, 0))

        # Gornji grejač kontrole
        self.uh_card = self._card(right); self.uh_card.pack(fill="x", pady=(0, 8))
        self._reg_i18n(tk.Label(self.uh_card, text=tr("uh_card_title"), bg=self.CARD, fg=self.MUT,
                 font=("Segoe UI", 9, "bold")), "uh_card_title").pack(anchor="w", padx=16, pady=(10, 6))
        self.uh_btns = {}
        for name in ["OFF", "Reflow", "Fast Reflow"]:
            self.uh_btns[name] = self._mode_btn(self.uh_card, name, lambda n=name: self._set_uh(n))

        # Donji grejač kontrole
        self.lh_card = self._card(right); self.lh_card.pack(fill="x", pady=(0, 8))
        self._reg_i18n(tk.Label(self.lh_card, text=tr("lh_card_title"), bg=self.CARD, fg=self.MUT,
                 font=("Segoe UI", 9, "bold")), "lh_card_title").pack(anchor="w", padx=16, pady=(10, 6))
        self.lh_btns = {}
        for name in ["OFF", "Preheat", "Reflow", "Fast Reflow"]:
            self.lh_btns[name] = self._mode_btn(self.lh_card, name, lambda n=name: self._set_lh(n))

        # Ventilator
        fan_card = self._card(right); fan_card.pack(fill="x")
        self.fan_btn = self._reg_i18n(tk.Button(
            fan_card, text=tr("fan_btn"), font=("Segoe UI", 11, "bold"),
            bg="#252a38", fg=self.FG, activebackground=self.ACC,
            relief="flat", bd=0, height=2, command=self._toggle_fan), "fan_btn")
        self.fan_btn.pack(fill="x", padx=12, pady=12)

    def _style_axes(self):
        self.ax.tick_params(colors=self.MUT, labelsize=8)
        for s in self.ax.spines.values():
            s.set_color("#2a2e3c")
        self.ax.grid(True, color="#20242f", linewidth=0.6)
        self.ax.set_xlabel(self.tr("graph_xlabel"), color=self.MUT, fontsize=8)
        self.ax.set_ylabel("°C", color=self.MUT, fontsize=8)
        # minimalne margine — graf popuni skoro celu karticu umesto
        # podrazumevanog matplotlib razmaka oko ose
        self.fig.subplots_adjust(left=0.045, right=0.995, top=0.97, bottom=0.09)

    def _status_row(self, parent, label_key):
        row = tk.Frame(parent, bg=self.CARD); row.pack(fill="x", padx=16, pady=3)
        self._reg_i18n(tk.Label(row, text=self.tr(label_key), bg=self.CARD, fg=self.FG,
                 font=("Segoe UI", 10)), label_key).pack(side="left")
        val = tk.Label(row, text="—", bg=self.CARD, fg=self.MUT,
                       font=("Segoe UI", 10, "bold"))
        val.pack(side="right")
        return val

    def _mode_btn(self, parent, name, cmd):
        b = tk.Button(parent, text=name, font=("Segoe UI", 10, "bold"),
                      bg="#252a38", fg=self.FG, activebackground=self.ACC,
                      relief="flat", bd=0, anchor="w", padx=14, height=1, command=cmd)
        b.pack(fill="x", padx=12, pady=2)
        return b

    # ---- Akcije ----
    def _set_uh(self, name):
        self.cmd_q.put(("uh", UH_NAME2LVL[name]))

    def _set_lh(self, name):
        self.cmd_q.put(("lh", LH_NAME2LVL[name]))

    def _toggle_fan(self):
        self.cmd_q.put(("fan",))

    def _beep(self, n=1):
        for _ in range(n):
            self.cmd_q.put(("buzzer",))

    # ---- Profil (reflow) ----
    def open_profile_editor(self):
        ProfileEditor(self.root, self)

    def apply_profile(self, name, steps):
        self.current_profile_name  = name
        self.current_profile_steps = steps
        self.profile_name_lbl.config(text=name)
        self.profile_status_lbl.config(text=self.tr("profile_ready", n=len(steps)))
        in_normal = self.last.get("mode") == MODE_NORMAL
        self.profile_start_btn.config(state="normal" if in_normal else "disabled")

    def start_profile(self):
        if not self.current_profile_steps or self.profile_running:
            return
        if self.last.get("mode") != MODE_NORMAL:
            messagebox.showwarning(
                self.tr("profile_need_normal_title"), self.tr("profile_need_normal_msg"))
            return
        self.profile_running   = True
        self.profile_phase     = "ramp"
        self.profile_step_idx  = 0
        self.profile_power_lvl = 0
        self.profile_last_lvl_change = 0.0
        self.profile_park_warned = False
        self.profile_start_btn.config(state="disabled")
        self.profile_stop_btn.config(state="normal")

    def stop_profile(self):
        if not self.profile_running:
            return
        self.profile_running = False
        self.profile_phase = None
        self.cmd_q.put(("uh", 1))
        self.cmd_q.put(("lh", 1))
        self.profile_status_lbl.config(text=self.tr("profile_stopped"))
        self.profile_start_btn.config(state="normal")
        self.profile_stop_btn.config(state="disabled")

    def _apply_power_level(self, lvl):
        lvl = max(0, min(len(POWER_LEVELS) - 1, lvl))
        if lvl == self.profile_power_lvl:
            return
        self.profile_power_lvl = lvl
        lh_lvl, uh_lvl = POWER_LEVELS[lvl]
        self.cmd_q.put(("lh", lh_lvl))
        self.cmd_q.put(("uh", uh_lvl))
        self.profile_last_lvl_change = time.time()

    def _profile_tick(self, st, park):
        """Pokrece se iz _update() na svakom ocitavanju dok profil radi."""
        if not self.profile_running:
            return
        temp = st["temp"]
        now = time.time()

        # Neocekivan prelazak u parking mod tokom rampe/drzanja -> prekini korake
        if self.profile_phase in ("ramp", "hold") and park:
            self.cmd_q.put(("uh", 1))
            self.cmd_q.put(("lh", 2))
            self.profile_phase = "cooldown"
            self.profile_cooldown_t0 = now
            self.profile_status_lbl.config(
                text=self.tr("profile_interrupted", s=COOLDOWN_SEC))
            return

        if self.profile_phase in ("ramp", "hold"):
            steps = self.current_profile_steps
            step  = steps[self.profile_step_idx]
            target = step["temp"]
            last_step = self.profile_step_idx == len(steps) - 1

            # Bang-bang regulacija nivoa snage prema ciljnoj temperaturi
            tol = REACH_TOL if self.profile_phase == "ramp" else HOLD_TOL
            if now - self.profile_last_lvl_change >= LEVEL_CHANGE_MIN_INTERVAL:
                if temp < target - tol:
                    self._apply_power_level(self.profile_power_lvl + 1)
                elif temp > target + tol:
                    self._apply_power_level(self.profile_power_lvl - 1)

            if self.profile_phase == "ramp":
                if temp >= target - REACH_TOL:
                    self._beep(3 if last_step else 1)
                    self.profile_phase = "hold"
                    self.profile_hold_t0 = now
                else:
                    self.profile_status_lbl.config(text=self.tr(
                        "profile_step_heat", i=self.profile_step_idx+1,
                        n=len(steps), t=target))
            else:  # hold
                left = step["hold"] - (now - self.profile_hold_t0)
                if left <= 0:
                    if last_step:
                        self.cmd_q.put(("uh", 1))
                        self.cmd_q.put(("lh", 2))
                        self.profile_phase = "cooldown"
                        self.profile_cooldown_t0 = now
                        self.profile_status_lbl.config(
                            text=self.tr("profile_done_cooldown", s=COOLDOWN_SEC))
                    else:
                        self.profile_step_idx += 1
                        self.profile_phase = "ramp"
                else:
                    self.profile_status_lbl.config(text=self.tr(
                        "profile_step_hold", i=self.profile_step_idx+1,
                        n=len(steps), t=target, s=int(left)+1))

        elif self.profile_phase == "cooldown":
            left = COOLDOWN_SEC - (now - self.profile_cooldown_t0)
            if left <= 0:
                if park:
                    self._finish_profile(st)
                else:
                    if not self.profile_park_warned:
                        self._beep(4)
                        self.profile_park_warned = True
                    self.profile_phase = "wait_park"
                    self.profile_status_lbl.config(text=self.tr("profile_wait_park"))
            else:
                self.profile_status_lbl.config(
                    text=self.tr("profile_cooling", s=int(left)+1))

        elif self.profile_phase == "wait_park":
            if park:
                self._finish_profile(st)

    def _finish_profile(self, st):
        """Kraj profila — ugasi donji grejac (izadje iz preheat-a) i upali ventilator."""
        if not st["fan"]:
            self.cmd_q.put(("fan",))
        self.cmd_q.put(("lh", 1))
        self.profile_status_lbl.config(text=self.tr("profile_finished"))
        self.profile_running = False
        self.profile_phase = None
        self.profile_start_btn.config(state="normal")
        self.profile_stop_btn.config(state="disabled")

    # ---- Petlja osvezavanja ----
    def _poll(self):
        try:
            while True:
                st = self.status_q.get_nowait()
                self.last = st
                self._update(st)
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _update(self, st):
        temp = st["temp"]
        # Odbaci nevalidna očitavanja (pomešan buffer posle komande daje 0 ili nerealno)
        if temp <= 0 or temp > 450:
            return
        self.temp_lbl.config(text=f"{temp}°C")

        # Grafik uvek prati temperaturu; kad grejac krene, obelezi pocetak
        heater_on = st["uh"] != 1 or st["lh"] != 1
        if heater_on and not self.graphing:
            self.graphing = True
            self.heat_start = time.time() - self.t0
            self.ax.axvline(self.heat_start, color=self.WARN, linewidth=1,
                            linestyle="--", alpha=0.7)
        self.times.append(time.time() - self.t0)
        self.temps.append(temp)
        self.line.set_data(list(self.times), list(self.temps))
        if len(self.temps) >= 2:
            xmax = max(self.times)
            xmin = max(0, xmax - GRAPH_WINDOW_SEC)
            self.ax.set_xlim(xmin, max(GRAPH_WINDOW_SEC, xmax))
            # y-osa samo iz vidljivog prozora — da se linija ne "zgusnjava"
            # kad prozor sadrzi staru istoriju van vidljivog dela x-ose
            visible = [t for x, t in zip(self.times, self.temps) if x >= xmin]
            tmin, tmax = min(visible), max(visible)
            self.ax.set_ylim(max(0, tmin - 10), tmax + 15)
        self.canvas.draw_idle()

        # Status tekst
        park = st["mode"] == MODE_PARK
        self.st_mode.config(text=self.tr("mode_park") if park else self.tr("mode_normal"),
                            fg=self.WARN if park else self.OK)
        uh_name = UH_MODES.get(st["uh"], "?")
        lh_name = LH_MODES.get(st["lh"], "?")
        self.st_uh.config(text=uh_name, fg=self.HOT if st["uh"] != 1 else self.MUT)
        self.st_lh.config(text=lh_name, fg=self.HOT if st["lh"] != 1 else self.MUT)
        fan_on = st["fan"] == 1
        self.st_fan.config(text=self.tr("fan_on") if fan_on else self.tr("fan_off"),
                          fg=self.OK if fan_on else self.MUT)

        # Istakni aktivni mod dugmeta
        for n, b in self.uh_btns.items():
            b.config(bg=self.HOT if n == uh_name else "#252a38",
                     fg="#000" if n == uh_name else self.FG)
        for n, b in self.lh_btns.items():
            b.config(bg=self.HOT if n == lh_name else "#252a38",
                     fg="#000" if n == lh_name else self.FG)
        self.fan_btn.config(bg=self.ACC if fan_on else "#252a38",
                            fg="#000" if fan_on else self.FG)

        # ── Auto-gašenje ventilatora pri prelasku PARK → NORMAL ──
        # U normalnom modu se greje; ventilator (hlađenje) ne treba, a dugme je
        # onemogućeno pa korisnik ne može ručno. Ugasi ga jednom.
        if not park:
            if fan_on and not self.fan_off_sent:
                self.cmd_q.put(("fan",))
                self.fan_off_sent = True
            elif not fan_on:
                self.fan_off_sent = False
        else:
            self.fan_off_sent = False

        # ── Profil (reflow) — pokreni pre pravila dozvole da state bude svez ──
        self._profile_tick(st, park)

        # Start dugme dozvoljeno samo u NORMAL modu (profil se ne sme pokrenuti
        # dok je prekidac u parking polozaju)
        if self.current_profile_steps and not self.profile_running:
            self.profile_start_btn.config(state="disabled" if park else "normal")

        # ── Pravila dozvole ──
        # PARK: samo donji grejac + ventilator.  NORMAL: oba grejaca, bez ventilatora.
        # Dok profil radi, rucne kontrole grejaca su onemogucene (profil ih vodi).
        for b in self.uh_btns.values():
            b.config(state="disabled" if (park or self.profile_running) else "normal")
        for b in self.lh_btns.values():
            b.config(state="disabled" if self.profile_running else "normal")
        self.fan_btn.config(state="normal" if (park and not self.profile_running) else "disabled")
        # vizuelno zatamni onemogucene grejace — ali ne i aktivni nivo, da se i
        # dalje vidi sta grejac trenutno radi (npr. dok profil vodi kontrolu)
        if park or self.profile_running:
            for n, b in self.uh_btns.items():
                if n != uh_name:
                    b.config(bg="#191c25", fg=self.MUT)
        if self.profile_running:
            for n, b in self.lh_btns.items():
                if n != lh_name:
                    b.config(bg="#191c25", fg=self.MUT)

    def on_close(self):
        # Sigurnosno gasenje grejaca pri izlasku
        try:
            self.cmd_q.put(("uh", 1))
            self.cmd_q.put(("lh", 1))
            time.sleep(0.8)
        except Exception:
            pass
        self.cmd_q.put(("stop",))
        time.sleep(0.3)
        self.dev.close()
        self.root.destroy()


def run_gui():
    # tkinter/matplotlib se uvoze samo u GUI procesu (port korisnik, ima DISPLAY)
    global tk, ttk, messagebox, Figure, FigureCanvasTkAgg
    import tkinter as tk
    from tkinter import ttk, messagebox
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    # Sačekaj daemon socket
    for _ in range(50):
        if os.path.exists(SOCK_PATH):
            break
        time.sleep(0.1)
    try:
        dev = RE7500Client()
    except Exception as e:
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("RE-7500",
            f"Ne mogu da se povežem na FTDI daemon:\n{e}\n\n"
            "Pokreni preko ./pokreni.sh")
        return

    root = tk.Tk()
    App(root, dev)
    root.mainloop()


def main():
    if "--daemon" in sys.argv:
        run_daemon()
    else:
        run_gui()


if __name__ == "__main__":
    main()
