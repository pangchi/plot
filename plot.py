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
    """
    Evaluate an Excel-style arithmetic expression using column names as variables.
    Supports: +, -, *, /, **, (, ), abs(), sqrt(), log(), exp(), sin(), cos(), tan(),
              min(), max(), mean(), std(), rolling_mean(signal, window), diff(signal)
    Column names with spaces should be wrapped in backticks: `col name`
    """
    # Extract backtick-quoted column names and replace with safe tokens
    token_map = {}
    def replace_col(m):
        col = m.group(1)
        token = f"__COL{len(token_map)}__"
        token_map[token] = col
        return token

    expr_clean = re.sub(r"`([^`]+)`", replace_col, expr)

    # Also detect bare column names (no spaces) that exist in df
    # Sort by length desc so longer names matched first
    bare_cols = sorted([c for c in df.columns if re.match(r"^\w+$", c)], key=len, reverse=True)
    for col in bare_cols:
        pattern = r"(?<!\w)" + re.escape(col) + r"(?!\w)"
        if re.search(pattern, expr_clean):
            token = f"__COL{len(token_map)}__"
            token_map[token] = col
            expr_clean = re.sub(pattern, token, expr_clean)

    # Build safe namespace
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

    # Inject column arrays
    for token, col in token_map.items():
        if col not in df.columns:
            raise ValueError(f"Column not found: '{col}'")
        safe_ns[token] = df[col].to_numpy(dtype=float)

    # Rewrite token names to valid python identifiers for eval
    for token in token_map:
        expr_clean = expr_clean.replace(token, token)  # already safe identifiers

    result = eval(expr_clean, {"__builtins__": {}}, safe_ns)

    if np.isscalar(result):
        result = np.full(len(df), float(result))
    return np.array(result, dtype=float)


# ---------------- TREND VIEWER ----------------
class TrendViewer:

    def __init__(self, root):
        self.root = root
        self.root.title("Trend Viewer")
        self.root.geometry("1400x1050")

        self.df = None
        self.filtered_df = None
        self.signal_axis_map = {}
        self.highlight_markers = []
        self.last_loaded_file = None
        self.derived_signals = {}   # name -> expression string
        self.all_signal_buttons = {}  # signal name -> tk.Label widget

        self._dragging = False
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

        tk.Button(f, text="Apply", command=self.apply_time_filter).grid(row=0, column=6, padx=5)
        tk.Button(f, text="Export", command=self.export_csv).grid(row=0, column=7, padx=5)
        tk.Button(f, text="Reset X", command=self.reset_x).grid(row=0, column=8, padx=5)

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

        # -------- Autocomplete popup (Toplevel, no border) --------
        self._ac_win    = None   # Toplevel window
        self._ac_lb     = None   # Listbox inside it
        self._ac_items  = []     # current suggestion list
        self._ac_sel    = -1     # selected index (-1 = none)
        self._ac_token  = ""     # the prefix being completed

        # -------- Signal buttons panel --------
        self.signal_outer = tk.Frame(root)
        self.signal_outer.pack(fill="x", pady=2)

        self.signal_frame = tk.LabelFrame(self.signal_outer, text="Signals")
        self.signal_frame.pack(fill="x", padx=4)

        # -------- Matplotlib figure --------
        self.fig, (self.ax_main, self.ax_roc) = plt.subplots(
            2, 1, sharex=True,
            gridspec_kw={"height_ratios": [3, 1]},
            figsize=(12, 8)
        )
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

        # -------- Stats label at bottom --------
        self.stats_label = tk.Label(root, text="", anchor="w", bg="#f0f0f0", justify="left")
        self.stats_label.pack(fill="x")

        self.ax_main.format_coord = lambda x, y: ""
        self.ax_roc.format_coord  = lambda x, y: ""

        # -------- Events --------
        self.canvas.mpl_connect("motion_notify_event", self.update_cursor)
        self.canvas.mpl_connect("scroll_event", self.zoom)
        self.canvas.mpl_connect("button_press_event", self.start_pan)
        self.canvas.mpl_connect("button_release_event", self.stop_pan)
        self.canvas.mpl_connect("motion_notify_event", self.pan)
        self.canvas.mpl_connect("figure_leave_event", self.on_mouse_leave)

    # ================================================================
    # SEARCH
    # ================================================================
    def _on_search_change(self, *_):
        query = self.search_var.get().strip().lower()
        for name, btn in self.all_signal_buttons.items():
            if query == "" or query in name.lower():
                btn.grid()
            else:
                btn.grid_remove()

    # ================================================================
    # AUTOCOMPLETE — expression entry
    # ================================================================
    # Built-in function tokens that should appear in suggestions
    _BUILTINS = [
        "abs(", "sqrt(", "log(", "log10(", "exp(",
        "sin(", "cos(", "tan(",
        "mean(", "std(",
        "diff(", "rolling_mean(", "rolling_std(", "cumsum(",
        "min(", "max(",
        "pi", "e",
    ]

    def _get_token_at_cursor(self):
        """Return (prefix, start_pos) of the identifier/token being typed."""
        text = self.expr_entry.get()
        pos  = self.expr_entry.index(tk.INSERT)
        # Walk left to find start of current word (alphanumeric / underscore / backtick)
        start = pos
        in_backtick = False
        for i in range(pos - 1, -1, -1):
            ch = text[i]
            if ch == '`':
                in_backtick = True
                start = i
                break
            if ch.isalnum() or ch == '_':
                start = i
            else:
                break
        prefix = text[start:pos]
        return prefix, start, pos

    def _suggestions(self, prefix):
        """Return sorted list of completions matching prefix (case-insensitive)."""
        if not prefix:
            return []
        pl = prefix.lstrip('`').lower()
        results = []
        # Signal columns first
        if self.filtered_df is not None:
            for col in self.filtered_df.columns:
                if col == "Time": continue
                if col.lower().startswith(pl):
                    # Wrap in backticks if name contains spaces / special chars
                    token = f"`{col}`" if not re.match(r"^\w+$", col) else col
                    results.append(("signal", token, col))
        # Built-ins
        for b in self._BUILTINS:
            if b.lower().startswith(pl):
                results.append(("builtin", b, b))
        return results

    def _on_expr_keyrelease(self, event):
        # Don't re-trigger on navigation keys
        if event.keysym in ("Tab","Up","Down","Return","Escape","Left","Right"):
            return
        prefix, start, pos = self._get_token_at_cursor()
        sug = self._suggestions(prefix)
        if sug:
            self._ac_token = prefix
            self._ac_items = sug
            self._ac_sel   = -1
            self._ac_show(sug)
        else:
            self._ac_hide()

    def _ac_show(self, suggestions):
        """Create or refresh the autocomplete popup listbox."""
        # Compute position below the expr_entry widget
        x = self.expr_entry.winfo_rootx()
        y = self.expr_entry.winfo_rooty() + self.expr_entry.winfo_height()

        if self._ac_win is None or not self._ac_win.winfo_exists():
            self._ac_win = tk.Toplevel(self.root)
            self._ac_win.wm_overrideredirect(True)   # no title bar / border
            self._ac_win.wm_attributes("-topmost", True)

            frame = tk.Frame(self._ac_win, bd=1, relief="solid")
            frame.pack(fill="both", expand=True)

            sb = tk.Scrollbar(frame, orient="vertical")
            self._ac_lb = tk.Listbox(
                frame,
                yscrollcommand=sb.set,
                selectmode="single",
                activestyle="dotbox",
                font=("Courier", 10),
                bg="#FFFDE7",
                selectbackground="#FFC107",
                selectforeground="black",
                height=min(8, len(suggestions)),
                width=36,
                exportselection=False,
            )
            sb.config(command=self._ac_lb.yview)
            self._ac_lb.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")

            self._ac_lb.bind("<ButtonRelease-1>", self._ac_click)
            self._ac_lb.bind("<Return>",           self._ac_accept)

        # Populate
        self._ac_lb.delete(0, "end")
        for kind, token, label in suggestions:
            icon = "⚡" if kind == "signal" else "ƒ"
            self._ac_lb.insert("end", f" {icon}  {label}")

        self._ac_lb.config(height=min(8, len(suggestions)))
        self._ac_win.geometry(f"+{x}+{y}")
        self._ac_win.deiconify()
        self._ac_sel = -1

    def _ac_hide(self):
        if self._ac_win and self._ac_win.winfo_exists():
            self._ac_win.withdraw()
        self._ac_sel = -1

    def _ac_down(self, event):
        if not self._ac_items: return "break"
        if self._ac_win and self._ac_win.winfo_exists() and self._ac_win.state() == "normal":
            self._ac_sel = min(self._ac_sel + 1, len(self._ac_items) - 1)
            self._ac_lb.selection_clear(0, "end")
            self._ac_lb.selection_set(self._ac_sel)
            self._ac_lb.see(self._ac_sel)
        else:
            self._ac_show(self._ac_items)
        return "break"

    def _ac_up(self, event):
        if not self._ac_items: return "break"
        self._ac_sel = max(self._ac_sel - 1, 0)
        self._ac_lb.selection_clear(0, "end")
        self._ac_lb.selection_set(self._ac_sel)
        self._ac_lb.see(self._ac_sel)
        return "break"

    def _ac_tab(self, event):
        """Tab: accept the top/selected suggestion."""
        if self._ac_items:
            idx = self._ac_sel if self._ac_sel >= 0 else 0
            self._ac_accept_index(idx)
        return "break"

    def _ac_click(self, event):
        idx = self._ac_lb.nearest(event.y)
        self._ac_accept_index(idx)

    def _ac_accept(self, event):
        idx = self._ac_lb.curselection()
        if idx:
            self._ac_accept_index(idx[0])
        return "break"

    def _ac_accept_index(self, idx):
        """Insert the chosen completion into the expression entry."""
        if idx < 0 or idx >= len(self._ac_items):
            return
        _, token, _ = self._ac_items[idx]

        text  = self.expr_entry.get()
        pos   = self.expr_entry.index(tk.INSERT)
        # Find start of current token
        start = pos
        for i in range(pos - 1, -1, -1):
            ch = text[i]
            if ch == '`' or ch.isalnum() or ch == '_':
                start = i
            else:
                break

        new_text = text[:start] + token + text[pos:]
        self.expr_entry.delete(0, "end")
        self.expr_entry.insert(0, new_text)
        new_cursor = start + len(token)
        self.expr_entry.icursor(new_cursor)
        self._ac_hide()
        self.expr_entry.focus_set()

    # ================================================================
    # DERIVED / ARITHMETIC SIGNALS
    # ================================================================
    def _add_derived_signal(self):
        if self.filtered_df is None:
            messagebox.showwarning("No data", "Load a CSV first.")
            return
        expr = self.expr_var.get().strip()
        name = self.expr_name_var.get().strip()
        if not expr:
            messagebox.showwarning("Empty expression", "Enter a formula.")
            return
        if not name:
            name = f"expr_{len(self.derived_signals)+1}"

        try:
            result = evaluate_expression(expr, self.filtered_df)
        except Exception as ex:
            messagebox.showerror("Expression error", str(ex))
            return

        if len(result) != len(self.filtered_df):
            messagebox.showerror("Shape mismatch", f"Result length {len(result)} != data length {len(self.filtered_df)}")
            return

        # Store expression and inject into df
        self.derived_signals[name] = expr
        self.df[name] = np.nan
        self.df.loc[self.filtered_df.index, name] = result
        self.filtered_df = self.filtered_df.copy()
        self.filtered_df[name] = result

        # Add button
        self._add_signal_button(name, derived=True)
        self.expr_var.set("")
        self.expr_name_var.set("")

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
            "  (SignalA - mean(SignalA)) / std(SignalA)   ← z-score\n"
        )
        win = tk.Toplevel(self.root)
        win.title("Expression Help")
        win.geometry("480x480")
        txt = tk.Text(win, wrap="word", font=("Courier", 10), padx=10, pady=10)
        txt.insert("1.0", help_text)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)

    # ================================================================
    # CSV LOAD
    # ================================================================
    def load_csv_dnd(self, event):
        path = event.data.strip("{}")
        self.load_csv(path)

    def load_csv(self, path):
        try:
            self.df = pd.read_csv(path)
            self.df["Time"] = pd.to_datetime(self.df["Time"])
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        self.last_loaded_file = path
        self.derived_signals.clear()
        self.all_signal_buttons.clear()

        for w in self.signal_frame.winfo_children():
            w.destroy()

        tmin = self.df["Time"].min()
        tmax = self.df["Time"].max()
        self.start_date.set_date(tmin.date())
        self.start_time.delete(0, "end")
        self.start_time.insert(0, tmin.strftime("%H:%M:%S"))
        self.end_date.set_date(tmax.date())
        self.end_time.delete(0, "end")
        self.end_time.insert(0, tmax.strftime("%H:%M:%S"))

        self.apply_time_filter()

        for c in self.df.columns:
            if c != "Time":
                self._add_signal_button(c)

    def _add_signal_button(self, name, derived=False):
        max_per_row = 12
        existing = list(self.all_signal_buttons.keys())
        idx = len(existing)
        row = idx // max_per_row
        col = idx % max_per_row

        bg = "#E3F2FD" if derived else "white"
        b = tk.Label(
            self.signal_frame,
            text=name,
            bg=bg,
            relief="raised",
            padx=6,
            pady=3
        )
        b.grid(row=row, column=col, padx=3, pady=3, sticky="w")
        b.bind("<Button-1>", self.toggle_signal)
        if derived:
            b.bind("<Button-3>", lambda e, n=name: self._remove_derived_signal(n))

        self.all_signal_buttons[name] = b

        # Re-apply search filter
        query = self.search_var.get().strip().lower()
        if query and query not in name.lower():
            b.grid_remove()

    def _remove_derived_signal(self, name):
        if name in self.signal_axis_map:
            line, roc = self.signal_axis_map[name]
            line.remove()
            roc.remove()
            del self.signal_axis_map[name]
            self.ax_main.legend()
            self.ax_roc.legend()
            self.canvas.draw_idle()

        if name in self.all_signal_buttons:
            self.all_signal_buttons[name].destroy()
            del self.all_signal_buttons[name]

        if name in self.derived_signals:
            del self.derived_signals[name]

        if name in self.filtered_df.columns:
            self.filtered_df = self.filtered_df.drop(columns=[name])
        if name in self.df.columns:
            self.df = self.df.drop(columns=[name])

    # ================================================================
    # FILTER
    # ================================================================
    def apply_time_filter(self):
        if self.df is None: return
        start = pd.to_datetime(f"{self.start_date.get()} {self.start_time.get()}")
        end   = pd.to_datetime(f"{self.end_date.get()} {self.end_time.get()}")
        mask  = (self.df["Time"] >= start) & (self.df["Time"] <= end)
        self.filtered_df = self.df.loc[mask].copy()

        # Re-evaluate derived signals on new filter window
        for name, expr in self.derived_signals.items():
            try:
                result = evaluate_expression(expr, self.filtered_df)
                self.filtered_df[name] = result
            except Exception:
                pass

        self.reset_plot()

    # ================================================================
    # RESET PLOT
    # ================================================================
    def reset_plot(self):
        self.ax_main.clear()
        self.ax_roc.clear()
        self.ax_main.set_title("Signals")
        self.ax_roc.set_title("Rate of Change")
        self.signal_axis_map.clear()

        # Reset button appearances
        for name, btn in self.all_signal_buttons.items():
            is_derived = name in self.derived_signals
            btn.config(relief="raised", bg="#E3F2FD" if is_derived else "white", fg="black")

        self.vline_main = self.ax_main.axvline(0, color="gray", linestyle="--", visible=False)
        self.vline_roc  = self.ax_roc.axvline(0, color="gray", linestyle="--", visible=False)
        self.reset_x()
        self.canvas.draw_idle()

    # ================================================================
    # SIGNAL TOGGLE
    # ================================================================
    def toggle_signal(self, event):
        if self.filtered_df is None: return
        w = event.widget
        s = w.cget("text")

        if s in self.signal_axis_map:
            line, roc = self.signal_axis_map[s]
            line.remove()
            roc.remove()
            del self.signal_axis_map[s]
            is_derived = s in self.derived_signals
            w.config(relief="raised", bg="#E3F2FD" if is_derived else "white", fg="black")
        else:
            if s not in self.filtered_df.columns:
                messagebox.showerror("Missing column", f"'{s}' not in current data.")
                return
            n = len(self.filtered_df)
            indices = downsample_indices(n)
            x_data  = self.filtered_df["Time"].iloc[indices]
            y_data  = self.filtered_df[s].iloc[indices]
            line,   = self.ax_main.plot(x_data, y_data, label=s)

            roc = self.filtered_df[s].diff() / self.filtered_df["Time"].diff().dt.total_seconds()
            roc.iloc[0] = 0
            roc_ds  = roc.iloc[indices]
            roc_line, = self.ax_roc.plot(x_data, roc_ds, linestyle="--", label=s)

            self.signal_axis_map[s] = (line, roc_line)
            w.config(relief="sunken", bg="#4CAF50", fg="white")

        self.ax_main.legend()
        self.ax_roc.legend()
        self.auto_adjust_yaxis()
        self.update_stats_label()
        self.canvas.draw_idle()

    # ================================================================
    # AUTO Y-AXIS
    # ================================================================
    def auto_adjust_yaxis(self):
        if not self.signal_axis_map: return
        xlim = self.ax_main.get_xlim()
        t_num = mdates.date2num(self.filtered_df["Time"].to_numpy())
        mask  = (t_num >= xlim[0]) & (t_num <= xlim[1])

        all_vals = []
        for s in self.signal_axis_map:
            vals = self.filtered_df[s][mask]
            all_vals.extend(vals.dropna())
        if all_vals:
            self.ax_main.set_ylim(min(all_vals), max(all_vals))

        all_roc = []
        for s in self.signal_axis_map:
            roc = self.filtered_df[s].diff() / self.filtered_df["Time"].diff().dt.total_seconds()
            roc.iloc[0] = 0
            all_roc.extend(roc[mask].dropna())
        if all_roc:
            self.ax_roc.set_ylim(min(all_roc), max(all_roc))

    # ================================================================
    # CURSOR / HOVER
    # ================================================================
    def update_cursor(self, event):
        if not event.inaxes or self.filtered_df is None:
            if hasattr(self, "hover_annotation"):
                self.hover_annotation.set_visible(False)
            self.canvas.draw_idle()
            return

        x = event.xdata
        if x is None: return
        x_dt  = mdates.num2date(x)
        x_str = x_dt.strftime("%Y-%m-%d %H:%M:%S")
        self.vline_main.set_xdata([x])
        self.vline_roc.set_xdata([x])
        self.vline_main.set_visible(True)
        self.vline_roc.set_visible(True)

        for m in self.highlight_markers:
            try: m.remove()
            except: pass
        self.highlight_markers.clear()
        if hasattr(self, "hover_annotation"):
            try: self.hover_annotation.remove()
            except: pass

        times = mdates.date2num(self.filtered_df["Time"].to_numpy())
        idx   = bisect.bisect_left(times, x)
        idx   = min(max(idx, 1), len(times) - 1)

        tooltip_lines = []
        y_val = 0
        xlim  = self.ax_main.get_xlim()
        mask  = (times >= xlim[0]) & (times <= xlim[1])

        for s in self.signal_axis_map:
            y_val = self.filtered_df[s].iloc[idx]
            dt    = (self.filtered_df["Time"].iloc[idx] - self.filtered_df["Time"].iloc[idx - 1]).total_seconds()
            roc   = 0 if dt == 0 else (self.filtered_df[s].iloc[idx] - self.filtered_df[s].iloc[idx - 1]) / dt

            m1, = self.ax_main.plot(self.filtered_df["Time"].iloc[idx], y_val, 'o', color="yellow", markersize=8, zorder=5)
            m2, = self.ax_roc.plot(self.filtered_df["Time"].iloc[idx],  roc,   'o', color="yellow", markersize=8, zorder=5)
            self.highlight_markers.extend([m1, m2])

            vals  = self.filtered_df[s][mask]
            vmin  = vals.min()
            vmax  = vals.max()
            vmean = vals.mean()
            vstd  = vals.std()

            tooltip_lines.append(
                f"{s}:\nTime={self.filtered_df['Time'].iloc[idx]}\n"
                f"y={y_val:.4f}  ROC={roc:.4f}/s\n"
                f"Min={vmin:.4f}  Max={vmax:.4f}  Mean={vmean:.4f}  Std={vstd:.4f}"
            )

        if tooltip_lines:
            tooltip = "\n\n".join(tooltip_lines)
            figw, figh = self.fig.get_size_inches() * self.fig.dpi
            offx, offy = 15, 15
            if event.x > figw * 0.7: offx = -120
            if event.y > figh * 0.7: offy = -60
            self.hover_annotation = self.ax_main.annotate(
                tooltip,
                xy=(self.filtered_df["Time"].iloc[idx], y_val),
                xytext=(offx, offy),
                textcoords="offset points",
                bbox=dict(boxstyle="round", fc="yellow", alpha=0.9),
                arrowprops=dict(arrowstyle="->")
            )
            self.coord_label.config(text=f"(x={x_str})")

        self.update_stats_label()
        self.canvas.draw_idle()

    def on_mouse_leave(self, event):
        if hasattr(self, "hover_annotation"):
            self.hover_annotation.set_visible(False)
        self.canvas.draw_idle()

    # ================================================================
    # STATS LABEL
    # ================================================================
    def update_stats_label(self):
        if self.filtered_df is None or not self.signal_axis_map:
            self.stats_label.config(text="")
            return

        xlim = self.ax_main.get_xlim()
        mask = (mdates.date2num(self.filtered_df["Time"].to_numpy()) >= xlim[0]) & \
               (mdates.date2num(self.filtered_df["Time"].to_numpy()) <= xlim[1])

        stats = []
        for s in self.signal_axis_map:
            vals = self.filtered_df[s][mask]
            if len(vals) == 0: continue
            stats.append(
                f"{s}: Min={vals.min():.4f}  Max={vals.max():.4f}  "
                f"Mean={vals.mean():.4f}  Median={vals.median():.4f}  Std={vals.std():.4f}"
            )

        if not stats:
            self.stats_label.config(text="")
            return

        cols = 3
        rows = math.ceil(len(stats) / cols)
        grid = [[""] * cols for _ in range(rows)]
        for i, txt in enumerate(stats):
            grid[i % rows][i // rows] = txt

        lines = []
        for r in grid:
            line = "     ".join(f"{x:<40}" for x in r if x)
            lines.append(line)

        self.stats_label.config(text="\n".join(lines))

    # ================================================================
    # ZOOM / PAN / RESET
    # ================================================================
    def zoom(self, event):
        if self.filtered_df is None: return
        factor = 0.15
        if event.inaxes == self.ax_main:
            x = event.xdata
            if x is None: return
            left, right = self.ax_main.get_xlim()
            scale = (1 - factor) if event.button == "up" else (1 + factor)
            new_left  = x - (x - left)  * scale
            new_right = x + (right - x) * scale
            xmin = mdates.date2num(self.filtered_df["Time"].min())
            xmax = mdates.date2num(self.filtered_df["Time"].max())
            new_left  = max(new_left,  xmin)
            new_right = min(new_right, xmax)
            self.ax_main.set_xlim(new_left, new_right)
            self.ax_roc.set_xlim(new_left,  new_right)
            self.update_time_entries()
            self.auto_adjust_yaxis()
            self.update_stats_label()
        self.canvas.draw_idle()

    def start_pan(self, event):
        if event.button == 1:
            self._dragging = True
            self._last_drag_x = event.xdata

    def stop_pan(self, event):
        self._dragging = False
        self._last_drag_x = None

    def pan(self, event):
        if not self._dragging or event.xdata is None: return
        dx = self._last_drag_x - event.xdata
        left, right = self.ax_main.get_xlim()
        self.ax_main.set_xlim(left + dx, right + dx)
        self.ax_roc.set_xlim(left + dx, right + dx)
        self._last_drag_x = event.xdata
        self.update_time_entries()
        self.auto_adjust_yaxis()
        self.update_stats_label()
        self.canvas.draw_idle()

    def update_time_entries(self):
        left, right = self.ax_main.get_xlim()
        dtimes = mdates.num2date([left, right])
        self.start_date.set_date(dtimes[0].date())
        self.start_time.delete(0, "end")
        self.start_time.insert(0, dtimes[0].strftime("%H:%M:%S"))
        self.end_date.set_date(dtimes[1].date())
        self.end_time.delete(0, "end")
        self.end_time.insert(0, dtimes[1].strftime("%H:%M:%S"))

    def reset_x(self):
        if self.filtered_df is None: return
        xmin = self.filtered_df["Time"].min()
        xmax = self.filtered_df["Time"].max()
        self.ax_main.set_xlim(xmin, xmax)
        self.ax_roc.set_xlim(xmin, xmax)
        self.update_time_entries()
        self.auto_adjust_yaxis()
        self.update_stats_label()
        self.canvas.draw_idle()

    # ================================================================
    # EXPORT / SCREENSHOT
    # ================================================================
    def export_csv(self):
        if self.filtered_df is None: return
        path = filedialog.asksaveasfilename(defaultextension=".csv")
        if path: self.filtered_df.to_csv(path, index=False)

    def save_screenshot(self, event=None):
        if self.last_loaded_file is None: return
        folder = os.path.dirname(self.last_loaded_file)
        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        path   = os.path.join(folder, f"trend_capture_{ts}.png")
        self.fig.savefig(path, dpi=300)
        print("Saved:", path)


# ---------------- MAIN ----------------
if __name__ == "__main__":
    root = TkinterDnD.Tk()
    app  = TrendViewer(root)
    root.mainloop()
