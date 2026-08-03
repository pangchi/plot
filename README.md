# TrendViewer

A desktop time-series visualisation tool for CSV data. Load a file, select signals, zoom and pan interactively, overlay moving averages and standard deviations, build derived signals with formulas, and analyse frequency content with a built-in FFT.

## Requirements

Python 3.8 or later. All dependencies are installed automatically on first run:

| Package | Purpose |
|---|---|
| `pandas` | Data loading and time-series operations |
| `matplotlib` | Plotting and interactive canvas |
| `numpy` | Numerical computation |
| `tkinterdnd2` | Drag-and-drop CSV loading |
| `tkcalendar` | Date picker widgets |

`scipy` is optional — used for improved FFT peak detection and the `flattop` window. The FFT works without it, falling back to NumPy.

## Running

```bash
python trendviewer.py
```

## CSV Format

The file must have a `Time` column. Two formats are supported:

- **Calendar timestamps** — parsed with `pandas.to_datetime` (e.g. `2024-01-15 08:00:00`). Timezone-aware timestamps are converted to local time on load.
- **Elapsed seconds** — if every value in `Time` is numeric and the column starts at `0`, it's treated as seconds elapsed rather than a calendar date. The chart axes, crosshair, and tooltip then show elapsed time (`0s`, `12.5s`, `1:02:03`) instead of a date.

All other columns are treated as numeric signals.

```
Time,Temperature,Pressure,FlowRate
2024-01-15 08:00:00,22.3,1013.2,4.7
2024-01-15 08:00:01,22.4,1013.1,4.8
...
```

```
Time,Temperature,Pressure
0,22.3,1013.2
1,22.4,1013.1
2,22.6,1013.0
...
```

---

## Interface Overview

### Loading Data

Drag a CSV file onto the **"Drag CSV here"** bar at the top, or modify the script to call `load_csv(path)` directly. The time range pickers are set automatically to the full extent of the data.

**Multiple files** can be loaded at once — either drop several CSVs together, or drop them in one at a time; each new drop is added to what's already loaded rather than replacing it. Files are aligned and merged on the `Time` column (an outer join, so mismatched timestamps just leave gaps rather than failing). If two files share a column name, every file after the first has its filename prefixed onto the clashing column (e.g. `sensorB_Temp`); columns unique to a file keep their plain name.

Elapsed-time mode only applies when **every** loaded file uses elapsed seconds — mixing an elapsed-time file with a calendar-timestamp file falls back to treating all `Time` values as absolute.

Loaded files are listed as chips under the drop bar; click the **×** on a chip to remove that file and re-merge the rest. Removing the last file clears the chart.

### Time Range Filter

Use the **Start** and **End** date/time pickers to narrow the visible time window, then click **Apply**. Currently active signals stay selected across filter changes. **Reset X** restores the full x-axis range without reloading.

**Export** saves the currently filtered data slice to a new CSV.

---

## Signals Panel

Every column in the CSV appears as a button in the **Signals** panel.

| Action | Gesture |
|---|---|
| Toggle signal on/off | Left-click the signal name button |
| Switch axis (Left ↔ Right) | Left-click the **[L]** / **[R]** button beside the signal name |
| Remove a derived / MA / MSD signal | Right-click the signal name button |

Active signals turn green. The **[L]** button is blue; **[R]** is red. The axis key legend below the MA/MSD row shows which is which.

**Search** — type in the Search box to filter the Signals panel in real time. Only buttons whose names contain the search text remain visible.

### Dual Y-Axis

Any signal can be assigned to the primary (left) or secondary (right) y-axis independently. The secondary axis is hidden until at least one signal is assigned to it.

---

## Moving Average and Moving Std Dev

The combined **Moving Avg / Moving Std** row lets you add rolling overlays on top of any signal.

### Controls (same layout for both halves)

| Control | Description |
|---|---|
| Signal entry | Searchable — type to filter, ↑↓ to navigate, Enter or click to select |
| **Win** d / h / m | Window duration in days, hours, and minutes |
| Summary badge | Shows the total window duration (e.g. `= 1d 6h`) |
| **Add MA** / **Add MSD** | Computes the overlay and adds it to the Signals panel |

The window is converted to a row count using the median sample interval of the loaded data, so the same duration works correctly regardless of sample rate.

### In the Signals Panel

MA and MSD overlays appear as coloured buttons in the Signals panel, identical to regular signals:

- **Toggle on/off** by clicking the button
- **Switch axis** with the [L]/[R] button
- **Remove** with right-click

**Chart style** — MA lines are long-dashed; MSD lines are short-dotted. Both use a fixed colour from a preset palette that cycles if more than eight overlays are added.

---

## Derived Signals

The **New Signal** row lets you compute custom signals from existing columns using an arithmetic expression.

### Syntax

```
expression   Name: my_signal   [Add]
```

- Column names are used directly: `Temperature`, `Pressure`
- Names containing spaces must be wrapped in backticks: `` `Oil Temp` ``
- Operators: `+ - * / ** ( )`
- The result is added as a new button in the Signals panel and persists across time filter changes

### Autocomplete

While typing in the expression box, a dropdown suggests matching column names and built-in functions. Navigate with ↑↓, accept with Tab or Enter.

### Available Functions

**Math**

| Function | Description |
|---|---|
| `abs(x)` | Absolute value |
| `sqrt(x)` | Square root |
| `log(x)` | Natural logarithm |
| `log10(x)` | Base-10 logarithm |
| `exp(x)` | Exponential |
| `sin(x)` `cos(x)` `tan(x)` | Trigonometric |
| `pi` `e` | Constants |

**Statistical**

| Function | Description |
|---|---|
| `mean(x)` | Mean of the entire series |
| `std(x)` | Standard deviation of the entire series |
| `min(x, y)` `max(x, y)` | Element-wise min/max of two arrays |

**Series (return an array)**

| Function | Description |
|---|---|
| `diff(x)` | Row-by-row difference |
| `rolling_mean(x, N)` | N-point rolling mean |
| `rolling_std(x, N)` | N-point rolling standard deviation |
| `cumsum(x)` | Cumulative sum |

### Examples

```
# Difference between two channels
Temperature - `Setpoint Temp`

# Normalised (z-score)
(Pressure - mean(Pressure)) / std(Pressure)

# 10-sample smoothed flow
rolling_mean(FlowRate, 10)

# Power from voltage and current
Voltage * Current

# Rate of change approximation
diff(Temperature) / diff(Time)   ← use the ROC subplot instead
```

---

## Chart Interaction

### Navigation

| Action | Gesture |
|---|---|
| Zoom in / out on x-axis | Scroll wheel |
| Rubber-band zoom (x and/or y) | Click and drag |
| Reset x-axis to full range | **Reset X** button |

Rubber-band zoom: drag a rectangle to zoom both axes simultaneously. If you drag only horizontally (no vertical movement), the y-axis auto-adjusts to the data in the new view. If you include a vertical drag, the y-axis is set to exactly the height you drew.

### Cursor and Tooltip

Moving the mouse over the chart:

- A vertical crosshair tracks the cursor across both the main plot and the ROC subplot
- A tooltip shows the current value, rate of change, and visible-range statistics (min, max, mean, std) for every active signal
- Yellow dots mark the nearest data point on each active signal

### Stats Bar

The bar below the chart permanently shows min, max, mean, median, and std for every active signal within the current x-axis view.

### Rate of Change (ROC) Subplot

The lower subplot shows the per-second derivative of every active signal, computed as `Δvalue / Δtime`. It shares the x-axis with the main plot and zooms together.

---

## FFT Analysis

Click the purple **FFT** button to open a frequency-domain analysis window for all currently active signals within the current x-axis view.

### Controls

| Control | Options |
|---|---|
| Window function | none, hann, hamming, blackman, flattop |
| X scale | linear, log |
| Y mode | amplitude, power, dB |
| Peaks | Number of dominant peaks to annotate (0 to disable) |

The title of each subplot shows the signal name, sample count, estimated sample rate, frequency resolution, time span, and window function used.

**Export FFT CSV** saves frequency, amplitude, and period for all active signals to a file.

---

## Screenshot

Press the **Print Screen** key while the app is focused to save a 300 dpi PNG of the current chart to the same folder as the loaded CSV file. The filename includes a timestamp: `trend_capture_YYYYMMDD_HHMMSS.png`.
