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
from tkinter import filedialog,messagebox
from tkinterdnd2 import TkinterDnD,DND_FILES
from tkcalendar import DateEntry
import pandas as pd
import numpy as np
import os
from datetime import datetime
import bisect

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg,NavigationToolbar2Tk
import matplotlib.dates as mdates

# ---------------- DOWNSAMPLE HELPER ----------------
def downsample_indices(n, max_points=5000):
    if n <= max_points:
        return np.arange(n)
    step = max(1, int(n / max_points))
    return np.arange(0, n, step)

# ---------------- TREND VIEWER ----------------
class TrendViewer:

    def __init__(self,root):
        self.root=root
        self.root.title("Trend Viewer")
        self.root.geometry("1400x1050")

        self.df=None
        self.filtered_df=None
        self.signal_axis_map={}
        self.highlight_markers=[]
        self.last_loaded_file=None

        self._dragging=False
        self._last_drag_x=None

        root.bind("<Print>",self.save_screenshot)

        # -------- Drop area --------
        drop=tk.Label(root,text="Drag CSV here",bg="lightgray",height=2)
        drop.pack(fill="x")
        drop.drop_target_register(DND_FILES)
        drop.dnd_bind("<<Drop>>",self.load_csv_dnd)

        # -------- Time controls --------
        f=tk.Frame(root)
        f.pack(pady=4)
        tk.Label(f,text="Start").grid(row=0,column=0)
        self.start_date=DateEntry(f,date_pattern="yyyy-mm-dd")
        self.start_date.grid(row=0,column=1)
        self.start_time=tk.Entry(f,width=8)
        self.start_time.insert(0,"00:00:00")
        self.start_time.grid(row=0,column=2)

        tk.Label(f,text="End").grid(row=0,column=3)
        self.end_date=DateEntry(f,date_pattern="yyyy-mm-dd")
        self.end_date.grid(row=0,column=4)
        self.end_time=tk.Entry(f,width=8)
        self.end_time.insert(0,"23:59:59")
        self.end_time.grid(row=0,column=5)

        tk.Button(f,text="Apply",command=self.apply_time_filter).grid(row=0,column=6,padx=5)
        tk.Button(f,text="Export",command=self.export_csv).grid(row=0,column=7,padx=5)
        tk.Button(f,text="Reset X",command=self.reset_x).grid(row=0,column=8,padx=5)

        # -------- Signal buttons --------
        self.signal_frame=tk.LabelFrame(root,text="Signals")
        self.signal_frame.pack(fill="x",pady=4)

        # -------- Matplotlib figure --------
        self.fig,(self.ax_main,self.ax_roc)=plt.subplots(
            2,1,sharex=True,
            gridspec_kw={"height_ratios":[3,1]},
            figsize=(12,8)
        )
        self.ax_main.set_title("Signals")
        self.ax_roc.set_title("Rate of Change")

        self.canvas=FigureCanvasTkAgg(self.fig,master=root)
        self.canvas.get_tk_widget().pack(fill="both",expand=True)
        self.toolbar=NavigationToolbar2Tk(self.canvas,root)
        self.toolbar.update()

        self.vline_main=self.ax_main.axvline(0,color="gray",linestyle="--",visible=False)
        self.vline_roc=self.ax_roc.axvline(0,color="gray",linestyle="--",visible=False)

        self.coord_label=tk.Label(root,text="",anchor="w")
        self.coord_label.pack(fill="x")

        # -------- Stats label at bottom --------
        self.stats_label=tk.Label(root,text="",anchor="w",bg="#f0f0f0",justify="left")
        self.stats_label.pack(fill="x")

        self.ax_main.format_coord=lambda x,y:""
        self.ax_roc.format_coord=lambda x,y:""

        # -------- Events --------
        self.canvas.mpl_connect("motion_notify_event",self.update_cursor)
        self.canvas.mpl_connect("scroll_event",self.zoom)
        self.canvas.mpl_connect("button_press_event",self.start_pan)
        self.canvas.mpl_connect("button_release_event",self.stop_pan)
        self.canvas.mpl_connect("motion_notify_event",self.pan)
        self.canvas.mpl_connect("figure_leave_event", self.on_mouse_leave)

    # ---------------- CSV LOAD ----------------
    def load_csv_dnd(self,event):
        path=event.data.strip("{}")
        self.load_csv(path)

    def load_csv(self,path):
        try:
            self.df=pd.read_csv(path)
            self.df["Time"]=pd.to_datetime(self.df["Time"])
        except Exception as e:
            messagebox.showerror("Error",str(e))
            return

        self.last_loaded_file=path

        for w in self.signal_frame.winfo_children():
            w.destroy()

        max_per_row = 12
        row = 0
        col = 0

        for c in self.df.columns:
            if c != "Time":
                b = tk.Label(
                    self.signal_frame,
                    text=c,
                    bg="white",
                    relief="raised",
                    padx=6,
                    pady=3
                )
                b.grid(row=row, column=col, padx=3, pady=3, sticky="w")
                b.bind("<Button-1>", self.toggle_signal)
                col += 1
                if col >= max_per_row:
                    col = 0
                    row += 1

        tmin=self.df["Time"].min()
        tmax=self.df["Time"].max()
        self.start_date.set_date(tmin.date())
        self.start_time.delete(0,"end")
        self.start_time.insert(0,tmin.strftime("%H:%M:%S"))
        self.end_date.set_date(tmax.date())
        self.end_time.delete(0,"end")
        self.end_time.insert(0,tmax.strftime("%H:%M:%S"))

        self.apply_time_filter()

    # ---------------- FILTER ----------------
    def apply_time_filter(self):
        if self.df is None:return
        start=pd.to_datetime(f"{self.start_date.get()} {self.start_time.get()}")
        end=pd.to_datetime(f"{self.end_date.get()} {self.end_time.get()}")
        mask=(self.df["Time"]>=start)&(self.df["Time"]<=end)
        self.filtered_df=self.df.loc[mask]
        self.reset_plot()

    # ---------------- RESET ----------------
    def reset_plot(self):
        self.ax_main.clear()
        self.ax_roc.clear()
        self.ax_main.set_title("Signals")
        self.ax_roc.set_title("Rate of Change")
        self.signal_axis_map.clear()
        self.vline_main=self.ax_main.axvline(0,color="gray",linestyle="--",visible=False)
        self.vline_roc=self.ax_roc.axvline(0,color="gray",linestyle="--",visible=False)
        self.reset_x()
        self.canvas.draw_idle()

    # ---------------- SIGNAL TOGGLE ----------------
    def toggle_signal(self,event):
        if self.filtered_df is None:return
        w=event.widget
        s=w.cget("text")
        if s in self.signal_axis_map:
            line,roc=self.signal_axis_map[s]
            line.remove()
            roc.remove()
            del self.signal_axis_map[s]
            w.config(relief="raised",bg="white",fg="black")
        else:
            n = len(self.filtered_df)
            indices = downsample_indices(n)
            x_data = self.filtered_df["Time"].iloc[indices]
            y_data = self.filtered_df[s].iloc[indices]
            line, = self.ax_main.plot(x_data, y_data, label=s)

            roc = self.filtered_df[s].diff()/self.filtered_df["Time"].diff().dt.total_seconds()
            roc.iloc[0]=0
            roc_ds = roc.iloc[indices]
            roc_line, = self.ax_roc.plot(x_data, roc_ds, linestyle="--", label=s)

            self.signal_axis_map[s]=(line,roc_line)
            w.config(relief="sunken",bg="#4CAF50",fg="white")

        self.ax_main.legend()
        self.ax_roc.legend()
        self.auto_adjust_yaxis()
        self.update_stats_label()
        self.canvas.draw_idle()

    # ---------------- AUTO Y-AXIS ----------------
    def auto_adjust_yaxis(self):
        if not self.signal_axis_map: return
        xlim = self.ax_main.get_xlim()
        mask = (mdates.date2num(self.filtered_df["Time"].to_numpy()) >= xlim[0]) & \
               (mdates.date2num(self.filtered_df["Time"].to_numpy()) <= xlim[1])

        all_vals=[]
        for s,_ in self.signal_axis_map.items():
            vals=self.filtered_df[s][mask]
            all_vals.extend(vals)
        if all_vals:
            self.ax_main.set_ylim(min(all_vals), max(all_vals))

        all_roc=[]
        for s,_ in self.signal_axis_map.items():
            roc = self.filtered_df[s].diff()/self.filtered_df["Time"].diff().dt.total_seconds()
            roc.iloc[0]=0
            vals = roc[mask]
            all_roc.extend(vals)
        if all_roc:
            self.ax_roc.set_ylim(min(all_roc), max(all_roc))

    # ---------------- CURSOR / HOVER ----------------
    def update_cursor(self,event):
        if not event.inaxes or self.filtered_df is None: 
            if hasattr(self,"hover_annotation"):
                self.hover_annotation.set_visible(False)
            self.canvas.draw_idle()
            return

        x=event.xdata
        if x is None: return
        x_dt = mdates.num2date(x)
        x_str = x_dt.strftime("%Y-%m-%d %H:%M:%S")
        self.vline_main.set_xdata([x])
        self.vline_roc.set_xdata([x])
        self.vline_main.set_visible(True)
        self.vline_roc.set_visible(True)

        for m in self.highlight_markers:
            try: m.remove()
            except: pass
        self.highlight_markers.clear()
        if hasattr(self,"hover_annotation"):
            try: self.hover_annotation.remove()
            except: pass

        times = mdates.date2num(self.filtered_df["Time"].to_numpy())
        idx = bisect.bisect_left(times, x)
        idx = min(max(idx,1), len(times)-1)

        tooltip_lines=[]
        y_val = 0
        xlim = self.ax_main.get_xlim()
        mask = (mdates.date2num(self.filtered_df["Time"].to_numpy()) >= xlim[0]) & \
               (mdates.date2num(self.filtered_df["Time"].to_numpy()) <= xlim[1])

        for s,_ in self.signal_axis_map.items():
            y_val = self.filtered_df[s].iloc[idx]
            dt = (self.filtered_df["Time"].iloc[idx]-self.filtered_df["Time"].iloc[idx-1]).total_seconds()
            roc = 0 if dt==0 else (self.filtered_df[s].iloc[idx]-self.filtered_df[s].iloc[idx-1])/dt

            m1,=self.ax_main.plot(self.filtered_df["Time"].iloc[idx],y_val,'o',color="yellow",markersize=8,zorder=5)
            m2,=self.ax_roc.plot(self.filtered_df["Time"].iloc[idx],roc,'o',color="yellow",markersize=8,zorder=5)
            self.highlight_markers.extend([m1,m2])

            vals = self.filtered_df[s][mask]
            vmin = vals.min()
            vmax = vals.max()
            vmean = vals.mean()
            vstd = vals.std()

            tooltip_lines.append(
                f"{s}:\nTime={self.filtered_df['Time'].iloc[idx]}\ny={y_val:.4f}\nROC={roc:.4f}/s\nMin={vmin:.4f} Max={vmax:.4f} Mean={vmean:.4f} StdDev={vstd:.4f}"
            )

        if tooltip_lines:
            tooltip="\n\n".join(tooltip_lines)
            figw,figh=self.fig.get_size_inches()*self.fig.dpi
            offx=15
            offy=15
            if event.x>figw*0.7: offx=-120
            if event.y>figh*0.7: offy=-60
            self.hover_annotation=self.ax_main.annotate(
                tooltip,
                xy=(self.filtered_df["Time"].iloc[idx],y_val),
                xytext=(offx,offy),
                textcoords="offset points",
                bbox=dict(boxstyle="round",fc="yellow",alpha=0.9),
                arrowprops=dict(arrowstyle="->")
            )
            self.coord_label.config(text=f"(x={x_str})")

        self.update_stats_label()
        self.canvas.draw_idle()

    # ---------------- HIDE HOVER ON LEAVE ----------------
    def on_mouse_leave(self,event):
        if hasattr(self,"hover_annotation"):
            self.hover_annotation.set_visible(False)
        self.canvas.draw_idle()

    # ---------------- UPDATE STATS LABEL ----------------
    def update_stats_label(self):
        if self.filtered_df is None or not self.signal_axis_map:
            self.stats_label.config(text="")
            return

        import math
        xlim = self.ax_main.get_xlim()

        mask = (mdates.date2num(self.filtered_df["Time"].to_numpy()) >= xlim[0]) & \
            (mdates.date2num(self.filtered_df["Time"].to_numpy()) <= xlim[1])

        stats = []

        for s,_ in self.signal_axis_map.items():
            vals = self.filtered_df[s][mask]
            if len(vals) == 0:
                continue

            stats.append(
                f"{s}: Min={vals.min():.4f}  Max={vals.max():.4f}  Mean={vals.mean():.4f} Median={vals.median():.4f} Std={vals.std():.4f}"
            )

        if not stats:
            self.stats_label.config(text="")
            return
    
        cols = 3   # number of columns
        rows = math.ceil(len(stats)/cols)

        grid = [[""]*cols for _ in range(rows)]

        for i,txt in enumerate(stats):
            r = i % rows
            c = i // rows
            grid[r][c] = txt

        lines=[]
        for r in grid:
            line = "     ".join(f"{x:<40}" for x in r if x)
            lines.append(line)

        self.stats_label.config(text="\n".join(lines))
        
    # ---------------- ZOOM / PAN / RESET ----------------
    def zoom(self,event):
        if self.filtered_df is None:return
        factor=0.15
        if event.inaxes==self.ax_main:
            x=event.xdata
            if x is None:return
            left,right=self.ax_main.get_xlim()
            scale=(1-factor) if event.button=="up" else (1+factor)
            new_left=x-(x-left)*scale
            new_right=x+(right-x)*scale
            xmin=mdates.date2num(self.filtered_df["Time"].min())
            xmax=mdates.date2num(self.filtered_df["Time"].max())
            new_left=max(new_left,xmin)
            new_right=min(new_right,xmax)
            self.ax_main.set_xlim(new_left,new_right)
            self.ax_roc.set_xlim(new_left,new_right)
            self.update_time_entries()
            self.auto_adjust_yaxis()
            self.update_stats_label()
        self.canvas.draw_idle()

    def start_pan(self,event):
        if event.button==1:
            self._dragging=True
            self._last_drag_x=event.xdata
    def stop_pan(self,event):
        self._dragging=False
        self._last_drag_x=None
    def pan(self,event):
        if not self._dragging or event.xdata is None:return
        dx=self._last_drag_x-event.xdata
        left,right=self.ax_main.get_xlim()
        self.ax_main.set_xlim(left+dx,right+dx)
        self.ax_roc.set_xlim(left+dx,right+dx)
        self._last_drag_x=event.xdata
        self.update_time_entries()
        self.auto_adjust_yaxis()
        self.update_stats_label()
        self.canvas.draw_idle()

    def update_time_entries(self):
        left,right=self.ax_main.get_xlim()
        dtimes = mdates.num2date([left,right])
        self.start_date.set_date(dtimes[0].date())
        self.start_time.delete(0,"end")
        self.start_time.insert(0,dtimes[0].strftime("%H:%M:%S"))
        self.end_date.set_date(dtimes[1].date())
        self.end_time.delete(0,"end")
        self.end_time.insert(0,dtimes[1].strftime("%H:%M:%S"))

    def reset_x(self):
        if self.filtered_df is None:return
        xmin=self.filtered_df["Time"].min()
        xmax=self.filtered_df["Time"].max()
        self.ax_main.set_xlim(xmin,xmax)
        self.ax_roc.set_xlim(xmin,xmax)
        self.update_time_entries()
        self.auto_adjust_yaxis()
        self.update_stats_label()
        self.canvas.draw_idle()

    # ---------------- EXPORT / SCREENSHOT ----------------
    def export_csv(self):
        if self.filtered_df is None:return
        path=filedialog.asksaveasfilename(defaultextension=".csv")
        if path: self.filtered_df.to_csv(path,index=False)

    def save_screenshot(self,event=None):
        if self.last_loaded_file is None:return
        folder=os.path.dirname(self.last_loaded_file)
        ts=datetime.now().strftime("%Y%m%d_%H%M%S")
        path=os.path.join(folder,f"trend_capture_{ts}.png")
        self.fig.savefig(path,dpi=300)
        print("Saved:",path)

# ---------------- MAIN ----------------
if __name__=="__main__":
    root=TkinterDnD.Tk()
    app=TrendViewer(root)
    root.mainloop()
