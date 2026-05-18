import sys
import subprocess
import importlib

# ---------------- AUTO INSTALL ----------------
packages = ["pandas","matplotlib","numpy","tkinterdnd2","tkcalendar"]

def install(pkg):
    try:
        importlib.import_module(pkg)
    except:
        subprocess.check_call([sys.executable,"-m","pip","install",pkg])

for p in packages:
    install(p)

# ---------------- IMPORTS ----------------
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from tkinterdnd2 import TkinterDnD, DND_FILES
from tkcalendar import DateEntry
import pandas as pd
import numpy as np
import os
import re
import math
from datetime import datetime
import bisect

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.dates as mdates

# ---------------- DOWNSAMPLE HELPER ----------------
def downsample_indices(n, max_points=5000):
    if n <= max_points:
        return np.arange(n)
    step = max(1, int(n / max_points))
    return np.arange(0, n, step)

# ---------------- EXPRESSION EVALUATOR ----------------
def evaluate_expression(expr, df):
    token_map = {}
    def replace_col(m):
        col = m.group(1)
        token = f"__COL{len(token_map)}__"
        token_map[token] = col
        return token

    expr_clean = re.sub(r"`([^`]+)`", replace_col, expr)

    bare_cols = sorted([c for c in df.columns if re.match(r"^\w+$", c)], key=len, reverse=True)
    for col in bare_cols:
        pattern = r"(?<!\w)" + re.escape(col) + r"(?!\w)"
        if re.search(pattern, expr_clean):
            token = f"__COL{len(token_map)}__"
            token_map[token] = col
            expr_clean = re.sub(pattern, token, expr_clean)

    safe_ns = {
        "abs": np.abs, "sqrt": np.sqrt, "log": np.log, "log10": np.log10,
        "exp": np.exp, "sin": np.sin, "cos": np.cos, "tan": np.tan,
        "pi": math.pi, "e": math.e,
        "min": np.minimum, "max": np.maximum,
        "mean": lambda x: pd.Series(x).mean(),
        "std": lambda x: pd.Series(x).std(),
        "diff": lambda x: pd.Series(x).diff().fillna(0).to_numpy(),
        "rolling_mean": lambda x, w: pd.Series(x).rolling(int(w), min_periods=1).mean().to_numpy(),
        "rolling_std":  lambda x, w: pd.Series(x).rolling(int(w), min_periods=1).std().fillna(0).to_numpy(),
        "cumsum": lambda x: pd.Series(x).cumsum().to_numpy(),
        "np": np,
    }

    for token, col in token_map.items():
        if col not in df.columns:
            raise ValueError(f"Column not found: '{col}'")
        safe_ns[token] = df[col].to_numpy(dtype=float)

    result = eval(expr_clean, {"__builtins__": {}}, safe_ns)

    if np.isscalar(result):
        result = np.full(len(df), float(result))
    return np.array(result, dtype=float)


# ---------------- TREND VIEWER ----------------
class TrendViewer:

    # How many button rows before the panel scrolls horizontally
    _MAX_SIGNAL_ROWS = 8
    # 5 % Y-axis breathing room above and below the data range
    _Y_PAD = 0.05

    def __init__(self, root):
        self.root = root
        self.root.title("Trend Viewer")
        self.root.geometry("1400x1050")

        self.df = None
        self.filtered_df = None
        self.signal_axis_map = {}
        self.signal_side = {}
        self.highlight_markers = []
        self.last_loaded_file = None
        self.derived_signals = {}
        self.all_signal_buttons = {}

        # MA overlay state
        self.ma_overlays = {}
        self._ma_overlays_by_name = {}
        self._ma_colors  = ["#1B8A1B","#0D5FA8","#B06000","#6A0080","#00777A",
                            "#4E342E","#1565C0","#2E7D32"]

        self.msd_overlays = {}
        self._msd_overlays_by_name = {}
        self._msd_colors = ["#D84315","#6A1B9A","#00695C","#AD1457","#283593",
                            "#558B2F","#F57F17","#4E342E"]

        # rubber-band zoom state
        self._rb_active    = False
        self._rb_press_x   = None
        self._rb_press_y   = None
        self._rb_start_x   = None
        self._rb_start_y   = None
        self._rb_start_ax  = None
        self._rb_rect_main = None
        self._rb_rect_roc  = None
        self._rb_MIN_PX    = 5

        self._dragging    = False
        self._last_drag_x = None

        root.bind("<Print>", self.save_screenshot)

        # -------- Drop area --------
        drop = tk.Label(root, text="Drag CSV here", bg="lightgray", height=2)
        drop.pack(fill="x")
        drop.drop_target_register(DND_FILES)
        drop.dnd_bind("<<Drop>>", self.load_csv_dnd)

        # -------- Time controls --------
        f = tk.Frame(root)
        f.pack(pady=4)
        tk.Label(f, text="Start").grid(row=0, column=0)
        self.start_date = DateEntry(f, date_pattern="yyyy-mm-dd")
        self.start_date.grid(row=0, column=1)
        self.start_time = tk.Entry(f, width=8)
        self.start_time.insert(0, "00:00:00")
        self.start_time.grid(row=0, column=2)

        tk.Label(f, text="End").grid(row=0, column=3)
        self.end_date = DateEntry(f, date_pattern="yyyy-mm-dd")
        self.end_date.grid(row=0, column=4)
        self.end_time = tk.Entry(f, width=8)
        self.end_time.insert(0, "23:59:59")
        self.end_time.grid(row=0, column=5)

        tk.Button(f, text="Apply",   command=self.apply_time_filter).grid(row=0, column=6, padx=5)
        tk.Button(f, text="Export",  command=self.export_csv).grid(row=0, column=7, padx=5)
        tk.Button(f, text="Reset X", command=lambda: self.reset_x(absolute=True)).grid(row=0, column=8, padx=5)
        tk.Button(f, text="FFT", command=self.show_fft,
                  bg="#7B1FA2", fg="white", font=("TkDefaultFont", 9, "bold")
                  ).grid(row=0, column=9, padx=5)

        # -------- Signal search + derived signal row --------
        sf = tk.Frame(root)
        sf.pack(fill="x", padx=6, pady=2)

        tk.Label(sf, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_change)
        search_entry = tk.Entry(sf, textvariable=self.search_var, width=22)
        search_entry.pack(side="left", padx=(2, 12))

        tk.Label(sf, text="New Signal  =").pack(side="left")
        self.expr_var = tk.StringVar()
        self.expr_entry = tk.Entry(sf, textvariable=self.expr_var, width=38)
        self.expr_entry.pack(side="left", padx=2)
        self.expr_entry.bind("<Return>",   lambda e: self._add_derived_signal())
        self.expr_entry.bind("<KeyRelease>", self._on_expr_keyrelease)
        self.expr_entry.bind("<Tab>",      self._ac_tab)
        self.expr_entry.bind("<Down>",     self._ac_down)
        self.expr_entry.bind("<Up>",       self._ac_up)
        self.expr_entry.bind("<Escape>",   lambda e: self._ac_hide())
        self.expr_entry.bind("<FocusOut>", lambda e: self.root.after(150, self._ac_hide))

        tk.Label(sf, text=" Name:").pack(side="left")
        self.expr_name_var = tk.StringVar()
        expr_name_entry = tk.Entry(sf, textvariable=self.expr_name_var, width=16)
        expr_name_entry.pack(side="left", padx=2)
        expr_name_entry.bind("<Return>", lambda e: self._add_derived_signal())

        tk.Button(sf, text="Add", command=self._add_derived_signal, bg="#2196F3", fg="white").pack(side="left", padx=4)
        tk.Button(sf, text="?", command=self._show_expr_help, width=2).pack(side="left")

        # -------- Moving Average + Moving Std Dev (single combined row) --------
        masd_row = tk.Frame(root, bd=1, relief="solid")
        masd_row.pack(fill="x", padx=4, pady=(2, 0))

        # ── Left half: Moving Average ──
        ma_f = tk.Frame(masd_row, bg="#E8F5E9")
        ma_f.pack(side="left", fill="y", padx=0)

        tk.Label(ma_f, text="Moving Avg:", bg="#E8F5E9",
                 font=("TkDefaultFont", 9, "bold")).pack(side="left", padx=(6, 4))
        tk.Label(ma_f, text="Signal:", bg="#E8F5E9").pack(side="left")

        self.ma_signal_var = tk.StringVar()
        self.ma_signal_var.trace_add("write", self._on_ma_search_change)
        self._ma_all_signals = []

        ma_entry_frame = tk.Frame(ma_f, bg="#E8F5E9")
        ma_entry_frame.pack(side="left", padx=(2, 6))
        self.ma_signal_entry = tk.Entry(ma_entry_frame, textvariable=self.ma_signal_var, width=18)
        self.ma_signal_entry.pack()
        self.ma_signal_entry.bind("<KeyRelease>", self._on_ma_entry_key)
        self.ma_signal_entry.bind("<Down>",       self._ma_dd_down)
        self.ma_signal_entry.bind("<Up>",         self._ma_dd_up)
        self.ma_signal_entry.bind("<Return>",     self._ma_dd_accept)
        self.ma_signal_entry.bind("<Escape>",     lambda e: self._ma_dd_hide())
        self.ma_signal_entry.bind("<FocusOut>",   lambda e: self.root.after(150, self._ma_dd_hide))

        self._ma_dd_win  = None
        self._ma_dd_lb   = None
        self._ma_dd_sel  = -1
        self._ma_dd_items = []

        tk.Label(ma_f, text="Win:", bg="#E8F5E9").pack(side="left")
        self.ma_days_var = tk.IntVar(value=0)
        tk.Spinbox(ma_f, from_=0, to=3650, width=4,
                   textvariable=self.ma_days_var,
                   command=self._update_ma_window_label).pack(side="left", padx=(2, 0))
        tk.Label(ma_f, text="d", bg="#E8F5E9", font=("TkDefaultFont", 8)).pack(side="left", padx=(1, 4))

        self.ma_hours_var = tk.IntVar(value=1)
        tk.Spinbox(ma_f, from_=0, to=23, width=3,
                   textvariable=self.ma_hours_var,
                   command=self._update_ma_window_label).pack(side="left", padx=(2, 0))
        tk.Label(ma_f, text="h", bg="#E8F5E9", font=("TkDefaultFont", 8)).pack(side="left", padx=(1, 4))

        self.ma_mins_var = tk.IntVar(value=0)
        tk.Spinbox(ma_f, from_=0, to=59, width=3,
                   textvariable=self.ma_mins_var,
                   command=self._update_ma_window_label).pack(side="left", padx=(2, 0))
        tk.Label(ma_f, text="m", bg="#E8F5E9", font=("TkDefaultFont", 8)).pack(side="left", padx=(1, 4))

        self.ma_window_summary = tk.Label(ma_f, text="= 1h",
                                           bg="#C8E6C9", fg="#1B5E20",
                                           font=("TkDefaultFont", 8, "bold"),
                                           padx=4, relief="groove")
        self.ma_window_summary.pack(side="left", padx=(0, 4))

        self.ma_days_var.trace_add("write",  lambda *_: self._update_ma_window_label())
        self.ma_hours_var.trace_add("write", lambda *_: self._update_ma_window_label())
        self.ma_mins_var.trace_add("write",  lambda *_: self._update_ma_window_label())

        tk.Button(ma_f, text="Add MA", command=self._add_ma,
                  bg="#2E7D32", fg="white",
                  font=("TkDefaultFont", 9, "bold")).pack(side="left", padx=(2, 6))

        # ── Vertical divider ──
        tk.Frame(masd_row, width=2, bg="#BDBDBD").pack(side="left", fill="y", padx=2)

        # ── Right half: Moving Std Dev ──
        msd_f = tk.Frame(masd_row, bg="#FFF3E0")
        msd_f.pack(side="left", fill="y", padx=0)

        tk.Label(msd_f, text="Moving Std:", bg="#FFF3E0",
                 font=("TkDefaultFont", 9, "bold")).pack(side="left", padx=(6, 4))
        tk.Label(msd_f, text="Signal:", bg="#FFF3E0").pack(side="left")

        self.msd_signal_var = tk.StringVar()
        self.msd_signal_var.trace_add("write", self._on_msd_search_change)

        msd_entry_frame = tk.Frame(msd_f, bg="#FFF3E0")
        msd_entry_frame.pack(side="left", padx=(2, 6))
        self.msd_signal_entry = tk.Entry(msd_entry_frame, textvariable=self.msd_signal_var, width=18)
        self.msd_signal_entry.pack()
        self.msd_signal_entry.bind("<KeyRelease>", self._on_msd_entry_key)
        self.msd_signal_entry.bind("<Down>",       self._msd_dd_down)
        self.msd_signal_entry.bind("<Up>",         self._msd_dd_up)
        self.msd_signal_entry.bind("<Return>",     self._msd_dd_accept)
        self.msd_signal_entry.bind("<Escape>",     lambda e: self._msd_dd_hide())
        self.msd_signal_entry.bind("<FocusOut>",   lambda e: self.root.after(150, self._msd_dd_hide))

        self._msd_dd_win  = None
        self._msd_dd_lb   = None
        self._msd_dd_sel  = -1
        self._msd_dd_items = []

        tk.Label(msd_f, text="Win:", bg="#FFF3E0").pack(side="left")
        self.msd_days_var = tk.IntVar(value=0)
        tk.Spinbox(msd_f, from_=0, to=3650, width=4,
                   textvariable=self.msd_days_var,
                   command=self._update_msd_window_label).pack(side="left", padx=(2, 0))
        tk.Label(msd_f, text="d", bg="#FFF3E0", font=("TkDefaultFont", 8)).pack(side="left", padx=(1, 4))

        self.msd_hours_var = tk.IntVar(value=1)
        tk.Spinbox(msd_f, from_=0, to=23, width=3,
                   textvariable=self.msd_hours_var,
                   command=self._update_msd_window_label).pack(side="left", padx=(2, 0))
        tk.Label(msd_f, text="h", bg="#FFF3E0", font=("TkDefaultFont", 8)).pack(side="left", padx=(1, 4))

        self.msd_mins_var = tk.IntVar(value=0)
        tk.Spinbox(msd_f, from_=0, to=59, width=3,
                   textvariable=self.msd_mins_var,
                   command=self._update_msd_window_label).pack(side="left", padx=(2, 0))
        tk.Label(msd_f, text="m", bg="#FFF3E0", font=("TkDefaultFont", 8)).pack(side="left", padx=(1, 4))

        self.msd_window_summary = tk.Label(msd_f, text="= 1h",
                                            bg="#FFE0B2", fg="#E65100",
                                            font=("TkDefaultFont", 8, "bold"),
                                            padx=4, relief="groove")
        self.msd_window_summary.pack(side="left", padx=(0, 4))

        self.msd_days_var.trace_add("write",  lambda *_: self._update_msd_window_label())
        self.msd_hours_var.trace_add("write", lambda *_: self._update_msd_window_label())
        self.msd_mins_var.trace_add("write",  lambda *_: self._update_msd_window_label())

        tk.Button(msd_f, text="Add MSD", command=self._add_msd,
                  bg="#E65100", fg="white",
                  font=("TkDefaultFont", 9, "bold")).pack(side="left", padx=(2, 6))

        # -------- Axis key legend --------
        legend_f = tk.Frame(root, bg="#F5F5F5", bd=1, relief="solid")
        legend_f.pack(fill="x", padx=4, pady=(0, 2))
        tk.Label(legend_f, text="Axis key:", bg="#F5F5F5", font=("TkDefaultFont", 8)).pack(side="left", padx=4)
        tk.Label(legend_f, text="[L]", bg="#1565C0", fg="white",
                 font=("TkDefaultFont", 8, "bold"), padx=4).pack(side="left", padx=2)
        tk.Label(legend_f, text="= Left / Primary axis", bg="#F5F5F5", font=("TkDefaultFont", 8)).pack(side="left")
        tk.Label(legend_f, text="[R]", bg="#BF360C", fg="white",
                 font=("TkDefaultFont", 8, "bold"), padx=4).pack(side="left", padx=(12,2))
        tk.Label(legend_f, text="= Right / Secondary axis  (click [L]/[R] on any signal to toggle)", bg="#F5F5F5",
                 font=("TkDefaultFont", 8)).pack(side="left")

        # -------- Autocomplete popup --------
        self._ac_win    = None
        self._ac_lb     = None
        self._ac_items  = []
        self._ac_sel    = -1
        self._ac_token  = ""

        # ================================================================
        # Signal buttons panel — dynamic height (grows to max 8 rows), horizontal scroll
        # ================================================================
        self._ROW_HEIGHT = 30   # approximate px per button row; used for height clamping

        self.signal_outer = tk.Frame(root, bd=1, relief="solid")
        self.signal_outer.pack(fill="x", pady=2)

        # Left / right page-scroll arrow buttons
        tk.Button(
            self.signal_outer, text="◀", width=2,
            command=lambda: self._sig_canvas.xview_scroll(-1, "pages"),
            relief="flat", bg="#E0E0E0", activebackground="#BDBDBD"
        ).pack(side="left", fill="y")

        tk.Button(
            self.signal_outer, text="▶", width=2,
            command=lambda: self._sig_canvas.xview_scroll(1, "pages"),
            relief="flat", bg="#E0E0E0", activebackground="#BDBDBD"
        ).pack(side="right", fill="y")

        # Column that holds the canvas + its horizontal scrollbar
        _canvas_col = tk.Frame(self.signal_outer)
        _canvas_col.pack(side="left", fill="both", expand=True)

        self._sig_canvas = tk.Canvas(
            _canvas_col, height=0,
            highlightthickness=0, bg="#FAFAFA"
        )
        self._sig_canvas.pack(fill="both", expand=True)

        _sig_hbar = tk.Scrollbar(
            _canvas_col, orient="horizontal",
            command=self._sig_canvas.xview
        )
        _sig_hbar.pack(fill="x")
        self._sig_canvas.configure(xscrollcommand=_sig_hbar.set)

        # Inner frame — buttons are grid-packed here
        self.signal_frame = tk.Frame(self._sig_canvas, bg="#FAFAFA")
        self._sig_win_id = self._sig_canvas.create_window(
            (0, 0), window=self.signal_frame, anchor="nw"
        )

        self.signal_frame.bind("<Configure>", self._on_sigframe_configure)
        self._sig_canvas.bind("<Configure>",  self._on_sigcanvas_configure)

        # Horizontal mouse-wheel scroll
        for seq, delta in (("<MouseWheel>", None), ("<Button-4>", -1), ("<Button-5>", 1)):
            self._sig_canvas.bind(
                seq,
                lambda e, d=delta: self._sig_canvas.xview_scroll(
                    -1 if (d if d is not None else e.delta) > 0 else 1, "units"
                )
            )

        # -------- Matplotlib figure --------
        self.fig, (self.ax_main, self.ax_roc) = plt.subplots(
            2, 1, sharex=True,
            gridspec_kw={"height_ratios": [3, 1]},
            figsize=(12, 8)
        )
        self.ax_main_r = self.ax_main.twinx()
        self.ax_roc_r  = self.ax_roc.twinx()
        self.ax_main_r.set_ylabel("Secondary axis", color="#BF360C", labelpad=2)
        self.ax_roc_r.set_ylabel("ROC (right)", color="#BF360C", labelpad=2)
        self.ax_main_r.tick_params(axis="y", colors="#BF360C")
        self.ax_roc_r.tick_params(axis="y", colors="#BF360C")
        self.ax_main_r.set_visible(False)
        self.ax_roc_r.set_visible(False)

        self.ax_main.set_title("Signals")
        self.ax_roc.set_title("Rate of Change")

        self.canvas = FigureCanvasTkAgg(self.fig, master=root)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, root)
        self.toolbar.update()

        self.vline_main = self.ax_main.axvline(0, color="gray", linestyle="--", visible=False)
        self.vline_roc  = self.ax_roc.axvline(0, color="gray", linestyle="--", visible=False)

        self.coord_label = tk.Label(root, text="", anchor="w")
        self.coord_label.pack(fill="x")

        self.stats_label = tk.Label(root, text="", anchor="w", bg="#f0f0f0", justify="left")
        self.stats_label.pack(fill="x")

        self.ax_main.format_coord   = lambda x, y: ""
        self.ax_roc.format_coord    = lambda x, y: ""
        self.ax_main_r.format_coord = lambda x, y: ""
        self.ax_roc_r.format_coord  = lambda x, y: ""

        # -------- Events --------
        self.canvas.mpl_connect("motion_notify_event", self.update_cursor)
        self.canvas.mpl_connect("scroll_event", self.zoom)
        self.canvas.mpl_connect("button_press_event", self.start_pan)
        self.canvas.mpl_connect("button_release_event", self.stop_pan)
        self.canvas.mpl_connect("motion_notify_event", self.pan)
        self.canvas.mpl_connect("figure_leave_event", self.on_mouse_leave)

    # ================================================================
    # SIGNAL PANEL — canvas scroll helpers
    # ================================================================
    def _on_sigframe_configure(self, event=None):
        self._sig_canvas.configure(scrollregion=self._sig_canvas.bbox("all"))
        # Grow/shrink the canvas to fit actual content, capped at MAX_SIGNAL_ROWS rows
        max_h = self._MAX_SIGNAL_ROWS * self._ROW_HEIGHT
        content_h = self.signal_frame.winfo_reqheight()
        new_h = max(0, min(content_h, max_h))
        self._sig_canvas.configure(height=new_h)

    def _on_sigcanvas_configure(self, event=None):
        # Keep inner frame height equal to the canvas so buttons don't float
        self._sig_canvas.itemconfig(self._sig_win_id,
                                    height=self._sig_canvas.winfo_height())

    # ================================================================
    # HELPERS — axes
    # ================================================================
    def _axes_for(self, name):
        if self.signal_side.get(name, "left") == "right":
            return self.ax_main_r, self.ax_roc_r
        return self.ax_main, self.ax_roc

    def _update_secondary_visibility(self):
        has_right = any(self.signal_side.get(s) == "right" for s in self.signal_axis_map)
        self.ax_main_r.set_visible(has_right)
        self.ax_roc_r.set_visible(has_right)

    # ================================================================
    # SEARCH
    # ================================================================
    def _on_search_change(self, *_):
        query = self.search_var.get().strip().lower()
        for name, widgets in self.all_signal_buttons.items():
            frame = widgets["frame"]
            if query == "" or query in name.lower():
                frame.grid()
            else:
                frame.grid_remove()
        self._on_sigframe_configure()

    # ================================================================
    # AUTOCOMPLETE
    # ================================================================
    _BUILTINS = [
        "abs(", "sqrt(", "log(", "log10(", "exp(",
        "sin(", "cos(", "tan(",
        "mean(", "std(",
        "diff(", "rolling_mean(", "rolling_std(", "cumsum(",
        "min(", "max(",
        "pi", "e",
    ]

    def _get_token_at_cursor(self):
        text = self.expr_entry.get()
        pos  = self.expr_entry.index(tk.INSERT)
        start = pos
        for i in range(pos - 1, -1, -1):
            ch = text[i]
            if ch == '`':
                start = i; break
            if ch.isalnum() or ch == '_':
                start = i
            else:
                break
        return text[start:pos], start, pos

    def _suggestions(self, prefix):
        if not prefix: return []
        pl = prefix.lstrip('`').lower()
        results = []
        if self.filtered_df is not None:
            for col in self.filtered_df.columns:
                if col == "Time": continue
                if col.lower().startswith(pl):
                    token = f"`{col}`" if not re.match(r"^\w+$", col) else col
                    results.append(("signal", token, col))
        for b in self._BUILTINS:
            if b.lower().startswith(pl):
                results.append(("builtin", b, b))
        return results

    def _on_expr_keyrelease(self, event):
        if event.keysym in ("Tab","Up","Down","Return","Escape","Left","Right"): return
        prefix, start, pos = self._get_token_at_cursor()
        sug = self._suggestions(prefix)
        if sug:
            self._ac_token = prefix; self._ac_items = sug; self._ac_sel = -1
            self._ac_show(sug)
        else:
            self._ac_hide()

    def _ac_show(self, suggestions):
        x = self.expr_entry.winfo_rootx()
        y = self.expr_entry.winfo_rooty() + self.expr_entry.winfo_height()
        if self._ac_win is None or not self._ac_win.winfo_exists():
            self._ac_win = tk.Toplevel(self.root)
            self._ac_win.wm_overrideredirect(True)
            self._ac_win.wm_attributes("-topmost", True)
            frame = tk.Frame(self._ac_win, bd=1, relief="solid")
            frame.pack(fill="both", expand=True)
            sb = tk.Scrollbar(frame, orient="vertical")
            self._ac_lb = tk.Listbox(frame, yscrollcommand=sb.set, selectmode="single",
                activestyle="dotbox", font=("Courier", 10), bg="#FFFDE7",
                selectbackground="#FFC107", selectforeground="black",
                height=min(8, len(suggestions)), width=36, exportselection=False)
            sb.config(command=self._ac_lb.yview)
            self._ac_lb.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self._ac_lb.bind("<ButtonRelease-1>", self._ac_click)
            self._ac_lb.bind("<Return>", self._ac_accept)
        self._ac_lb.delete(0, "end")
        for kind, token, label in suggestions:
            self._ac_lb.insert("end", f" {'⚡' if kind=='signal' else 'ƒ'}  {label}")
        self._ac_lb.config(height=min(8, len(suggestions)))
        self._ac_win.geometry(f"+{x}+{y}")
        self._ac_win.deiconify()
        self._ac_sel = -1

    def _ac_hide(self):
        if self._ac_win and self._ac_win.winfo_exists(): self._ac_win.withdraw()
        self._ac_sel = -1

    def _ac_down(self, event):
        if not self._ac_items: return "break"
        if self._ac_win and self._ac_win.winfo_exists() and self._ac_win.state() == "normal":
            self._ac_sel = min(self._ac_sel + 1, len(self._ac_items) - 1)
            self._ac_lb.selection_clear(0, "end"); self._ac_lb.selection_set(self._ac_sel)
            self._ac_lb.see(self._ac_sel)
        else:
            self._ac_show(self._ac_items)
        return "break"

    def _ac_up(self, event):
        if not self._ac_items: return "break"
        self._ac_sel = max(self._ac_sel - 1, 0)
        self._ac_lb.selection_clear(0, "end"); self._ac_lb.selection_set(self._ac_sel)
        self._ac_lb.see(self._ac_sel); return "break"

    def _ac_tab(self, event):
        if self._ac_items: self._ac_accept_index(self._ac_sel if self._ac_sel >= 0 else 0)
        return "break"

    def _ac_click(self, event): self._ac_accept_index(self._ac_lb.nearest(event.y))

    def _ac_accept(self, event):
        idx = self._ac_lb.curselection()
        if idx: self._ac_accept_index(idx[0])
        return "break"

    def _ac_accept_index(self, idx):
        if idx < 0 or idx >= len(self._ac_items): return
        _, token, _ = self._ac_items[idx]
        text = self.expr_entry.get(); pos = self.expr_entry.index(tk.INSERT)
        start = pos
        for i in range(pos - 1, -1, -1):
            ch = text[i]
            if ch == '`' or ch.isalnum() or ch == '_': start = i
            else: break
        new_text = text[:start] + token + text[pos:]
        self.expr_entry.delete(0, "end"); self.expr_entry.insert(0, new_text)
        self.expr_entry.icursor(start + len(token))
        self._ac_hide(); self.expr_entry.focus_set()

    # ================================================================
    # DERIVED / ARITHMETIC SIGNALS
    # ================================================================
    def _add_derived_signal(self):
        if self.filtered_df is None:
            messagebox.showwarning("No data", "Load a CSV first."); return
        expr = self.expr_var.get().strip(); name = self.expr_name_var.get().strip()
        if not expr: messagebox.showwarning("Empty expression", "Enter a formula."); return
        if not name: name = f"expr_{len(self.derived_signals)+1}"
        try:
            result = evaluate_expression(expr, self.filtered_df)
        except Exception as ex:
            messagebox.showerror("Expression error", str(ex)); return
        if len(result) != len(self.filtered_df):
            messagebox.showerror("Shape mismatch", f"Result length {len(result)} != {len(self.filtered_df)}"); return
        self.derived_signals[name] = expr
        self.df[name] = np.nan
        self.df.loc[self.filtered_df.index, name] = result
        self.filtered_df = self.filtered_df.copy()
        self.filtered_df[name] = result
        self._add_signal_button(name, derived=True)
        self._refresh_ma_signal_list(); self._refresh_msd_signal_list()
        self.expr_var.set(""); self.expr_name_var.set("")

    def _show_expr_help(self):
        help_text = (
            "Excel-style Arithmetic Signal Builder\n"
            "═══════════════════════════════════════\n\n"
            "Use column names directly in expressions.\n"
            "Wrap names with spaces in backticks: `col name`\n\n"
            "Operators:  + - * / ** ( )\n\n"
            "Math functions:\n"
            "  abs(x)   sqrt(x)   log(x)   log10(x)\n"
            "  exp(x)   sin(x)    cos(x)   tan(x)\n\n"
            "Statistical functions:\n"
            "  mean(x)   std(x)\n\n"
            "Series functions (return array):\n"
            "  diff(x)                — row-by-row difference\n"
            "  rolling_mean(x, N)     — N-point rolling mean\n"
            "  rolling_std(x, N)      — N-point rolling std dev\n"
            "  cumsum(x)              — cumulative sum\n\n"
            "Constants:  pi,  e\n\n"
            "Examples:\n"
            "  SignalA + SignalB\n"
            "  SignalA / SignalB * 100\n"
            "  sqrt(abs(SignalA - SignalB))\n"
            "  rolling_mean(Temperature, 10)\n"
            "  `Oil Temp` - `Water Temp`\n"
            "  (SignalA - mean(SignalA)) / std(SignalA)   <- z-score\n"
        )
        win = tk.Toplevel(self.root); win.title("Expression Help"); win.geometry("480x480")
        txt = tk.Text(win, wrap="word", font=("Courier", 10), padx=10, pady=10)
        txt.insert("1.0", help_text); txt.config(state="disabled"); txt.pack(fill="both", expand=True)

    # ================================================================
    # CSV LOAD
    # ================================================================
    def load_csv_dnd(self, event): self.load_csv(event.data.strip("{}"))

    def load_csv(self, path):
        try:
            self.df = pd.read_csv(path)
            self.df["Time"] = pd.to_datetime(self.df["Time"], utc=False)
            if self.df["Time"].dt.tz is not None:
                self.df["Time"] = (self.df["Time"]
                    .dt.tz_convert(datetime.now().astimezone().tzinfo)
                    .dt.tz_localize(None))
        except Exception as e:
            messagebox.showerror("Error", str(e)); return

        self.last_loaded_file = path
        self.derived_signals.clear(); self.all_signal_buttons.clear()
        self.signal_side.clear()
        self.ma_overlays.clear(); self._ma_overlays_by_name.clear()
        self.msd_overlays.clear(); self._msd_overlays_by_name.clear()

        for w in self.signal_frame.winfo_children(): w.destroy()

        tmin = self.df["Time"].min(); tmax = self.df["Time"].max()
        self.start_date.set_date(tmin.date())
        self.start_time.delete(0, "end"); self.start_time.insert(0, tmin.strftime("%H:%M:%S"))
        self.end_date.set_date(tmax.date())
        self.end_time.delete(0, "end"); self.end_time.insert(0, tmax.strftime("%H:%M:%S"))

        self.apply_time_filter()
        for c in self.df.columns:
            if c != "Time": self._add_signal_button(c)
        self._refresh_ma_signal_list(); self._refresh_msd_signal_list()

    def _add_signal_button(self, name, derived=False):
        """
        Column-major layout: fill _MAX_SIGNAL_ROWS rows before opening a new
        column pair.  Panel scrolls horizontally when columns overflow width.
        """
        max_rows = self._MAX_SIGNAL_ROWS
        idx  = len(self.all_signal_buttons)
        col  = (idx // max_rows) * 2   # 2 grid-columns per signal (label + side-btn)
        row  = idx % max_rows

        if name not in self.signal_side:
            self.signal_side[name] = "left"

        container = tk.Frame(self.signal_frame, bg="#FAFAFA")
        container.grid(row=row, column=col, columnspan=2, padx=2, pady=1, sticky="w")

        btn = tk.Label(container, text=name,
                       bg="#E3F2FD" if derived else "white",
                       relief="raised", padx=6, pady=3)
        btn.pack(side="left")
        btn.bind("<Button-1>", self.toggle_signal)
        if derived:
            btn.bind("<Button-3>", lambda e, n=name: self._remove_derived_signal(n))

        side_btn = tk.Label(container, text="L", bg="#1565C0", fg="white",
                            relief="raised", padx=5, pady=3,
                            font=("TkDefaultFont", 8, "bold"), cursor="hand2")
        side_btn.pack(side="left", padx=(1, 0))
        side_btn.bind("<Button-1>", lambda e, n=name: self._toggle_signal_side(n))

        self.all_signal_buttons[name] = {"frame": container, "btn": btn, "side_btn": side_btn}

        self.signal_frame.update_idletasks()
        self._on_sigframe_configure()

        query = self.search_var.get().strip().lower()
        if query and query not in name.lower():
            container.grid_remove()

    def _toggle_signal_side(self, name):
        new_side = "right" if self.signal_side.get(name, "left") == "left" else "left"
        self.signal_side[name] = new_side
        widgets = self.all_signal_buttons.get(name)
        if widgets:
            widgets["side_btn"].config(
                text="R" if new_side == "right" else "L",
                bg="#BF360C" if new_side == "right" else "#1565C0"
            )
        if name in self.signal_axis_map:
            self._redraw_signals(); self._update_secondary_visibility()
            self.auto_adjust_yaxis(); self.update_stats_label(); self.canvas.draw_idle()

    def _remove_derived_signal(self, name):
        if name in self.signal_axis_map:
            del self.signal_axis_map[name]; self._update_legends(); self.canvas.draw_idle()
        widgets = self.all_signal_buttons.get(name)
        if widgets: widgets["frame"].destroy(); del self.all_signal_buttons[name]
        self.derived_signals.pop(name, None); self.signal_side.pop(name, None)
        if self.filtered_df is not None and name in self.filtered_df.columns:
            self.filtered_df = self.filtered_df.drop(columns=[name])
        if self.df is not None and name in self.df.columns:
            self.df = self.df.drop(columns=[name])
        self._redraw_signals(); self._update_secondary_visibility()
        self._refresh_ma_signal_list(); self._refresh_msd_signal_list()
        self.canvas.draw_idle()

    # ================================================================
    # FILTER
    # ================================================================
    def apply_time_filter(self):
        if self.df is None: return
        start = pd.to_datetime(f"{self.start_date.get()} {self.start_time.get()}")
        end   = pd.to_datetime(f"{self.end_date.get()} {self.end_time.get()}")
        mask  = (self.df["Time"] >= start) & (self.df["Time"] <= end)
        self.filtered_df = self.df.loc[mask].copy()

        for name, expr in self.derived_signals.items():
            try: self.filtered_df[name] = evaluate_expression(expr, self.filtered_df)
            except: pass

        for ma_name, info in self._ma_overlays_by_name.items():
            sig, ws = info["sig"], info["window_secs"]
            if sig not in self.filtered_df.columns: continue
            self.filtered_df[ma_name] = (self.filtered_df[sig]
                .rolling(self._seconds_to_rows(ws), min_periods=1).mean().values)

        for msd_name, info in self._msd_overlays_by_name.items():
            sig, ws = info["sig"], info["window_secs"]
            if sig not in self.filtered_df.columns: continue
            self.filtered_df[msd_name] = (self.filtered_df[sig]
                .rolling(self._seconds_to_rows(ws), min_periods=2).std().fillna(0).values)

        previously_active = set(self.signal_axis_map.keys())
        self.reset_plot()

        for name in previously_active:
            if name not in self.filtered_df.columns: continue
            self.signal_axis_map[name] = None
            widgets = self.all_signal_buttons.get(name)
            if widgets:
                ov = self._ma_overlays_by_name.get(name) or self._msd_overlays_by_name.get(name)
                if ov: widgets["btn"].config(relief="sunken", bg=ov["color"], fg="white")
                else:   widgets["btn"].config(relief="sunken", bg="#4CAF50", fg="white")

        if previously_active:
            self._redraw_signals(); self._update_secondary_visibility()
            self.auto_adjust_yaxis(); self.update_stats_label(); self.canvas.draw_idle()

        self._refresh_ma_signal_list(); self._refresh_msd_signal_list()

    # ================================================================
    # RESET PLOT
    # ================================================================
    def reset_plot(self):
        self.ax_main.clear(); self.ax_roc.clear()
        self.ax_main_r.clear(); self.ax_roc_r.clear()
        self.ax_main.set_title("Signals"); self.ax_roc.set_title("Rate of Change")
        self.ax_main_r.set_ylabel("Secondary axis", color="#BF360C", labelpad=2)
        self.ax_roc_r.set_ylabel("ROC (right)", color="#BF360C", labelpad=2)
        self.ax_main_r.tick_params(axis="y", colors="#BF360C")
        self.ax_roc_r.tick_params(axis="y", colors="#BF360C")
        self.signal_axis_map.clear()

        for name, widgets in self.all_signal_buttons.items():
            if widgets.get("is_ma"):
                ov = self._ma_overlays_by_name.get(name) or self._msd_overlays_by_name.get(name, {})
                widgets["btn"].config(relief="raised", bg=ov.get("color","#888888"), fg="white")
            else:
                widgets["btn"].config(relief="raised",
                    bg="#E3F2FD" if name in self.derived_signals else "white", fg="black")

        self._update_secondary_visibility()
        self.vline_main = self.ax_main.axvline(0, color="gray", linestyle="--", visible=False)
        self.vline_roc  = self.ax_roc.axvline(0,  color="gray", linestyle="--", visible=False)
        self.reset_x(); self.canvas.draw_idle()

    # ================================================================
    # SIGNAL TOGGLE
    # ================================================================
    def toggle_signal(self, event):
        if self.filtered_df is None: return
        w = event.widget; s = w.cget("text")
        if s in self.signal_axis_map:
            del self.signal_axis_map[s]
            ov = self._ma_overlays_by_name.get(s) or self._msd_overlays_by_name.get(s)
            if ov: w.config(relief="raised", bg=ov["color"], fg="white")
            else:   w.config(relief="raised", bg="#E3F2FD" if s in self.derived_signals else "white", fg="black")
        else:
            if s not in self.filtered_df.columns:
                messagebox.showerror("Missing column", f"'{s}' not in current data."); return
            self.signal_axis_map[s] = None
            ov = self._ma_overlays_by_name.get(s) or self._msd_overlays_by_name.get(s)
            if ov: w.config(relief="sunken", bg=ov["color"], fg="white")
            else:   w.config(relief="sunken", bg="#4CAF50", fg="white")
        self._redraw_signals(); self._update_secondary_visibility()
        self.auto_adjust_yaxis(); self.update_stats_label(); self.canvas.draw_idle()

    # ================================================================
    # LEGEND
    # ================================================================
    def _update_legends(self):
        for ax in (self.ax_main, self.ax_main_r, self.ax_roc, self.ax_roc_r):
            lines, _ = ax.get_legend_handles_labels()
            if lines: ax.legend(fontsize=8, loc="upper left" if ax in (self.ax_main, self.ax_roc) else "upper right")
            elif ax.get_legend(): ax.get_legend().remove()

    # ================================================================
    # REDRAW SIGNALS
    # ================================================================
    def _redraw_signals(self):
        if self.filtered_df is None: return
        xlim  = self.ax_main.get_xlim()
        t_num = mdates.date2num(self.filtered_df["Time"].to_numpy())
        view  = self.filtered_df[(t_num >= xlim[0]) & (t_num <= xlim[1])]

        prop_cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        all_names  = list(self.signal_axis_map.keys())

        keep = {self.vline_main, self.vline_roc}
        for ax in (self.ax_main, self.ax_main_r, self.ax_roc, self.ax_roc_r):
            for a in ax.get_lines():
                if a not in keep:
                    try: a.remove()
                    except: pass

        new_map = {}
        for i, s in enumerate(all_names):
            ma_info  = self._ma_overlays_by_name.get(s)
            msd_info = self._msd_overlays_by_name.get(s)
            ov = ma_info or msd_info
            color = ov["color"] if ov else prop_cycle[i % len(prop_cycle)]
            ax_m, ax_r = self._axes_for(s)
            n = len(view)

            if n == 0 or s not in view.columns:
                line,     = ax_m.plot([], [], label=s, color=color)
                roc_line, = ax_r.plot([], [], linestyle="--", label=s, color=color)
                new_map[s] = (line, roc_line); continue

            idx    = downsample_indices(n)
            x_data = view["Time"].iloc[idx]
            y_data = view[s].iloc[idx]
            lw = 2.0 if ov else 1.5
            ls = (0,(6,2)) if ma_info else ((0,(2,2)) if msd_info else "solid")
            line,     = ax_m.plot(x_data, y_data, label=s, color=color, linewidth=lw, linestyle=ls)

            roc = view[s].diff() / view["Time"].diff().dt.total_seconds()
            roc.iloc[0] = 0
            roc_line, = ax_r.plot(x_data, roc.iloc[idx], linestyle="--", label=s, color=color)
            new_map[s] = (line, roc_line)

        self.signal_axis_map = new_map
        self._update_legends(); self._update_secondary_visibility()

    # ================================================================
    # AUTO Y-AXIS — 5 % padding on each side
    # ================================================================
    @staticmethod
    def _padded_ylim(values, pad=0.05):
        """Return (ymin, ymax) with `pad` fraction of range added on each side."""
        if not values: return (0, 1)
        lo, hi = min(values), max(values)
        span = hi - lo
        if span == 0:
            margin = abs(lo) * pad if lo != 0 else 1.0
        else:
            margin = span * pad
        return (lo - margin, hi + margin)

    def auto_adjust_yaxis(self):
        if not self.signal_axis_map: return
        xlim  = self.ax_main.get_xlim()
        t_num = mdates.date2num(self.filtered_df["Time"].to_numpy())
        mask  = (t_num >= xlim[0]) & (t_num <= xlim[1])

        lv, rv, lr, rr = [], [], [], []
        for s in self.signal_axis_map:
            vals = self.filtered_df[s][mask]
            roc  = (self.filtered_df[s].diff() /
                    self.filtered_df["Time"].diff().dt.total_seconds())
            roc.iloc[0] = 0
            if self.signal_side.get(s, "left") == "right":
                rv.extend(vals.dropna().tolist()); rr.extend(roc[mask].dropna().tolist())
            else:
                lv.extend(vals.dropna().tolist()); lr.extend(roc[mask].dropna().tolist())

        if lv: self.ax_main.set_ylim(*self._padded_ylim(lv))
        if rv: self.ax_main_r.set_ylim(*self._padded_ylim(rv))
        if lr: self.ax_roc.set_ylim(*self._padded_ylim(lr))
        if rr: self.ax_roc_r.set_ylim(*self._padded_ylim(rr))

    # ================================================================
    # CURSOR / HOVER
    # ================================================================
    def update_cursor(self, event):
        if not event.inaxes or self.filtered_df is None:
            if hasattr(self, "hover_annotation"):
                try: self.hover_annotation.set_visible(False)
                except: pass
            self.canvas.draw_idle(); return

        x = event.xdata
        if x is None: return
        x_str = mdates.num2date(x).strftime("%Y-%m-%d %H:%M:%S")
        self.vline_main.set_xdata([x]); self.vline_roc.set_xdata([x])
        self.vline_main.set_visible(True); self.vline_roc.set_visible(True)

        for m in self.highlight_markers:
            try: m.remove()
            except: pass
        self.highlight_markers.clear()

        if hasattr(self, "hover_annotation"):
            try: self.hover_annotation.remove()
            except: pass
            del self.hover_annotation

        times = mdates.date2num(self.filtered_df["Time"].to_numpy())
        idx   = min(max(bisect.bisect_left(times, x), 1), len(times) - 1)

        tooltip_lines = []
        xlim = self.ax_main.get_xlim()
        mask = (times >= xlim[0]) & (times <= xlim[1])
        last_ann_y = None; last_ann_sig = None

        for s in self.signal_axis_map:
            ax_m, ax_r = self._axes_for(s)
            y_val = self.filtered_df[s].iloc[idx]
            dt    = (self.filtered_df["Time"].iloc[idx] - self.filtered_df["Time"].iloc[idx-1]).total_seconds()
            roc   = 0 if dt == 0 else (self.filtered_df[s].iloc[idx] - self.filtered_df[s].iloc[idx-1]) / dt
            m1, = ax_m.plot(self.filtered_df["Time"].iloc[idx], y_val, 'o', color="yellow", markersize=8, zorder=5)
            m2, = ax_r.plot(self.filtered_df["Time"].iloc[idx], roc,  'o', color="yellow", markersize=8, zorder=5)
            self.highlight_markers.extend([m1, m2])
            vals = self.filtered_df[s][mask]
            side_tag = "R" if self.signal_side.get(s, "left") == "right" else "L"
            tooltip_lines.append(
                f"{s} [{side_tag}]:\nTime={self.filtered_df['Time'].iloc[idx]}\n"
                f"y={y_val:.4f}  ROC={roc:.4f}/s\n"
                f"Min={vals.min():.4f}  Max={vals.max():.4f}  Mean={vals.mean():.4f}  Std={vals.std():.4f}"
            )
            if self.signal_side.get(s, "left") == "left":
                last_ann_y = y_val; last_ann_sig = s

        if tooltip_lines:
            figw, figh = self.fig.get_size_inches() * self.fig.dpi
            offx = -150 if event.x > figw * 0.7 else 15
            offy = -80  if event.y > figh * 0.7 else 15
            if last_ann_y is None:
                last_ann_sig = list(self.signal_axis_map.keys())[0]
                last_ann_y   = self.filtered_df[last_ann_sig].iloc[idx]
            self.hover_annotation = self.ax_main.annotate(
                "\n\n".join(tooltip_lines),
                xy=(self.filtered_df["Time"].iloc[idx], last_ann_y),
                xytext=(offx, offy), textcoords="offset points",
                bbox=dict(boxstyle="round", fc="yellow", alpha=0.9),
                arrowprops=dict(arrowstyle="->"), annotation_clip=False, zorder=20)
            self.coord_label.config(text=f"(x={x_str})")

        self.update_stats_label(); self.canvas.draw_idle()

    def on_mouse_leave(self, event):
        if hasattr(self, "hover_annotation"):
            try: self.hover_annotation.set_visible(False)
            except: pass
        self.canvas.draw_idle()

    # ================================================================
    # STATS LABEL
    # ================================================================
    def update_stats_label(self):
        if self.filtered_df is None or not self.signal_axis_map:
            self.stats_label.config(text=""); return
        xlim = self.ax_main.get_xlim()
        mask = ((mdates.date2num(self.filtered_df["Time"].to_numpy()) >= xlim[0]) &
                (mdates.date2num(self.filtered_df["Time"].to_numpy()) <= xlim[1]))
        stats = []
        for s in self.signal_axis_map:
            vals = self.filtered_df[s][mask]
            if not len(vals): continue
            side_tag = "[R]" if self.signal_side.get(s,"left") == "right" else "[L]"
            stats.append(f"{s}{side_tag}: Min={vals.min():.4f}  Max={vals.max():.4f}  "
                         f"Mean={vals.mean():.4f}  Median={vals.median():.4f}  Std={vals.std():.4f}")
        if not stats: self.stats_label.config(text=""); return
        cols = 3; rows = math.ceil(len(stats)/cols)
        grid = [[""] * cols for _ in range(rows)]
        for i, txt in enumerate(stats): grid[i % rows][i // rows] = txt
        self.stats_label.config(text="\n".join(
            "     ".join(f"{x:<40}" for x in r if x) for r in grid))

    # ================================================================
    # ZOOM / PAN / RESET
    # ================================================================
    def zoom(self, event):
        if self.filtered_df is None: return
        if event.inaxes in (self.ax_main, self.ax_main_r, self.ax_roc, self.ax_roc_r):
            x = event.xdata
            if x is None: return
            left, right = self.ax_main.get_xlim()
            scale = 0.85 if event.button == "up" else 1.15
            new_left  = max(x - (x-left)*scale,  mdates.date2num(self.filtered_df["Time"].min()))
            new_right = min(x + (right-x)*scale, mdates.date2num(self.filtered_df["Time"].max()))
            self.ax_main.set_xlim(new_left, new_right)
            self.ax_roc.set_xlim(new_left, new_right)
            self.update_time_entries(); self._redraw_signals()
            self.auto_adjust_yaxis(); self.update_stats_label()
        self.canvas.draw_idle()

    def start_pan(self, event):
        if event.button == 1 and event.inaxes in (self.ax_main, self.ax_roc,
                                                    self.ax_main_r, self.ax_roc_r):
            self._rb_press_x  = event.x;  self._rb_press_y  = event.y
            self._rb_start_x  = event.xdata; self._rb_start_y  = event.ydata
            self._rb_start_ax = event.inaxes; self._rb_active = False

    def stop_pan(self, event):
        if event.button != 1: return
        for attr in ("_rb_rect_main", "_rb_rect_roc"):
            r = getattr(self, attr, None)
            if r is not None:
                try: r.remove()
                except: pass
            setattr(self, attr, None)

        if self._rb_active and self._rb_start_x is not None and event.xdata is not None:
            x0, x1 = sorted([self._rb_start_x, event.xdata])
            if x1 - x0 > 0:
                self.ax_main.set_xlim(x0, x1); self.ax_roc.set_xlim(x0, x1)
                y0, y1 = self._rb_start_y, event.ydata
                y_zoomed = y0 is not None and y1 is not None and abs(y1-y0) > 1e-10
                self.update_time_entries(); self._redraw_signals()
                if y_zoomed:
                    ylo, yhi = sorted([y0, y1])
                    self._rb_start_ax.set_ylim(ylo, yhi)
                else:
                    self.auto_adjust_yaxis()
                self.update_stats_label()

        self._rb_active = False; self._rb_start_x = None; self._rb_start_y = None
        self._rb_press_x = None; self._rb_press_y = None; self._rb_start_ax = None
        self.canvas.draw_idle()

    def pan(self, event):
        if self._rb_start_x is None or event.xdata is None or event.button != 1: return
        if not self._rb_active:
            if (abs(event.x-(self._rb_press_x or event.x)) < self._rb_MIN_PX and
                    abs(event.y-(self._rb_press_y or event.y)) < self._rb_MIN_PX): return
            self._rb_active = True

        from matplotlib.patches import Rectangle
        x0, x1 = self._rb_start_x, event.xdata
        ylim_main = self.ax_main.get_ylim(); ylim_roc = self.ax_roc.get_ylim()
        if self._rb_start_ax in (self.ax_main, self.ax_main_r):
            y0m, y1m = self._rb_start_y, (event.ydata if event.inaxes in (self.ax_main, self.ax_main_r) else ylim_main[1])
            y0r, y1r = ylim_roc
        else:
            y0r, y1r = self._rb_start_y, (event.ydata if event.inaxes in (self.ax_roc, self.ax_roc_r) else ylim_roc[1])
            y0m, y1m = ylim_main

        def _rect(old, ax, rx0, ry0, rx1, ry1):
            if old is not None:
                try: old.remove()
                except: pass
            r = Rectangle((min(rx0,rx1), min(ry0,ry1)), abs(rx1-rx0), abs(ry1-ry0),
                linewidth=1.5, edgecolor="#1976D2", facecolor="#90CAF9",
                alpha=0.25, linestyle=(0,(6,3)), zorder=10)
            ax.add_patch(r); return r

        self._rb_rect_main = _rect(self._rb_rect_main, self.ax_main, x0, y0m, x1, y1m)
        self._rb_rect_roc  = _rect(self._rb_rect_roc,  self.ax_roc,  x0, y0r, x1, y1r)
        self.canvas.draw_idle()

    def update_time_entries(self):
        left, right = self.ax_main.get_xlim()
        dtimes = mdates.num2date([left, right])
        self.start_date.set_date(dtimes[0].date())
        self.start_time.delete(0, "end"); self.start_time.insert(0, dtimes[0].strftime("%H:%M:%S"))
        self.end_date.set_date(dtimes[1].date())
        self.end_time.delete(0, "end"); self.end_time.insert(0, dtimes[1].strftime("%H:%M:%S"))

    def reset_x(self, absolute=False):
        if self.filtered_df is None: return
        xmin = (self.df if absolute else self.filtered_df)["Time"].min()
        xmax = (self.df if absolute else self.filtered_df)["Time"].max()
        self.ax_main.set_xlim(xmin, xmax); self.ax_roc.set_xlim(xmin, xmax)
        self.update_time_entries(); self._redraw_signals()
        self.auto_adjust_yaxis(); self.update_stats_label(); self.canvas.draw_idle()
        if absolute: self.apply_time_filter()

    # ================================================================
    # MOVING AVERAGE helpers
    # ================================================================
    def _ma_window_seconds(self):
        try: d = int(self.ma_days_var.get())
        except: d = 0
        try: h = int(self.ma_hours_var.get())
        except: h = 0
        try: m = int(self.ma_mins_var.get())
        except: m = 0
        return d*86400 + h*3600 + m*60

    def _update_ma_window_label(self, *_):
        try: secs = self._ma_window_seconds()
        except: return
        self.ma_window_summary.config(text="= " + (self._fmt_secs(secs) or "0s"))

    def _msd_window_seconds(self):
        try: d = int(self.msd_days_var.get())
        except: d = 0
        try: h = int(self.msd_hours_var.get())
        except: h = 0
        try: m = int(self.msd_mins_var.get())
        except: m = 0
        return d*86400 + h*3600 + m*60

    def _update_msd_window_label(self, *_):
        try: secs = self._msd_window_seconds()
        except: return
        self.msd_window_summary.config(text="= " + (self._fmt_secs(secs) or "0s"))

    @staticmethod
    def _fmt_secs(secs):
        if secs <= 0: return "0s"
        d, rem = divmod(secs, 86400); h, rem = divmod(rem, 3600); m, s = divmod(rem, 60)
        parts = []
        if d: parts.append(f"{d}d")
        if h: parts.append(f"{h}h")
        if m: parts.append(f"{m}m")
        if s: parts.append(f"{s}s")
        return " ".join(parts)

    def _seconds_to_rows(self, window_secs):
        if self.filtered_df is None or len(self.filtered_df) < 2: return max(1, int(window_secs))
        dt_arr = np.diff(self.filtered_df["Time"].to_numpy().astype("datetime64[ns]")).astype(float)*1e-9
        dt_med = float(np.median(dt_arr))
        return max(1, round(window_secs / dt_med)) if dt_med > 0 else max(1, int(window_secs))

    def _ma_label(self, sig, ws):
        return f"MA{self._fmt_secs(int(ws)).replace(' ','')}({sig})"

    def _msd_label(self, sig, ws):
        return f"MSD{self._fmt_secs(int(ws)).replace(' ','')}({sig})"

    # ================================================================
    # MA searchable dropdown
    # ================================================================
    def _on_ma_search_change(self, *_): pass

    def _on_ma_entry_key(self, event):
        if event.keysym in ("Return","Escape","Up","Down","Tab"): return
        q = self.ma_signal_var.get().strip().lower()
        items = [s for s in self._ma_all_signals if q in s.lower()]
        if items: self._ma_dd_items = items; self._ma_dd_show(items)
        else: self._ma_dd_hide()

    def _ma_dd_show(self, items):
        x = self.ma_signal_entry.winfo_rootx()
        y = self.ma_signal_entry.winfo_rooty() + self.ma_signal_entry.winfo_height()
        if self._ma_dd_win is None or not self._ma_dd_win.winfo_exists():
            self._ma_dd_win = tk.Toplevel(self.root)
            self._ma_dd_win.wm_overrideredirect(True); self._ma_dd_win.wm_attributes("-topmost", True)
            fr = tk.Frame(self._ma_dd_win, bd=1, relief="solid"); fr.pack(fill="both", expand=True)
            sb = tk.Scrollbar(fr, orient="vertical")
            self._ma_dd_lb = tk.Listbox(fr, yscrollcommand=sb.set, selectmode="single",
                font=("TkDefaultFont",10), bg="#F1F8E9", selectbackground="#66BB6A",
                selectforeground="white", height=min(8,len(items)), width=30, exportselection=False)
            sb.config(command=self._ma_dd_lb.yview)
            self._ma_dd_lb.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
            self._ma_dd_lb.bind("<ButtonRelease-1>", self._ma_dd_click)
            self._ma_dd_lb.bind("<Return>", lambda e: self._ma_dd_accept(e))
        self._ma_dd_lb.delete(0,"end")
        for it in items: self._ma_dd_lb.insert("end", f"  {it}")
        self._ma_dd_lb.config(height=min(8,len(items)))
        self._ma_dd_win.geometry(f"+{x}+{y}"); self._ma_dd_win.deiconify(); self._ma_dd_sel = -1

    def _ma_dd_hide(self):
        if self._ma_dd_win and self._ma_dd_win.winfo_exists(): self._ma_dd_win.withdraw()
        self._ma_dd_sel = -1

    def _ma_dd_down(self, event):
        if not self._ma_dd_items: self._ma_dd_items = list(self._ma_all_signals); self._ma_dd_show(self._ma_dd_items); return "break"
        if self._ma_dd_win and self._ma_dd_win.winfo_exists() and self._ma_dd_win.state()=="normal":
            self._ma_dd_sel = min(self._ma_dd_sel+1, len(self._ma_dd_items)-1)
            self._ma_dd_lb.selection_clear(0,"end"); self._ma_dd_lb.selection_set(self._ma_dd_sel); self._ma_dd_lb.see(self._ma_dd_sel)
        else: self._ma_dd_show(self._ma_dd_items)
        return "break"

    def _ma_dd_up(self, event):
        if not self._ma_dd_items: return "break"
        self._ma_dd_sel = max(self._ma_dd_sel-1, 0)
        self._ma_dd_lb.selection_clear(0,"end"); self._ma_dd_lb.selection_set(self._ma_dd_sel); self._ma_dd_lb.see(self._ma_dd_sel); return "break"

    def _ma_dd_accept(self, event=None):
        idx = self._ma_dd_sel if self._ma_dd_sel >= 0 else (self._ma_dd_lb.curselection()[0] if self._ma_dd_lb.curselection() else -1)
        if 0 <= idx < len(self._ma_dd_items): self.ma_signal_var.set(self._ma_dd_items[idx])
        self._ma_dd_hide(); return "break"

    def _ma_dd_click(self, event): self._ma_dd_sel = self._ma_dd_lb.nearest(event.y); self._ma_dd_accept()

    # ================================================================
    # _add_ma / _remove_ma
    # ================================================================
    def _add_ma(self):
        if self.filtered_df is None: messagebox.showwarning("No data","Load a CSV first."); return
        sig = self.ma_signal_var.get().strip()
        if not sig: messagebox.showwarning("No signal","Select a signal."); return
        if sig not in self.filtered_df.columns: messagebox.showerror("Missing", f"'{sig}' not found."); return
        ws = self._ma_window_seconds()
        if ws <= 0: messagebox.showwarning("Zero window","Set a window > 0."); return
        key = (sig, ws)
        if key in self.ma_overlays: messagebox.showinfo("Already added", f"{self._ma_label(sig,ws)} shown."); return
        color = self._ma_colors[len(self.ma_overlays) % len(self._ma_colors)]
        self.ma_overlays[key] = color
        ma_name = self._ma_label(sig, ws)
        self._ma_overlays_by_name[ma_name] = {"key":key,"color":color,"sig":sig,"window_secs":ws}
        wr = self._seconds_to_rows(ws)
        series = self.filtered_df[sig].rolling(wr, min_periods=1).mean()
        self.filtered_df[ma_name] = series.values
        self.df[ma_name] = np.nan; self.df.loc[self.filtered_df.index, ma_name] = series.values
        self._add_signal_button(ma_name, derived=True)
        w = self.all_signal_buttons[ma_name]
        w["btn"].config(bg=color, fg="white", font=("TkDefaultFont",8,"bold"))
        w["side_btn"].config(text="L", bg="#1565C0"); w["is_ma"] = True
        w["btn"].bind("<Button-3>", lambda e, k=key, n=ma_name: self._remove_ma(k, n))
        self._redraw_signals(); self._update_secondary_visibility(); self.auto_adjust_yaxis(); self.canvas.draw_idle()

    def _remove_ma(self, key, ma_name):
        self.ma_overlays.pop(key, None); self._ma_overlays_by_name.pop(ma_name, None)
        for fr in (self.filtered_df, self.df):
            if fr is not None and ma_name in fr.columns:
                try: fr.drop(columns=[ma_name], inplace=True)
                except: pass
        self.signal_axis_map.pop(ma_name, None); self.signal_side.pop(ma_name, None)
        w = self.all_signal_buttons.pop(ma_name, None)
        if w: w["frame"].destroy()
        self._redraw_signals(); self._update_secondary_visibility(); self.auto_adjust_yaxis(); self.canvas.draw_idle()

    def _refresh_ma_signal_list(self):
        if self.filtered_df is None: return
        self._ma_all_signals = [c for c in self.filtered_df.columns if c != "Time"]
        if self._ma_all_signals and not self.ma_signal_var.get():
            self.ma_signal_var.set(self._ma_all_signals[0])

    # ================================================================
    # MSD searchable dropdown
    # ================================================================
    def _on_msd_search_change(self, *_): pass

    def _on_msd_entry_key(self, event):
        if event.keysym in ("Return","Escape","Up","Down","Tab"): return
        q = self.msd_signal_var.get().strip().lower()
        items = [s for s in self._ma_all_signals if q in s.lower()]
        if items: self._msd_dd_items = items; self._msd_dd_show(items)
        else: self._msd_dd_hide()

    def _msd_dd_show(self, items):
        x = self.msd_signal_entry.winfo_rootx()
        y = self.msd_signal_entry.winfo_rooty() + self.msd_signal_entry.winfo_height()
        if self._msd_dd_win is None or not self._msd_dd_win.winfo_exists():
            self._msd_dd_win = tk.Toplevel(self.root)
            self._msd_dd_win.wm_overrideredirect(True); self._msd_dd_win.wm_attributes("-topmost", True)
            fr = tk.Frame(self._msd_dd_win, bd=1, relief="solid"); fr.pack(fill="both", expand=True)
            sb = tk.Scrollbar(fr, orient="vertical")
            self._msd_dd_lb = tk.Listbox(fr, yscrollcommand=sb.set, selectmode="single",
                font=("TkDefaultFont",10), bg="#FFF3E0", selectbackground="#FF8F00",
                selectforeground="white", height=min(8,len(items)), width=30, exportselection=False)
            sb.config(command=self._msd_dd_lb.yview)
            self._msd_dd_lb.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
            self._msd_dd_lb.bind("<ButtonRelease-1>", self._msd_dd_click)
            self._msd_dd_lb.bind("<Return>", lambda e: self._msd_dd_accept(e))
        self._msd_dd_lb.delete(0,"end")
        for it in items: self._msd_dd_lb.insert("end", f"  {it}")
        self._msd_dd_lb.config(height=min(8,len(items)))
        self._msd_dd_win.geometry(f"+{x}+{y}"); self._msd_dd_win.deiconify(); self._msd_dd_sel = -1

    def _msd_dd_hide(self):
        if self._msd_dd_win and self._msd_dd_win.winfo_exists(): self._msd_dd_win.withdraw()
        self._msd_dd_sel = -1

    def _msd_dd_down(self, event):
        if not self._msd_dd_items: self._msd_dd_items = list(self._ma_all_signals); self._msd_dd_show(self._msd_dd_items); return "break"
        if self._msd_dd_win and self._msd_dd_win.winfo_exists() and self._msd_dd_win.state()=="normal":
            self._msd_dd_sel = min(self._msd_dd_sel+1, len(self._msd_dd_items)-1)
            self._msd_dd_lb.selection_clear(0,"end"); self._msd_dd_lb.selection_set(self._msd_dd_sel); self._msd_dd_lb.see(self._msd_dd_sel)
        else: self._msd_dd_show(self._msd_dd_items)
        return "break"

    def _msd_dd_up(self, event):
        if not self._msd_dd_items: return "break"
        self._msd_dd_sel = max(self._msd_dd_sel-1, 0)
        self._msd_dd_lb.selection_clear(0,"end"); self._msd_dd_lb.selection_set(self._msd_dd_sel); self._msd_dd_lb.see(self._msd_dd_sel); return "break"

    def _msd_dd_accept(self, event=None):
        idx = self._msd_dd_sel if self._msd_dd_sel >= 0 else (self._msd_dd_lb.curselection()[0] if self._msd_dd_lb.curselection() else -1)
        if 0 <= idx < len(self._msd_dd_items): self.msd_signal_var.set(self._msd_dd_items[idx])
        self._msd_dd_hide(); return "break"

    def _msd_dd_click(self, event): self._msd_dd_sel = self._msd_dd_lb.nearest(event.y); self._msd_dd_accept()

    # ================================================================
    # _add_msd / _remove_msd
    # ================================================================
    def _add_msd(self):
        if self.filtered_df is None: messagebox.showwarning("No data","Load a CSV first."); return
        sig = self.msd_signal_var.get().strip()
        if not sig: messagebox.showwarning("No signal","Select a signal."); return
        if sig not in self.filtered_df.columns: messagebox.showerror("Missing", f"'{sig}' not found."); return
        ws = self._msd_window_seconds()
        if ws <= 0: messagebox.showwarning("Zero window","Set a window > 0."); return
        key = (sig, ws)
        if key in self.msd_overlays: messagebox.showinfo("Already added", f"{self._msd_label(sig,ws)} shown."); return
        color = self._msd_colors[len(self.msd_overlays) % len(self._msd_colors)]
        self.msd_overlays[key] = color
        msd_name = self._msd_label(sig, ws)
        self._msd_overlays_by_name[msd_name] = {"key":key,"color":color,"sig":sig,"window_secs":ws}
        wr = self._seconds_to_rows(ws)
        series = self.filtered_df[sig].rolling(wr, min_periods=2).std().fillna(0)
        self.filtered_df[msd_name] = series.values
        self.df[msd_name] = np.nan; self.df.loc[self.filtered_df.index, msd_name] = series.values
        self._add_signal_button(msd_name, derived=True)
        w = self.all_signal_buttons[msd_name]
        w["btn"].config(bg=color, fg="white", font=("TkDefaultFont",8,"bold"))
        w["side_btn"].config(text="L", bg="#1565C0"); w["is_ma"] = True
        w["btn"].bind("<Button-3>", lambda e, k=key, n=msd_name: self._remove_msd(k, n))
        self._redraw_signals(); self._update_secondary_visibility(); self.auto_adjust_yaxis(); self.canvas.draw_idle()

    def _remove_msd(self, key, msd_name):
        self.msd_overlays.pop(key, None); self._msd_overlays_by_name.pop(msd_name, None)
        for fr in (self.filtered_df, self.df):
            if fr is not None and msd_name in fr.columns:
                try: fr.drop(columns=[msd_name], inplace=True)
                except: pass
        self.signal_axis_map.pop(msd_name, None); self.signal_side.pop(msd_name, None)
        w = self.all_signal_buttons.pop(msd_name, None)
        if w: w["frame"].destroy()
        self._redraw_signals(); self._update_secondary_visibility(); self.auto_adjust_yaxis(); self.canvas.draw_idle()

    def _refresh_msd_signal_list(self):
        if self._ma_all_signals and not self.msd_signal_var.get():
            self.msd_signal_var.set(self._ma_all_signals[0])

    # ================================================================
    # FOURIER TRANSFORM
    # ================================================================
    def show_fft(self):
        if self.filtered_df is None: messagebox.showwarning("No data","Load a CSV first."); return
        if not self.signal_axis_map: messagebox.showwarning("No signals","Select at least one signal."); return
        xlim  = self.ax_main.get_xlim()
        t_num = mdates.date2num(self.filtered_df["Time"].to_numpy())
        mask  = (t_num >= xlim[0]) & (t_num <= xlim[1])
        view_df = self.filtered_df[mask].copy()
        if len(view_df) < 4: messagebox.showwarning("Too few points","Need >= 4 samples in view."); return

        win = tk.Toplevel(self.root); win.title("FFT — Frequency Spectrum"); win.geometry("1050x700")
        opt = tk.Frame(win); opt.pack(fill="x", padx=8, pady=4)
        tk.Label(opt, text="Window:").pack(side="left")
        win_var = tk.StringVar(value="hann")
        for w in ("none","hann","hamming","blackman","flattop"):
            tk.Radiobutton(opt, text=w, variable=win_var, value=w, command=lambda: _refresh()).pack(side="left", padx=3)
        tk.Label(opt, text="   Scale:").pack(side="left")
        scale_var = tk.StringVar(value="linear")
        for s in ("linear","log"):
            tk.Radiobutton(opt, text=s, variable=scale_var, value=s, command=lambda: _refresh()).pack(side="left", padx=3)
        tk.Label(opt, text="   Y:").pack(side="left")
        ymode_var = tk.StringVar(value="amplitude")
        for y in ("amplitude","power","dB"):
            tk.Radiobutton(opt, text=y, variable=ymode_var, value=y, command=lambda: _refresh()).pack(side="left", padx=3)
        tk.Label(opt, text="   Peaks:").pack(side="left")
        peak_var = tk.IntVar(value=5)
        tk.Spinbox(opt, from_=0, to=20, width=3, textvariable=peak_var, command=lambda: _refresh()).pack(side="left", padx=3)

        def _export_fft():
            path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV","*.csv")])
            if not path: return
            rows = []
            for sig in self.signal_axis_map:
                if sig not in view_df.columns: continue
                y = view_df[sig].to_numpy(dtype=float); y -= y.mean(); n = len(y)
                t_sec = (view_df["Time"].iloc[-1]-view_df["Time"].iloc[0]).total_seconds()
                fs = (n-1)/t_sec if t_sec > 0 else 1.0
                freqs = np.fft.rfftfreq(n, d=1.0/fs); amp = np.abs(np.fft.rfft(y))*2/n
                for ff, a in zip(freqs, amp): rows.append({"Signal":sig,"Frequency_Hz":ff,"Amplitude":a,"Period_s":1/ff if ff>0 else np.inf})
            pd.DataFrame(rows).to_csv(path, index=False); messagebox.showinfo("Exported", f"Saved:\n{path}")

        tk.Button(opt, text="Export FFT CSV", command=_export_fft, bg="#388E3C", fg="white").pack(side="right", padx=6)

        n_sigs = len(self.signal_axis_map)
        fig_fft, axes = plt.subplots(n_sigs, 1, figsize=(10, max(3, 3*n_sigs)), squeeze=False)
        fig_fft.suptitle("Fourier Transform — Frequency Spectrum", fontsize=13, fontweight="bold")
        canvas_fft = FigureCanvasTkAgg(fig_fft, master=win); canvas_fft.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(canvas_fft, win).update()
        info_lbl = tk.Label(win, text="", anchor="w", bg="#EDE7F6", justify="left", font=("Courier",9))
        info_lbl.pack(fill="x", padx=4, pady=2)

        WFNS = {"none":lambda n:np.ones(n),"hann":np.hanning,"hamming":np.hamming,"blackman":np.blackman,"flattop":lambda n:np.blackman(n)}
        try:
            from scipy.signal.windows import flattop as _ft; WFNS["flattop"] = _ft
        except ImportError: pass

        def _refresh():
            wfn = WFNS.get(win_var.get(), np.hanning); n_peaks = peak_var.get(); info_parts = []
            for ax, sig in zip(axes[:,0], list(self.signal_axis_map.keys())):
                ax.clear()
                if sig not in view_df.columns: ax.set_title(f"{sig} — not in view"); continue
                y = view_df[sig].to_numpy(dtype=float); y -= y.mean(); n = len(y)
                dt_arr = np.diff(view_df["Time"].to_numpy().astype("datetime64[ns]")).astype(float)*1e-9
                dt_med = np.median(dt_arr) if len(dt_arr) else 1.0
                if dt_med <= 0: dt_med = 1.0
                fs = 1.0/dt_med; win_arr = wfn(n); cg = win_arr.mean()
                fft_v = np.fft.rfft(y*win_arr); freqs = np.fft.rfftfreq(n, d=1.0/fs)
                amp = np.abs(fft_v)*2/(n*cg); amp[0] /= 2
                ym = ymode_var.get()
                y_plot = amp if ym=="amplitude" else (amp**2 if ym=="power" else 20*np.log10(np.maximum(amp,1e-12)))
                ylabel = {"amplitude":"Amplitude","power":"Power","dB":"Magnitude (dB)"}.get(ym,"Amplitude")
                ax.plot(freqs[1:], y_plot[1:], color="#7B1FA2", linewidth=1.0)
                ax.fill_between(freqs[1:], y_plot[1:], alpha=0.15, color="#CE93D8")
                ax.set_xlabel("Frequency (Hz)"); ax.set_ylabel(ylabel)
                sc = scale_var.get()
                if sc == "log": ax.set_xscale("log")
                ax.grid(True, which="both", alpha=0.3)
                if n_peaks > 0 and len(amp) > 2:
                    from scipy.signal import find_peaks as _fp
                    try: pidx, _ = _fp(amp[1:], height=amp[1:].max()*0.05); pidx += 1
                    except: pidx = np.argsort(amp[1:])[::-1][:n_peaks]+1
                    pidx = sorted(pidx, key=lambda i: amp[i], reverse=True)[:n_peaks]
                    si = [f"{sig}:"]
                    for rank, pi in enumerate(pidx, 1):
                        fhz = freqs[pi]; av = y_plot[pi]; period = 1.0/fhz if fhz > 0 else np.inf
                        ax.axvline(fhz, color="red", linewidth=0.8, linestyle="--", alpha=0.6)
                        ax.annotate(f"#{rank}\n{fhz:.3f} Hz\nT={period:.3f}s", xy=(fhz,av),
                            xytext=(6,4), textcoords="offset points", fontsize=7, color="darkred",
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7))
                        si.append(f"  #{rank}: {fhz:.4f} Hz  |  T={period:.4f}s  |  {ym}={av:.4f}")
                    info_parts.append("\n".join(si))
                t_span = (view_df["Time"].iloc[-1]-view_df["Time"].iloc[0]).total_seconds()
                ax.set_title(f"{sig}   [N={n}, fs≈{fs:.2f} Hz, Δf={fs/n:.4f} Hz, span={t_span:.2f}s, window={win_var.get()}]", fontsize=9)
            fig_fft.tight_layout(rect=[0,0,1,0.95]); canvas_fft.draw_idle()
            info_lbl.config(text="\n".join(info_parts) if info_parts else "")

        _refresh()

    # ================================================================
    # EXPORT / SCREENSHOT
    # ================================================================
    def export_csv(self):
        if self.filtered_df is None: return
        path = filedialog.asksaveasfilename(defaultextension=".csv")
        if path: self.filtered_df.to_csv(path, index=False)

    def save_screenshot(self, event=None):
        if self.last_loaded_file is None: return
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(os.path.dirname(self.last_loaded_file), f"trend_capture_{ts}.png")
        self.fig.savefig(path, dpi=300); print("Saved:", path)


# ---------------- MAIN ----------------
if __name__ == "__main__":
    root = TkinterDnD.Tk()
    app  = TrendViewer(root)
    root.mainloop()
