"""
WaveformFrame — persistent wx.Frame showing transient analysis traces.

Usage:
    frame = WaveformFrame(parent, traces, on_probe_toggle=cb)
    frame.Show()
    # later:
    frame.toggle_net('VCC')   # called when canvas probe-clicks a net
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

import wx

from .model.simulation import TransientTrace


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r, g, b


_TRACE_COLORS = [
    '#e8c020', '#20b060', '#4080e0', '#e04040',
    '#c040c0', '#40c0c0', '#e08020', '#80a080',
    '#a0e040', '#e060a0', '#60a0e0', '#e0a060',
]


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
    _MARGIN = (55, 16, 36, 52)   # top, right, bottom, left

    def __init__(self, parent,
                 traces: Dict[str, TransientTrace],
                 net_colors: Dict[str, str],
                 visible: Dict[str, bool]):
        super().__init__(parent)
        self.SetBackgroundColour(wx.Colour(18, 18, 28))
        self._traces  = traces
        self._colors  = net_colors   # shared ref — updated by WaveformFrame
        self._visible = visible       # shared ref — updated by WaveformFrame
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE,  lambda _: self.Refresh())

    def set_visibility(self, net: str, on: bool) -> None:
        self._visible[net] = on
        self.Refresh()

    def _on_paint(self, _evt) -> None:
        dc = wx.BufferedPaintDC(self)
        w, h = self.GetClientSize()
        mt, mr, mb, ml = self._MARGIN
        pw, ph = w - ml - mr, h - mt - mb

        dc.SetBackground(wx.Brush(wx.Colour(18, 18, 28)))
        dc.Clear()

        if pw < 40 or ph < 30 or not self._traces:
            return

        active = {n: t for n, t in self._traces.items()
                  if self._visible.get(n) and t.times}
        if not active:
            dc.SetTextForeground(wx.Colour(80, 80, 100))
            dc.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_ITALIC,
                               wx.FONTWEIGHT_NORMAL))
            msg = 'Click the Probe button, then click a net on the breadboard'
            tw, th2 = dc.GetTextExtent(msg)
            dc.DrawText(msg, (w - tw) // 2, (h - th2) // 2)
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
            return ml + int((t * t_scale) / t_max_s * pw)

        def ty(v: float) -> int:
            if v_max == v_min:
                return mt + ph // 2
            return mt + ph - int((v - v_min) / (v_max - v_min) * ph)

        # Grid
        dc.SetPen(wx.Pen(wx.Colour(45, 45, 62), 1))
        t_ticks = _nice_ticks(0, t_max_s, 6)
        v_ticks = _nice_ticks(v_min, v_max, 5)
        for tt in t_ticks:
            x = ml + int(tt / t_max_s * pw) if t_max_s else ml
            dc.DrawLine(x, mt, x, mt + ph)
        for vt in v_ticks:
            y = ty(vt)
            dc.DrawLine(ml, y, ml + pw, y)

        # Axes
        dc.SetPen(wx.Pen(wx.Colour(100, 100, 120), 1))
        dc.DrawLine(ml, mt, ml, mt + ph)
        dc.DrawLine(ml, mt + ph, ml + pw, mt + ph)

        # Axis labels
        dc.SetTextForeground(wx.Colour(160, 160, 180))
        font = wx.Font(7, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        dc.SetFont(font)

        for tt in t_ticks:
            x = ml + int(tt / t_max_s * pw) if t_max_s else ml
            label = f'{tt:.4g}'
            tw2 = dc.GetTextExtent(label)[0] // 2
            dc.DrawText(label, x - tw2, mt + ph + 4)

        unit_lbl = f'time ({t_unit})'
        uw = dc.GetTextExtent(unit_lbl)[0]
        dc.DrawText(unit_lbl, ml + pw // 2 - uw // 2, mt + ph + 18)

        for vt in v_ticks:
            y = ty(vt)
            label = f'{vt:.4g}'
            tw = dc.GetTextExtent(label)[0]
            dc.DrawText(label, ml - tw - 4, y - 7)

        dc.DrawRotatedText('V', 10, mt + ph // 2 + 5, 90)

        # Traces
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
    def __init__(self, parent,
                 net_names: List[str],
                 net_colors: Dict[str, str],
                 visible: Dict[str, bool],
                 on_toggle: Callable):
        super().__init__(parent, style=wx.VSCROLL)
        self.SetBackgroundColour(wx.Colour(28, 28, 40))
        self.SetScrollRate(0, 10)
        self._checkboxes: Dict[str, wx.CheckBox] = {}

        sizer = wx.BoxSizer(wx.VERTICAL)
        lbl = wx.StaticText(self, label='Traces')
        lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        lbl.SetForegroundColour(wx.Colour(160, 160, 180))
        sizer.Add(lbl, 0, wx.ALL, 6)

        for net in net_names:
            cb = wx.CheckBox(self, label=net)
            cb.SetValue(visible.get(net, False))
            cb.SetForegroundColour(wx.Colour(*_hex_to_rgb(net_colors.get(net, '#888888'))))
            cb.SetFont(wx.Font(8, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            cb.SetBackgroundColour(wx.Colour(28, 28, 40))
            cb.Bind(wx.EVT_CHECKBOX, lambda e, n=net: on_toggle(n, e.IsChecked()))
            sizer.Add(cb, 0, wx.LEFT | wx.BOTTOM, 6)
            self._checkboxes[net] = cb

        self.SetSizer(sizer)
        self.FitInside()
        self.SetMinSize(wx.Size(160, -1))

    def set_checked(self, net_name: str, value: bool) -> None:
        cb = self._checkboxes.get(net_name)
        if cb:
            cb.SetValue(value)


# ---------------------------------------------------------------------------
# Main WaveformFrame
# ---------------------------------------------------------------------------

class WaveformFrame(wx.Frame):
    """Persistent waveform viewer. Call toggle_net() to add/remove traces."""

    def __init__(self, parent,
                 traces: Dict[str, TransientTrace],
                 on_probe_toggle: Optional[Callable[[bool], None]] = None):
        super().__init__(parent, title='Transient Analysis',
                         size=(900, 520),
                         style=wx.DEFAULT_FRAME_STYLE)
        self._traces          = traces
        self._on_probe_toggle = on_probe_toggle
        self._probe_active    = False

        # Assign a fixed color to every net upfront
        self._net_colors: Dict[str, str] = {}
        for i, name in enumerate(sorted(traces)):
            self._net_colors[name] = _TRACE_COLORS[i % len(_TRACE_COLORS)]

        # All traces start hidden
        self._visible: Dict[str, bool] = {n: False for n in traces}

        self._build()
        self.SetIcon(wx.NullIcon)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    # ------------------------------------------------------------------
    # Public API

    def toggle_net(self, net_name: str) -> None:
        """Show this net's trace if hidden, hide it if shown."""
        if net_name not in self._traces:
            return
        new_state = not self._visible.get(net_name, False)
        self._visible[net_name] = new_state
        self._wave_panel.set_visibility(net_name, new_state)
        self._selector.set_checked(net_name, new_state)

    # ------------------------------------------------------------------

    def _build(self) -> None:
        net_names = sorted(self._traces)

        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.Colour(18, 18, 28))

        # Toolbar row
        toolbar = wx.Panel(panel)
        toolbar.SetBackgroundColour(wx.Colour(28, 28, 40))
        tb_sz = wx.BoxSizer(wx.HORIZONTAL)

        self._probe_btn = wx.ToggleButton(toolbar, label='⦿  Probe')
        self._probe_btn.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                        wx.FONTWEIGHT_BOLD))
        self._probe_btn.SetToolTip(
            'Click nets on the breadboard to add their traces here')
        self._probe_btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_probe_btn)

        clear_btn = wx.Button(toolbar, label='Clear all')
        clear_btn.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_NORMAL))
        clear_btn.Bind(wx.EVT_BUTTON, self._on_clear_all)

        info = wx.StaticText(toolbar, label='')
        self._info_lbl = info
        info.SetForegroundColour(wx.Colour(120, 120, 140))
        info.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_ITALIC,
                             wx.FONTWEIGHT_NORMAL))

        if self._traces:
            t_total = max(max(tr.times) for tr in self._traces.values() if tr.times)
            t_scale, t_unit = _time_scale(t_total)
            info.SetLabel(f'{len(self._traces)} nets  ·  {t_total * t_scale:.4g} {t_unit}')

        tb_sz.Add(self._probe_btn, 0, wx.ALIGN_CENTRE_VERTICAL | wx.ALL, 6)
        tb_sz.Add(clear_btn,      0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        tb_sz.Add(info,           0, wx.ALIGN_CENTRE_VERTICAL)
        toolbar.SetSizer(tb_sz)

        # Splitter
        splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH)
        splitter.SetMinimumPaneSize(80)

        self._wave_panel = WaveformPanel(splitter, self._traces,
                                         self._net_colors, self._visible)
        self._selector   = TraceSelectorPanel(
            splitter, net_names, self._net_colors, self._visible,
            on_toggle=self._on_checkbox_toggle,
        )
        splitter.SplitVertically(self._selector, self._wave_panel, 170)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(toolbar,  0, wx.EXPAND)
        outer.Add(splitter, 1, wx.EXPAND | wx.ALL, 2)
        panel.SetSizer(outer)

        frame_sz = wx.BoxSizer(wx.VERTICAL)
        frame_sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(frame_sz)
        self.Layout()

    def _on_checkbox_toggle(self, net_name: str, checked: bool) -> None:
        self._visible[net_name] = checked
        self._wave_panel.set_visibility(net_name, checked)

    def _on_probe_btn(self, _evt) -> None:
        self._probe_active = self._probe_btn.GetValue()
        if self._on_probe_toggle:
            self._on_probe_toggle(self._probe_active)

    def _on_clear_all(self, _evt) -> None:
        for net in self._visible:
            self._visible[net] = False
        self._wave_panel.Refresh()
        for net, cb in self._selector._checkboxes.items():
            cb.SetValue(False)

    def deactivate_probe(self) -> None:
        """Called by window when probe mode is ended externally (e.g. Escape)."""
        self._probe_active = False
        self._probe_btn.SetValue(False)

    def _on_close(self, evt) -> None:
        if self._probe_active and self._on_probe_toggle:
            self._on_probe_toggle(False)
        evt.Skip()
