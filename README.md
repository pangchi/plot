# Trend Viewer

This application allows you to visualize and analyze time-series data from CSV files. It provides features for:

*   **Loading Data:** Drag and drop CSV files directly into the application.
*   **Time Filtering:** Select specific date and time ranges to focus on.
*   **Signal Visualization:** Plot multiple signals on primary and secondary Y-axes.
*   **Rate of Change (ROC):** View the rate of change for each signal.
*   **Derived Signals:** Create new signals by applying mathematical expressions to existing ones.
*   **Zooming and Panning:** Interact with the plots to explore data in detail.
*   **Statistics:** View min, max, mean, median, and standard deviation for selected signals within the current view.
*   **Fourier Transform (FFT):** Analyze the frequency components of signals within the selected time range.
*   **Exporting:** Save the filtered data as a CSV file and export FFT results.
*   **Screenshots:** Capture the current plot as a PNG image.

## Features

### Data Loading

Drag and drop a CSV file into the designated area. The file must contain a 'Time' column that can be parsed into datetime objects. Other columns will be treated as signals.

### Time Controls

*   **Start/End Date & Time:** Use the `DateEntry` widgets and time input fields to define the desired time window.
*   **Apply:** Updates the displayed data based on the selected time range.
*   **Export:** Saves the currently filtered data to a new CSV file.
*   **Reset X:** Resets the X-axis to display the full time range of the loaded data.
*   **FFT:** Opens a new window to perform a Fast Fourier Transform on the currently visible data.

### Signal Management

*   **Search:** Filter the list of available signals by typing in the search box.
*   **Add New Signal:** Define new signals using mathematical expressions in the "New Signal =" field.
    *   Wrap column names with spaces in backticks (e.g., `` `Column Name` ``).
    *   Supported operators: `+`, `-`, `*`, `/`, `**` (power), `()`.
    *   Supported functions: `abs`, `sqrt`, `log`, `log10`, `exp`, `sin`, `cos`, `tan`, `min`, `max`, `mean`, `std`, `diff`, `rolling_mean`, `rolling_std`, `cumsum`.
    *   Constants: `pi`, `e`.
    *   Press Enter in the expression or name field to add the signal.
*   **Signal Buttons:** Click a signal's name to toggle its visibility on the plot. A sunken button indicates the signal is currently displayed.
*   **Axis Toggle:** Click the `[L]` or `[R]` button next to a signal name to switch it between the left (primary) and right (secondary) Y-axis.
*   **Remove Derived Signal:** Right-click on a derived signal's button to remove it.

### Plot Interaction

*   **Zoom:** Use the mouse scroll wheel while hovering over the plot to zoom in or out.
*   **Pan:** Click and drag with the left mouse button to pan the plot. A rectangle will show the selected region.
*   **Hover:** Moving the mouse cursor over the plot will display a tooltip with detailed information about the signals at that specific time point, including value, rate of change, min/max/mean/std within the view, and the Y-axis side.
*   **Screenshot:** Press the `Print Screen` key to save the current plot as a PNG file in the same directory as the loaded CSV.

### FFT Analysis

The FFT window allows you to analyze the frequency content of the signals within the currently zoomed view.

*   **Window:** Apply different windowing functions (none, Hann, Hamming, Blackman, Flattop) to reduce spectral leakage.
*   **Scale:** Choose between linear or logarithmic scaling for the Y-axis.
*   **Y-axis Mode:** Display the results as Amplitude, Power, or Magnitude in decibels (dB).
*   **Peaks:** Highlight the top N most prominent frequency peaks.
*   **Export FFT CSV:** Save the calculated frequency and amplitude data to a CSV file.

## Requirements

*   Python 3.7+
*   `pandas`
*   `matplotlib`
*   `numpy`
*   `tkinterdnd2`
*   `tkcalendar`

The script will attempt to automatically install these dependencies if they are not found.

## Usage

1.  Run the script: `python your_script_name.py`
2.  Drag and drop a CSV file containing a 'Time' column and signal data into the application window.
3.  Use the time controls to filter the data.
4.  Click on signal names to display them on the plot.
5.  Use the `[L]` / `[R]` buttons to assign signals to the left or right Y-axis.
6.  Interact with the plot using the mouse for zooming and panning.
7.  Create derived signals using the expression builder.
8.  Click the "FFT" button to analyze frequency components.
```
