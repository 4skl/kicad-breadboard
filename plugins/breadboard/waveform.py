"""
WaveformFrame — KiScope digital-storage oscilloscope UI for transient analysis.

Public API (unchanged from the outside):
    frame = WaveformFrame(parent, traces, on_probe_toggle=cb)
    frame.Show()
    frame.toggle_net('VCC')      # add/remove a trace
    frame.deactivate_probe()     # called when canvas exits probe mode externally
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

import wx

from .model.simulation import TransientTrace

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

_BODY     = wx.Colour(24, 24, 26)        # main front-panel metal
_BEZEL    = wx.Colour(10, 14, 11)        # bezel around CRT
_SCREEN   = wx.Colour(3, 14, 5)          # phosphor screen background
_GRID_MAJ = wx.Colour(12, 36, 15)        # major graticule lines
_GRID_CTR = wx.Colour(18, 54, 22)        # centre crosshair (brighter)
_BRAND    = wx.Colour(0, 218, 100)       # KiScope logo green
_AMBER    = wx.Colour(255, 175, 30)      # knob indicator / warm accent
_LED_GRN  = wx.Colour(0, 235, 80)        # green indicator LED
_DIM      = wx.Colour(85, 90, 85)        # engraved-label colour
_MED      = wx.Colour(155, 158, 155)     # secondary text
_KNOB_BD  = wx.Colour(44, 44, 47)        # knob body
_KNOB_HI  = wx.Colour(72, 72, 76)        # knob highlight (top-left glint)
_STRIP_BG = wx.Colour(18, 18, 20)        # channel strip + controls background

# Classic DSO channel colours (yellow first, like Tektronix)
_CH_COLORS = [
    '#f0e020', '#00ccff', '#ff7700', '#ff44ff',
    '#44ff88', '#ff4444', '#44ffdd', '#ffcc44',
    '#8844ff', '#ff88cc', '#88ff44', '#ff4488',
]

# TIME/DIV steps (seconds per division)
_T_DIVS: List[float] = [
    1e-9, 2e-9, 5e-9, 1e-8, 2e-8, 5e-8,
    1e-7, 2e-7, 5e-7, 1e-6, 2e-6, 5e-6,
    1e-5, 2e-5, 5e-5, 1e-4, 2e-4, 5e-4,
    1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2,
    0.1, 0.2, 0.5, 1.0, 2.0, 5.0,
]

# VOLTS/DIV steps
_V_DIVS: List[float] = [
    1e-3, 2e-3, 5e-3, 0.01, 0.02, 0.05,
    0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0,
]

_NH = 10   # horizontal (time) divisions
_NV = 8    # vertical   (volt) divisions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _fmt_eng(val: float, unit: str) -> str:
    """Format a value in engineering notation with SI prefix."""
    if val == 0:
        return f'0 {unit}'
    a = abs(val)
    if a < 1e-6:   return f'{val * 1e9:.3g} n{unit}'
    if a < 1e-3:   return f'{val * 1e6:.3g} µ{unit}'
    if a < 1.0:    return f'{val * 1e3:.3g} m{unit}'
    if a < 1000:   return f'{val:.3g} {unit}'
    return f'{val / 1e3:.3g} k{unit}'


def _best_idx(table: List[float], needed: float) -> int:
    for i, v in enumerate(table):
        if v >= needed:
            return i
    return len(table) - 1


# ---------------------------------------------------------------------------
# OscopeScreen — phosphor CRT display panel
# ---------------------------------------------------------------------------

class OscopeScreen(wx.Panel):
    """Custom-painted oscilloscope screen with phosphor glow and graticule."""

    _PAD = 10   # pixels between panel edge and graticule

    def __init__(self, parent,
                 traces:     Dict[str, TransientTrace],
                 net_colors: Dict[str, str],
                 visible:    Dict[str, bool]):
        super().__init__(parent)
        self.SetBackgroundColour(_SCREEN)
        self.SetMinSize(wx.Size(420, 280))
        self._traces   = traces
        self._colors   = net_colors   # shared ref
        self._visible  = visible      # shared ref
        self._t_div: Optional[float] = None   # None = auto
        self._v_div: Optional[float] = None
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE,  lambda _: self.Refresh())

    def set_visibility(self, net: str, on: bool) -> None:
        self._visible[net] = on
        self.Refresh()

    def set_t_div(self, v: float) -> None:
        self._t_div = v
        self.Refresh()

    def set_v_div(self, v: float) -> None:
        self._v_div = v
        self.Refresh()

    # ------------------------------------------------------------------

    def _on_paint(self, _evt) -> None:
        dc = wx.BufferedPaintDC(self)
        W, H = self.GetClientSize()
        p = self._PAD
        pw, ph = W - 2 * p, H - 2 * p   # plot area size

        dc.SetBackground(wx.Brush(_SCREEN))
        dc.Clear()
        if pw < 40 or ph < 30:
            return

        self._draw_graticule(dc, p, p, pw, ph)

        active = {n: t for n, t in self._traces.items()
                  if self._visible.get(n) and t.times}

        if not active:
            dc.SetTextForeground(wx.Colour(0, 55, 18))
            dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_ITALIC, wx.FONTWEIGHT_NORMAL))
            msg = 'Click  ⦿ PROBE  then click any net on the breadboard'
            tw, th = dc.GetTextExtent(msg)
            dc.DrawText(msg, (W - tw) // 2, (H - th) // 2)
            return

        t_max = max(max(tr.times) for tr in active.values())
        all_v  = [v for tr in active.values() for v in tr.values]
        v_min0, v_max0 = min(all_v), max(all_v)
        v_span = v_max0 - v_min0 or 1.0

        t_window = self._t_div * _NH if self._t_div else (t_max or 1e-3)

        if self._v_div:
            vc   = (v_max0 + v_min0) / 2
            vhalf = self._v_div * _NV / 2
            v_lo, v_hi = vc - vhalf, vc + vhalf
        else:
            pad  = v_span * 0.10
            v_lo = v_min0 - pad
            v_hi = v_max0 + pad

        def tx(t: float) -> float:
            return p + t / t_window * pw

        def ty(v: float) -> float:
            if v_hi == v_lo:
                return p + ph / 2
            return p + ph - (v - v_lo) / (v_hi - v_lo) * ph

        try:
            gc = wx.GraphicsContext.Create(dc)
        except Exception:
            gc = None

        for net, trace in active.items():
            pts = [(tx(t), ty(v))
                   for t, v in zip(trace.times, trace.values)
                   if p - 4 <= tx(t) <= p + pw + 4]
            if len(pts) < 2:
                continue
            r, g, b = _hex_to_rgb(self._colors.get(net, '#f0e020'))
            self._draw_phosphor(dc, gc, pts, r, g, b)

        # Corner readouts
        t_div_lbl = _fmt_eng(self._t_div or t_window / _NH, 's') + '/div'
        v_div_lbl = _fmt_eng(self._v_div or (v_hi - v_lo) / _NV, 'V') + '/div'
        dc.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(wx.Colour(0, 148, 52))
        dc.DrawText(t_div_lbl, p + 4, p + ph - 14)
        tw = dc.GetTextExtent(v_div_lbl)[0]
        dc.DrawText(v_div_lbl, p + pw - tw - 4, p + ph - 14)

    def _draw_graticule(self, dc: wx.DC, ox: int, oy: int, pw: int, ph: int) -> None:
        # Major grid lines
        dc.SetPen(wx.Pen(_GRID_MAJ, 1))
        for i in range(_NH + 1):
            x = ox + round(i * pw / _NH)
            dc.DrawLine(x, oy, x, oy + ph)
        for i in range(_NV + 1):
            y = oy + round(i * ph / _NV)
            dc.DrawLine(ox, y, ox + pw, y)

        # Centre crosshair (brighter)
        dc.SetPen(wx.Pen(_GRID_CTR, 1))
        cx = ox + pw // 2
        cy = oy + ph // 2
        dc.DrawLine(cx, oy, cx, oy + ph)
        dc.DrawLine(ox, cy, ox + pw, cy)

        # Minor subdivision ticks along centre axes (5 per major div)
        tk = 4
        for i in range(_NH * 5 + 1):
            x = ox + round(i * pw / (_NH * 5))
            dc.DrawLine(x, cy - tk, x, cy + tk)
        for i in range(_NV * 5 + 1):
            y = oy + round(i * ph / (_NV * 5))
            dc.DrawLine(cx - tk, y, cx + tk, y)

        # Border
        dc.SetPen(wx.Pen(_GRID_CTR, 1))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.DrawRectangle(ox, oy, pw, ph)

    def _draw_phosphor(self, dc: wx.DC, gc, pts: list,
                       r: int, g: int, b: int) -> None:
        """Three-pass phosphor glow: wide/dim → medium → bright core."""
        if gc is not None:
            layers = [
                (8.0,  wx.Colour(r // 8,  g // 8,  b // 8)),
                (3.5,  wx.Colour(r // 3,  g // 3,  b // 3)),
                (1.5,  wx.Colour(r,        g,        b)),
            ]
            for width, col in layers:
                gc.SetPen(gc.CreatePen(
                    wx.GraphicsPenInfo(col).Width(width)
                    .Cap(wx.CAP_ROUND).Join(wx.JOIN_ROUND)
                ))
                path = gc.CreatePath()
                path.MoveToPoint(pts[0])
                for pt in pts[1:]:
                    path.AddLineToPoint(pt)
                gc.StrokePath(path)
        else:
            dc.SetPen(wx.Pen(wx.Colour(r, g, b), 2))
            dc.DrawLines([(int(x), int(y)) for x, y in pts])


# ---------------------------------------------------------------------------
# KnobWidget — painted rotary knob, scroll-wheel adjustable
# ---------------------------------------------------------------------------

class KnobWidget(wx.Panel):
    """Oscilloscope-style rotary knob with amber indicator and engraved label."""

    _R = 22   # knob radius in px

    def __init__(self, parent, label: str, divs: List[float], unit: str,
                 on_change: Optional[Callable] = None):
        super().__init__(parent, size=wx.Size(72, 82))
        self.SetMinSize(wx.Size(72, 82))
        self.SetBackgroundColour(_STRIP_BG)
        self._label     = label
        self._divs      = divs
        self._unit      = unit
        self._idx       = len(divs) // 2
        self._on_change = on_change
        self.Bind(wx.EVT_PAINT,      self._on_paint)
        self.Bind(wx.EVT_MOUSEWHEEL, self._on_wheel)

    @property
    def value(self) -> float:
        return self._divs[self._idx]

    def set_index(self, idx: int) -> None:
        self._idx = max(0, min(idx, len(self._divs) - 1))
        self.Refresh()

    def _on_wheel(self, evt: wx.MouseEvent) -> None:
        self._idx = max(0, min(
            self._idx + (1 if evt.GetWheelRotation() > 0 else -1),
            len(self._divs) - 1,
        ))
        self.Refresh()
        if self._on_change:
            self._on_change(self._divs[self._idx])

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        W, H = self.GetClientSize()
        dc.SetBackground(wx.Brush(_STRIP_BG))
        dc.Clear()

        R  = self._R
        cx = W // 2
        cy = R + 8   # knob centre y

        # Shadow ring
        dc.SetPen(wx.Pen(wx.Colour(8, 8, 9), 2))
        dc.SetBrush(wx.Brush(wx.Colour(12, 12, 13)))
        dc.DrawCircle(cx, cy, R + 5)

        # Knob body
        dc.SetPen(wx.Pen(wx.Colour(55, 55, 58), 1))
        dc.SetBrush(wx.Brush(_KNOB_BD))
        dc.DrawCircle(cx, cy, R)

        # Top-left specular glint
        dc.SetPen(wx.Pen(_KNOB_HI, 1))
        dc.SetBrush(wx.Brush(_KNOB_HI))
        dc.DrawCircle(cx - R // 3, cy - R // 3, R // 4)

        # Amber indicator line
        n     = max(len(self._divs) - 1, 1)
        angle = math.radians(-135 + (self._idx / n) * 270 - 90)
        ix    = cx + int((R - 7) * math.cos(angle))
        iy    = cy + int((R - 7) * math.sin(angle))
        dc.SetPen(wx.Pen(_AMBER, 3, wx.PENSTYLE_SOLID))
        dc.DrawLine(cx, cy, ix, iy)

        # Axle centre dot
        dc.SetPen(wx.Pen(wx.Colour(8, 8, 8), 1))
        dc.SetBrush(wx.Brush(wx.Colour(8, 8, 8)))
        dc.DrawCircle(cx, cy, 4)

        # Tick marks around the knob arc (9 marks, −135° to +135°)
        dc.SetPen(wx.Pen(_DIM, 1))
        for k in range(9):
            a = math.radians(-135 + k * 270 / 8 - 90)
            x1 = cx + int((R + 2) * math.cos(a))
            y1 = cy + int((R + 2) * math.sin(a))
            x2 = cx + int((R + 5) * math.cos(a))
            y2 = cy + int((R + 5) * math.sin(a))
            dc.DrawLine(x1, y1, x2, y2)

        # Engraved label
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(_DIM)
        lw = dc.GetTextExtent(self._label)[0]
        dc.DrawText(self._label, (W - lw) // 2, cy + R + 7)

        # Green LED-style value readout
        val_str = _fmt_eng(self._divs[self._idx], self._unit) + '/div'
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(wx.Colour(0, 185, 72))
        vw = dc.GetTextExtent(val_str)[0]
        dc.DrawText(val_str, (W - vw) // 2, cy + R + 18)


# ---------------------------------------------------------------------------
# ChannelButton — per-net LED toggle in the channel strip
# ---------------------------------------------------------------------------

class ChannelButton(wx.Panel):
    """A clickable channel row: coloured LED + net name."""

    _H = 30

    def __init__(self, parent, idx: int, net_name: str,
                 color_hex: str, active: bool, on_click: Callable):
        super().__init__(parent, size=wx.Size(-1, self._H))
        self.SetMinSize(wx.Size(-1, self._H))
        self.SetBackgroundColour(_STRIP_BG)
        self._idx      = idx
        self._net      = net_name
        self._color    = color_hex
        self._active   = active
        self._on_click = on_click
        self._hover    = False
        self.Bind(wx.EVT_PAINT,        self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN,    self._on_ldown)
        self.Bind(wx.EVT_ENTER_WINDOW, self._on_enter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._on_leave)

    def set_active(self, v: bool) -> None:
        self._active = v
        self.Refresh()

    def _on_ldown(self, _evt) -> None:
        self._on_click(self._net)

    def _on_enter(self, _evt) -> None:
        self._hover = True
        self.Refresh()

    def _on_leave(self, _evt) -> None:
        self._hover = False
        self.Refresh()

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        W, H = self.GetClientSize()
        bg = wx.Colour(34, 36, 34) if self._hover else _STRIP_BG
        dc.SetBackground(wx.Brush(bg))
        dc.Clear()

        r, g, b = _hex_to_rgb(self._color)
        led_r = 6
        led_cx, led_cy = 14, H // 2

        # LED circle
        if self._active:
            # bright filled
            dc.SetPen(wx.Pen(wx.Colour(r, g, b), 1))
            dc.SetBrush(wx.Brush(wx.Colour(r, g, b)))
        else:
            # dim / unlit
            dc.SetPen(wx.Pen(wx.Colour(r // 6, g // 6, b // 6), 1))
            dc.SetBrush(wx.Brush(wx.Colour(r // 8, g // 8, b // 8)))
        dc.DrawCircle(led_cx, led_cy, led_r)

        # Channel index label (e.g. "1")
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        num_lbl = str(self._idx + 1)
        nw, nh = dc.GetTextExtent(num_lbl)
        dc.SetTextForeground(wx.Colour(r // 2, g // 2, b // 2) if not self._active
                             else wx.Colour(r, g, b))
        dc.DrawText(num_lbl, led_cx - nw // 2, led_cy - nh // 2)

        # Net name
        short = self._net[:18]
        dc.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(wx.Colour(r, g, b) if self._active else _DIM)
        dc.DrawText(short, 26, (H - dc.GetTextExtent(short)[1]) // 2)


# ---------------------------------------------------------------------------
# WaveformFrame — the full KiScope oscilloscope window
# ---------------------------------------------------------------------------

class WaveformFrame(wx.Frame):
    """KiScope oscilloscope-style waveform viewer."""

    def __init__(self, parent,
                 traces: Dict[str, TransientTrace],
                 on_probe_toggle: Optional[Callable[[bool], None]] = None):
        super().__init__(parent, title='KiScope',
                         size=(1060, 660),
                         style=wx.DEFAULT_FRAME_STYLE)
        self._traces          = traces
        self._on_probe_toggle = on_probe_toggle
        self._probe_active    = False
        self._ch_buttons:  Dict[str, ChannelButton] = {}
        self._probe_btn:   Optional[wx.ToggleButton] = None

        # Assign a fixed colour to every net (sorted for determinism)
        self._net_colors: Dict[str, str] = {
            name: _CH_COLORS[i % len(_CH_COLORS)]
            for i, name in enumerate(sorted(traces))
        }
        self._visible: Dict[str, bool] = {n: False for n in traces}

        # Pick initial knob positions from data
        all_times = [t for tr in traces.values() for t in (tr.times or [])]
        all_vals  = [v for tr in traces.values() for v in (tr.values or [])]
        t_needed  = (max(all_times) / _NH) if all_times else 1e-3
        v_needed  = ((max(all_vals) - min(all_vals)) / _NV) if all_vals else 1.0
        self._init_t_idx = _best_idx(_T_DIVS, t_needed)
        self._init_v_idx = _best_idx(_V_DIVS, v_needed)

        self._build()
        self.SetIcon(wx.NullIcon)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    # ------------------------------------------------------------------
    # Public API

    def toggle_net(self, net_name: str) -> None:
        if net_name not in self._traces:
            return
        new = not self._visible.get(net_name, False)
        self._visible[net_name] = new
        self._screen.set_visibility(net_name, new)
        btn = self._ch_buttons.get(net_name)
        if btn:
            btn.set_active(new)

    def deactivate_probe(self) -> None:
        self._probe_active = False
        if self._probe_btn:
            self._probe_btn.SetValue(False)
            self._style_probe_btn(False)

    # ------------------------------------------------------------------

    def _build(self) -> None:
        body = wx.Panel(self)
        body.SetBackgroundColour(_BODY)
        outer = wx.BoxSizer(wx.VERTICAL)

        outer.Add(self._make_header(body), 0, wx.EXPAND)
        outer.Add(self._make_main(body),   1, wx.EXPAND | wx.ALL, 5)
        outer.Add(self._make_controls(body), 0, wx.EXPAND)

        body.SetSizer(outer)
        fsz = wx.BoxSizer(wx.VERTICAL)
        fsz.Add(body, 1, wx.EXPAND)
        self.SetSizer(fsz)
        self.Layout()

    # ── Header ──────────────────────────────────────────────────────────

    def _make_header(self, parent: wx.Panel) -> wx.Panel:
        hdr = wx.Panel(parent)
        hdr.SetBackgroundColour(wx.Colour(14, 16, 14))
        hdr.SetMinSize(wx.Size(-1, 50))
        sz = wx.BoxSizer(wx.HORIZONTAL)

        # Logo: "KiScope"
        logo = wx.StaticText(hdr, label='KiScope')
        logo.SetFont(wx.Font(22, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        logo.SetForegroundColour(_BRAND)

        # Sub-label
        sub = wx.StaticText(hdr, label='DIGITAL STORAGE OSCILLOSCOPE  ·  DSO BB-1')
        sub.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        sub.SetForegroundColour(_DIM)

        logo_col = wx.BoxSizer(wx.VERTICAL)
        logo_col.Add(logo, 0)
        logo_col.Add(sub,  0, wx.TOP, 2)

        sz.Add(logo_col, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 14)
        sz.AddStretchSpacer()

        # Simulation info
        if self._traces:
            all_t = [t for tr in self._traces.values() for t in (tr.times or [])]
            if all_t:
                t_tot = max(all_t)
                info_txt = (f'{len(self._traces)} nets captured  ·  '
                            f'span {_fmt_eng(t_tot, "s")}')
                info = wx.StaticText(hdr, label=info_txt)
                info.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE,
                                     wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
                info.SetForegroundColour(wx.Colour(0, 130, 55))
                sz.Add(info, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 16)

        # Power LED indicator (painted)
        pwr = _LedDot(hdr, _LED_GRN, 'PWR')
        sz.Add(pwr, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 12)

        hdr.SetSizer(sz)
        return hdr

    # ── Main area: screen + channel strip ───────────────────────────────

    def _make_main(self, parent: wx.Panel) -> wx.BoxSizer:
        row = wx.BoxSizer(wx.HORIZONTAL)

        # Bezel (dark frame around the CRT)
        bezel = wx.Panel(parent)
        bezel.SetBackgroundColour(_BEZEL)
        b_sz = wx.BoxSizer(wx.VERTICAL)
        self._screen = OscopeScreen(bezel, self._traces,
                                    self._net_colors, self._visible)
        self._screen.set_t_div(_T_DIVS[self._init_t_idx])
        self._screen.set_v_div(_V_DIVS[self._init_v_idx])
        b_sz.Add(self._screen, 1, wx.EXPAND | wx.ALL, 7)
        bezel.SetSizer(b_sz)

        row.Add(bezel, 1, wx.EXPAND)
        row.Add(self._make_channel_strip(parent), 0, wx.EXPAND | wx.LEFT, 5)
        return row

    def _make_channel_strip(self, parent: wx.Panel) -> wx.Panel:
        strip = wx.Panel(parent)
        strip.SetBackgroundColour(_STRIP_BG)
        strip.SetMinSize(wx.Size(148, -1))
        sz = wx.BoxSizer(wx.VERTICAL)

        # "CHANNELS" label
        lbl = wx.StaticText(strip, label='CHANNELS')
        lbl.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        lbl.SetForegroundColour(_DIM)
        lbl.SetBackgroundColour(_STRIP_BG)
        sz.Add(lbl, 0, wx.LEFT | wx.TOP, 8)

        # Separator
        sz.Add(wx.StaticLine(strip), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)

        # Scrolled channel list
        scr = wx.ScrolledWindow(strip, style=wx.VSCROLL)
        scr.SetScrollRate(0, 10)
        scr.SetBackgroundColour(_STRIP_BG)
        scr_sz = wx.BoxSizer(wx.VERTICAL)

        for i, net in enumerate(sorted(self._traces)):
            color = self._net_colors[net]
            btn = ChannelButton(scr, i, net, color,
                                active=False,
                                on_click=self._on_channel_click)
            scr_sz.Add(btn, 0, wx.EXPAND | wx.TOP, 2)
            self._ch_buttons[net] = btn

        scr.SetSizer(scr_sz)
        scr.FitInside()
        sz.Add(scr, 1, wx.EXPAND | wx.TOP, 4)

        sz.Add(wx.StaticLine(strip), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)

        # Probe toggle button
        self._probe_btn = wx.ToggleButton(strip, label='⦿  PROBE')
        self._probe_btn.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                                        wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self._probe_btn.SetToolTip('Activate probe mode — click any net on the breadboard')
        self._probe_btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_probe_btn)
        self._style_probe_btn(False)
        sz.Add(self._probe_btn, 0, wx.EXPAND | wx.ALL, 8)

        # "CLEAR ALL" link
        clr = wx.Button(strip, label='Clear all', style=wx.BORDER_NONE)
        clr.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        clr.SetForegroundColour(_DIM)
        clr.SetBackgroundColour(_STRIP_BG)
        clr.Bind(wx.EVT_BUTTON, self._on_clear_all)
        sz.Add(clr, 0, wx.ALIGN_CENTRE | wx.BOTTOM, 6)

        strip.SetSizer(sz)
        return strip

    # ── Controls strip (knobs + decorative buttons) ──────────────────────

    def _make_controls(self, parent: wx.Panel) -> wx.Panel:
        ctrl = wx.Panel(parent)
        ctrl.SetBackgroundColour(_STRIP_BG)
        ctrl.SetMinSize(wx.Size(-1, 88))
        sz = wx.BoxSizer(wx.HORIZONTAL)

        # V/DIV knob
        self._v_knob = KnobWidget(ctrl, 'VOLTS/DIV', _V_DIVS, 'V',
                                   on_change=self._screen.set_v_div)
        self._v_knob.set_index(self._init_v_idx)

        # TIME/DIV knob
        self._t_knob = KnobWidget(ctrl, 'TIME/DIV', _T_DIVS, 's',
                                   on_change=self._screen.set_t_div)
        self._t_knob.set_index(self._init_t_idx)

        # TRIG LVL knob (decorative — controls nothing yet)
        trig_knob = KnobWidget(ctrl, 'TRIG LVL', _V_DIVS, 'V')
        trig_knob.set_index(len(_V_DIVS) // 2)

        # POSITION knob (decorative)
        pos_knob = KnobWidget(ctrl, 'POSITION', [-4.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0], 'div')
        pos_knob.set_index(4)

        sz.Add(self._v_knob,  0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,  16)
        sz.Add(self._t_knob,  0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,  12)
        sz.Add(trig_knob,     0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,  12)
        sz.Add(pos_knob,      0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,  12)

        # Separator
        sep = wx.StaticLine(ctrl, style=wx.LI_VERTICAL)
        sz.Add(sep, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 14)

        # RUN/STOP button (decorative green)
        run_btn = wx.Button(ctrl, label='▶  RUN')
        run_btn.SetBackgroundColour(wx.Colour(0, 80, 30))
        run_btn.SetForegroundColour(wx.Colour(0, 230, 80))
        run_btn.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        run_btn.SetToolTip('Simulation already complete')
        sz.Add(run_btn, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)

        # AUTO button (decorative)
        auto_btn = wx.Button(ctrl, label='AUTO')
        auto_btn.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        auto_btn.Bind(wx.EVT_BUTTON, self._on_auto)
        auto_btn.SetToolTip('Auto-fit all visible traces')
        sz.Add(auto_btn, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)

        ctrl.SetSizer(sz)
        return ctrl

    # ── Event handlers ────────────────────────────────────────────────────

    def _on_channel_click(self, net_name: str) -> None:
        self.toggle_net(net_name)

    def _on_probe_btn(self, _evt) -> None:
        self._probe_active = self._probe_btn.GetValue()
        self._style_probe_btn(self._probe_active)
        if self._on_probe_toggle:
            self._on_probe_toggle(self._probe_active)

    def _style_probe_btn(self, active: bool) -> None:
        if active:
            self._probe_btn.SetBackgroundColour(wx.Colour(80, 50, 0))
            self._probe_btn.SetForegroundColour(_AMBER)
        else:
            self._probe_btn.SetBackgroundColour(wx.NullColour)
            self._probe_btn.SetForegroundColour(wx.NullColour)

    def _on_clear_all(self, _evt) -> None:
        for net in list(self._visible):
            self._visible[net] = False
        self._screen.Refresh()
        for btn in self._ch_buttons.values():
            btn.set_active(False)

    def _on_auto(self, _evt) -> None:
        """Reset knobs to auto-fit the current active traces."""
        self._screen.set_t_div(None)
        self._screen.set_v_div(None)
        self._t_knob.set_index(self._init_t_idx)
        self._v_knob.set_index(self._init_v_idx)

    def _on_close(self, evt) -> None:
        if self._probe_active and self._on_probe_toggle:
            self._on_probe_toggle(False)
        evt.Skip()


# ---------------------------------------------------------------------------
# _LedDot — small painted LED indicator used in the header
# ---------------------------------------------------------------------------

class _LedDot(wx.Panel):
    def __init__(self, parent: wx.Panel, color: wx.Colour, label: str):
        super().__init__(parent, size=wx.Size(32, 44))
        self.SetMinSize(wx.Size(32, 44))
        self.SetBackgroundColour(parent.GetBackgroundColour())
        self._color = color
        self._label = label
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        W, H = self.GetClientSize()
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()
        r = 7
        cx = W // 2
        # Glow ring
        dc.SetPen(wx.Pen(wx.Colour(
            self._color.Red() // 4,
            self._color.Green() // 4,
            self._color.Blue() // 4,
        ), 1))
        dc.SetBrush(wx.Brush(wx.Colour(
            self._color.Red() // 4,
            self._color.Green() // 4,
            self._color.Blue() // 4,
        )))
        dc.DrawCircle(cx, r + 4, r + 3)
        # Core
        dc.SetPen(wx.Pen(self._color, 1))
        dc.SetBrush(wx.Brush(self._color))
        dc.DrawCircle(cx, r + 4, r)
        # Label
        dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(_DIM)
        lw = dc.GetTextExtent(self._label)[0]
        dc.DrawText(self._label, (W - lw) // 2, r * 2 + 8)
