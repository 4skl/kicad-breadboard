"""
WaveformFrame — standalone wx.Frame that displays transient analysis traces.

Usage:
    WaveformFrame(parent, traces, probe_nets, probe_meta).Show()

    traces:     Dict[str, TransientTrace]   net_name → trace data
    probe_nets: Dict[str, str]              probe_name (e.g. 'CH1') → net_name
    probe_meta: Dict[str, dict]             from PROBE_META; each dict has 'color', 'label'
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import wx

from .model.simulation import TransientTrace


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r, g, b


_FALLBACK_COLORS = [
    '#e8c020', '#20b060', '#4080e0', '#e04040',
    '#c040c0', '#40c0c0', '#e08020', '#80a080',
]

# How to scale the time axis
def _time_scale(t_max: float) -> Tuple[float, str]:
    if t_max == 0:
        return 1.0, 's'
    if t_max < 1e-6:
        return 1e9, 'ns'
    if t_max < 1e-3:
        return 1e6, 'µs'
    if t_max < 1.0:
        return 1e3, 'ms'
    return 1.0, 's'


def _nice_ticks(lo: float, hi: float, n: int = 5) -> List[float]:
    """Return ~n evenly spaced 'nice' tick values between lo and hi."""
    span = hi - lo
    if span == 0:
        return [lo]
    raw_step = span / n
    mag = math.floor(math.log10(abs(raw_step)))
    step = 10 ** mag
    for nice in (1, 2, 5, 10):
        if nice * step >= raw_step:
            step = nice * step
            break
    start = math.ceil(lo / step) * step
    ticks = []
    v = start
    while v <= hi + 1e-10 * abs(hi):
        ticks.append(round(v, 12))
        v += step
    return ticks


# ---------------------------------------------------------------------------
# Waveform drawing panel
# ---------------------------------------------------------------------------

class WaveformPanel(wx.Panel):
    """Custom-drawn panel showing voltage-vs-time traces."""

    _MARGIN = (55, 16, 36, 52)   # top, right, bottom, left

    def __init__(self, parent,
                 traces: Dict[str, TransientTrace],
                 net_colors: Dict[str, str],
                 visible: Dict[str, bool]):
        super().__init__(parent)
        self.SetBackgroundColour(wx.Colour(18, 18, 28))
        self._traces   = traces
        self._colors   = net_colors
        self._visible  = visible    # net_name → bool (updated by legend panel)
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE,  lambda _: self.Refresh())

    def set_visibility(self, net: str, on: bool) -> None:
        self._visible[net] = on
        self.Refresh()

    # ------------------------------------------------------------------

    def _on_paint(self, _evt) -> None:
        dc = wx.BufferedPaintDC(self)
        w, h = self.GetClientSize()
        mt, mr, mb, ml = self._MARGIN
        pw, ph = w - ml - mr, h - mt - mb

        # Background
        dc.SetBackground(wx.Brush(wx.Colour(18, 18, 28)))
        dc.Clear()

        if pw < 40 or ph < 30 or not self._traces:
            return

        # --- Data range ---
        active = {n: t for n, t in self._traces.items() if self._visible.get(n, True) and t.times}
        if not active:
            return

        t_max = max(max(tr.times) for tr in active.values())
        t_min = 0.0

        all_vals = [v for tr in active.values() for v in tr.values]
        if not all_vals:
            return
        v_min = min(all_vals)
        v_max = max(all_vals)
        v_span = v_max - v_min or 1.0
        v_min -= v_span * 0.08
        v_max += v_span * 0.08

        t_scale, t_unit = _time_scale(t_max)
        t_max_s = t_max * t_scale

        def tx(t: float) -> int:
            if t_max <= t_min:
                return ml
            return ml + int((t * t_scale - 0.0) / t_max_s * pw)

        def ty(v: float) -> int:
            if v_max == v_min:
                return mt + ph // 2
            return mt + ph - int((v - v_min) / (v_max - v_min) * ph)

        # --- Grid ---
        dc.SetPen(wx.Pen(wx.Colour(45, 45, 62), 1))
        t_ticks = _nice_ticks(0, t_max_s, 6)
        v_ticks = _nice_ticks(v_min, v_max, 5)
        for tt in t_ticks:
            x = ml + int(tt / t_max_s * pw) if t_max_s else ml
            dc.DrawLine(x, mt, x, mt + ph)
        for vt in v_ticks:
            y = ty(vt)
            dc.DrawLine(ml, y, ml + pw, y)

        # --- Axes ---
        dc.SetPen(wx.Pen(wx.Colour(100, 100, 120), 1))
        dc.DrawLine(ml, mt, ml, mt + ph)
        dc.DrawLine(ml, mt + ph, ml + pw, mt + ph)

        # --- Axis labels ---
        dc.SetTextForeground(wx.Colour(160, 160, 180))
        font = wx.Font(7, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)

        # Time axis ticks
        for tt in t_ticks:
            x = ml + int(tt / t_max_s * pw) if t_max_s else ml
            label = f'{tt:.4g}'
            tw2 = dc.GetTextExtent(label)[0] // 2
            dc.DrawText(label, x - tw2, mt + ph + 4)

        # Time axis unit label
        unit_lbl = f'time ({t_unit})'
        uw = dc.GetTextExtent(unit_lbl)[0]
        dc.DrawText(unit_lbl, ml + pw // 2 - uw // 2, mt + ph + 18)

        # Voltage axis ticks
        for vt in v_ticks:
            y = ty(vt)
            label = f'{vt:.4g}'
            tw = dc.GetTextExtent(label)[0]
            dc.DrawText(label, ml - tw - 4, y - 7)

        # Y-axis label (rotated)
        vlbl = 'V'
        dc.DrawRotatedText(vlbl, 10, mt + ph // 2 + 5, 90)

        # --- Traces ---
        for net_name, trace in active.items():
            if not trace.times:
                continue
            color = self._colors.get(net_name, '#888888')
            r, g, b = _hex_to_rgb(color)
            dc.SetPen(wx.Pen(wx.Colour(r, g, b), 2))
            pts = [(tx(t), ty(v)) for t, v in zip(trace.times, trace.values)]
            if len(pts) >= 2:
                dc.DrawLines(pts)


# ---------------------------------------------------------------------------
# Trace selector sidebar
# ---------------------------------------------------------------------------

class TraceSelectorPanel(wx.ScrolledWindow):
    """Checkbox list for toggling individual traces."""

    def __init__(self, parent, net_names: List[str],
                 net_colors: Dict[str, str],
                 net_labels: Dict[str, str],
                 on_toggle):
        super().__init__(parent, style=wx.VSCROLL)
        self.SetBackgroundColour(wx.Colour(28, 28, 40))
        self.SetScrollRate(0, 10)

        sizer = wx.BoxSizer(wx.VERTICAL)
        lbl = wx.StaticText(self, label='Traces')
        lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        lbl.SetForegroundColour(wx.Colour(160, 160, 180))
        sizer.Add(lbl, 0, wx.ALL, 6)

        for net in net_names:
            cb = wx.CheckBox(self, label=net_labels.get(net, net))
            cb.SetValue(True)
            cb.SetForegroundColour(wx.Colour(*_hex_to_rgb(net_colors.get(net, '#888888'))))
            cb.SetFont(wx.Font(8, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            cb.SetBackgroundColour(wx.Colour(28, 28, 40))
            cb.Bind(wx.EVT_CHECKBOX, lambda e, n=net: on_toggle(n, e.IsChecked()))
            sizer.Add(cb, 0, wx.LEFT | wx.BOTTOM, 6)

        self.SetSizer(sizer)
        self.FitInside()
        self.SetMinSize(wx.Size(140, -1))


# ---------------------------------------------------------------------------
# Main WaveformFrame
# ---------------------------------------------------------------------------

class WaveformFrame(wx.Frame):
    """Standalone frame showing transient analysis results."""

    def __init__(self, parent,
                 traces: Dict[str, TransientTrace],
                 probe_nets: Dict[str, str],
                 probe_meta: Dict[str, dict]):
        super().__init__(parent, title='Transient Analysis',
                         size=(820, 500),
                         style=wx.DEFAULT_FRAME_STYLE)

        # Build net → color and net → label maps
        net_colors: Dict[str, str] = {}
        net_labels: Dict[str, str] = {}

        # Map probe assignments to colors
        net_to_probe: Dict[str, str] = {net: name for name, net in probe_nets.items() if net}
        color_idx = 0
        for net_name in traces:
            probe_name = net_to_probe.get(net_name)
            if probe_name and probe_name in probe_meta:
                net_colors[net_name] = probe_meta[probe_name]['color']
                net_labels[net_name] = f'{probe_meta[probe_name]["label"]}: {net_name}'
            else:
                net_colors[net_name] = _FALLBACK_COLORS[color_idx % len(_FALLBACK_COLORS)]
                net_labels[net_name] = net_name
                color_idx += 1

        net_names = list(traces.keys())
        visible = {n: True for n in net_names}

        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.Colour(18, 18, 28))

        splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH)
        splitter.SetMinimumPaneSize(80)

        self._wave_panel = WaveformPanel(splitter, traces, net_colors, visible)
        selector = TraceSelectorPanel(
            splitter, net_names, net_colors, net_labels,
            on_toggle=self._wave_panel.set_visibility,
        )

        splitter.SplitVertically(selector, self._wave_panel, 150)

        outer = wx.BoxSizer(wx.VERTICAL)

        # Info bar: show VSIN frequency for each source
        freqs = sorted({t.times[-1] - t.times[0] for t in traces.values() if t.times},
                       reverse=True)
        if freqs:
            t_total = max(t.times[-1] for t in traces.values() if t.times)
            _, t_unit = _time_scale(t_total)
            t_scale, _ = _time_scale(t_total)
            info_str = f'Simulation span: {t_total * t_scale:.4g} {t_unit}'
            info = wx.StaticText(panel, label=info_str)
            info.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            info.SetForegroundColour(wx.Colour(140, 140, 160))
            info.SetBackgroundColour(wx.Colour(18, 18, 28))
            outer.Add(info, 0, wx.LEFT | wx.TOP, 6)

        outer.Add(splitter, 1, wx.EXPAND | wx.ALL, 4)
        panel.SetSizer(outer)

        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(frame_sizer)

        self.SetIcon(wx.NullIcon)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Layout()

    def _on_close(self, evt) -> None:
        evt.Skip()
