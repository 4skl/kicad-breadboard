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
# Colour palette — Tektronix 2430A inspired
# ---------------------------------------------------------------------------

_BODY      = wx.Colour(175, 172, 166)   # main front-panel warm light gray
_HDR_DARK  = wx.Colour(32, 32, 35)      # dark top header + section header bars
_BEZEL     = wx.Colour(16, 16, 18)      # screen bezel (very dark)
_SCREEN    = wx.Colour(3, 14, 5)        # phosphor screen background
_GRID_MAJ  = wx.Colour(12, 36, 15)     # major graticule lines
_GRID_CTR  = wx.Colour(18, 54, 22)     # centre crosshair (brighter)
_SECT_BG   = wx.Colour(150, 147, 142)  # section panel background
_SECT_LBL  = wx.Colour(215, 213, 208)  # section header label (on dark bar)
_TEXT      = wx.Colour(22, 22, 22)     # primary text on light panel
_TEXT_DIM  = wx.Colour(86, 84, 80)    # secondary / dim text
_KNOB_RING = wx.Colour(36, 36, 40)    # outer serrated ring
_KNOB_FACE = wx.Colour(80, 78, 74)    # concave knob face
_KNOB_SHAD = wx.Colour(20, 20, 22)    # drop shadow under knob
_IND_LINE  = wx.Colour(244, 243, 236) # white indicator line
_LED_GRN   = wx.Colour(0, 225, 65)   # power LED green

# Classic DSO channel colours
_CH_COLORS = [
    '#f0e020', '#00ccff', '#ff7700', '#ff44ff',
    '#44ff88', '#ff4444', '#44ffdd', '#ffcc44',
    '#8844ff', '#ff88cc', '#88ff44', '#ff4488',
]

_T_DIVS: List[float] = [
    1e-9, 2e-9, 5e-9, 1e-8, 2e-8, 5e-8,
    1e-7, 2e-7, 5e-7, 1e-6, 2e-6, 5e-6,
    1e-5, 2e-5, 5e-5, 1e-4, 2e-4, 5e-4,
    1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2,
    0.1, 0.2, 0.5, 1.0, 2.0, 5.0,
]
_V_DIVS: List[float] = [
    1e-3, 2e-3, 5e-3, 0.01, 0.02, 0.05,
    0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0,
]

_NH = 10
_NV = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _fmt_eng(val: float, unit: str) -> str:
    if val == 0:
        return f'0 {unit}'
    a = abs(val)
    if a < 1e-6:  return f'{val * 1e9:.3g} n{unit}'
    if a < 1e-3:  return f'{val * 1e6:.3g} µ{unit}'
    if a < 1.0:   return f'{val * 1e3:.3g} m{unit}'
    if a < 1000:  return f'{val:.3g} {unit}'
    return f'{val / 1e3:.3g} k{unit}'


def _best_idx(table: List[float], needed: float) -> int:
    for i, v in enumerate(table):
        if v >= needed:
            return i
    return len(table) - 1


# ---------------------------------------------------------------------------
# OscopeScreen — phosphor CRT display
# ---------------------------------------------------------------------------

class OscopeScreen(wx.Panel):
    _PAD = 10

    def __init__(self, parent,
                 traces:     Dict[str, TransientTrace],
                 net_colors: Dict[str, str],
                 visible:    Dict[str, bool]):
        super().__init__(parent)
        self.SetBackgroundColour(_SCREEN)
        self.SetMinSize(wx.Size(420, 280))
        self._traces  = traces
        self._colors  = net_colors
        self._visible = visible
        self._t_div: Optional[float] = None
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

    def _on_paint(self, _evt) -> None:
        dc = wx.BufferedPaintDC(self)
        W, H = self.GetClientSize()
        p = self._PAD
        pw, ph = W - 2 * p, H - 2 * p

        dc.SetBackground(wx.Brush(_SCREEN))
        dc.Clear()
        if pw < 40 or ph < 30:
            return

        self._draw_graticule(dc, p, p, pw, ph)

        active = {n: t for n, t in self._traces.items()
                  if self._visible.get(n) and t.times}

        if not active:
            dc.SetTextForeground(wx.Colour(0, 52, 18))
            dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_ITALIC, wx.FONTWEIGHT_NORMAL))
            msg = 'Click  ⦿ PROBE  then click any net on the breadboard'
            tw, th = dc.GetTextExtent(msg)
            dc.DrawText(msg, (W - tw) // 2, (H - th) // 2)
            return

        t_max  = max(max(tr.times) for tr in active.values())
        all_v  = [v for tr in active.values() for v in tr.values]
        v_min0, v_max0 = min(all_v), max(all_v)
        v_span = v_max0 - v_min0 or 1.0

        t_window = self._t_div * _NH if self._t_div else (t_max or 1e-3)

        if self._v_div:
            vc    = (v_max0 + v_min0) / 2
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

        t_div_lbl = _fmt_eng(self._t_div or t_window / _NH, 's') + '/div'
        v_div_lbl = _fmt_eng(self._v_div or (v_hi - v_lo) / _NV, 'V') + '/div'
        dc.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(wx.Colour(0, 148, 52))
        dc.DrawText(t_div_lbl, p + 4, p + ph - 14)
        tw = dc.GetTextExtent(v_div_lbl)[0]
        dc.DrawText(v_div_lbl, p + pw - tw - 4, p + ph - 14)

    def _draw_graticule(self, dc: wx.DC, ox: int, oy: int, pw: int, ph: int) -> None:
        dc.SetPen(wx.Pen(_GRID_MAJ, 1))
        for i in range(_NH + 1):
            x = ox + round(i * pw / _NH)
            dc.DrawLine(x, oy, x, oy + ph)
        for i in range(_NV + 1):
            y = oy + round(i * ph / _NV)
            dc.DrawLine(ox, y, ox + pw, y)

        dc.SetPen(wx.Pen(_GRID_CTR, 1))
        cx = ox + pw // 2
        cy = oy + ph // 2
        dc.DrawLine(cx, oy, cx, oy + ph)
        dc.DrawLine(ox, cy, ox + pw, cy)

        tk = 4
        for i in range(_NH * 5 + 1):
            x = ox + round(i * pw / (_NH * 5))
            dc.DrawLine(x, cy - tk, x, cy + tk)
        for i in range(_NV * 5 + 1):
            y = oy + round(i * ph / (_NV * 5))
            dc.DrawLine(cx - tk, y, cx + tk, y)

        dc.SetPen(wx.Pen(_GRID_CTR, 1))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.DrawRectangle(ox, oy, pw, ph)

    def _draw_phosphor(self, dc: wx.DC, gc, pts: list,
                       r: int, g: int, b: int) -> None:
        if gc is not None:
            layers = [
                (8.0, wx.Colour(r // 8,  g // 8,  b // 8)),
                (3.5, wx.Colour(r // 3,  g // 3,  b // 3)),
                (1.5, wx.Colour(r,        g,        b)),
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
# KnobWidget — Tektronix-style rotary knob
# ---------------------------------------------------------------------------

class KnobWidget(wx.Panel):
    """Tektronix-style knob: dark outer serrated ring, concave face, white indicator."""

    _R_RING = 26   # outer ring radius
    _R_FACE = 19   # concave face radius

    def __init__(self, parent, label: str, divs: List[float], unit: str,
                 on_change: Optional[Callable] = None):
        super().__init__(parent, size=wx.Size(76, 90))
        self.SetMinSize(wx.Size(76, 90))
        self.SetBackgroundColour(_SECT_BG)
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
        dc.SetBackground(wx.Brush(_SECT_BG))
        dc.Clear()

        Rr = self._R_RING
        Rf = self._R_FACE
        cx = W // 2
        cy = Rr + 10

        # Drop shadow
        dc.SetPen(wx.Pen(_KNOB_SHAD, 1))
        dc.SetBrush(wx.Brush(_KNOB_SHAD))
        dc.DrawCircle(cx + 2, cy + 2, Rr + 2)

        # Outer serrated ring
        dc.SetPen(wx.Pen(wx.Colour(52, 52, 56), 1))
        dc.SetBrush(wx.Brush(_KNOB_RING))
        dc.DrawCircle(cx, cy, Rr)

        # Serration notches (subtle radial marks on the outer rim)
        dc.SetPen(wx.Pen(wx.Colour(48, 48, 52), 1))
        for k in range(20):
            a = math.radians(k * 360 / 20)
            x1 = cx + int((Rr - 4) * math.cos(a))
            y1 = cy + int((Rr - 4) * math.sin(a))
            x2 = cx + int(Rr * math.cos(a))
            y2 = cy + int(Rr * math.sin(a))
            dc.DrawLine(x1, y1, x2, y2)

        # Concave face
        dc.SetPen(wx.Pen(wx.Colour(96, 94, 90), 1))
        dc.SetBrush(wx.Brush(_KNOB_FACE))
        dc.DrawCircle(cx, cy, Rf)

        # White indicator line
        n     = max(len(self._divs) - 1, 1)
        angle = math.radians(-135 + (self._idx / n) * 270 - 90)
        ix    = cx + int((Rf - 4) * math.cos(angle))
        iy    = cy + int((Rf - 4) * math.sin(angle))
        dc.SetPen(wx.Pen(_IND_LINE, 2, wx.PENSTYLE_SOLID))
        dc.DrawLine(cx, cy, ix, iy)

        # Centre rivet
        dc.SetPen(wx.Pen(wx.Colour(50, 50, 48), 1))
        dc.SetBrush(wx.Brush(wx.Colour(62, 60, 58)))
        dc.DrawCircle(cx, cy, 3)

        # Scale marks outside the ring (9 ticks, −135° to +135°)
        dc.SetPen(wx.Pen(_TEXT_DIM, 1))
        for k in range(9):
            a  = math.radians(-135 + k * 270 / 8 - 90)
            x1 = cx + int((Rr + 3) * math.cos(a))
            y1 = cy + int((Rr + 3) * math.sin(a))
            x2 = cx + int((Rr + 7) * math.cos(a))
            y2 = cy + int((Rr + 7) * math.sin(a))
            dc.DrawLine(x1, y1, x2, y2)

        # Label
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(_TEXT_DIM)
        lw = dc.GetTextExtent(self._label)[0]
        dc.DrawText(self._label, (W - lw) // 2, cy + Rr + 7)

        # Value readout
        val_str = _fmt_eng(self._divs[self._idx], self._unit) + '/div'
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(_TEXT)
        vw = dc.GetTextExtent(val_str)[0]
        dc.DrawText(val_str, (W - vw) // 2, cy + Rr + 18)


# ---------------------------------------------------------------------------
# ChannelButton — per-net LED toggle on the light panel
# ---------------------------------------------------------------------------

class ChannelButton(wx.Panel):
    _H = 26

    def __init__(self, parent, idx: int, net_name: str,
                 color_hex: str, active: bool, on_click: Callable):
        super().__init__(parent, size=wx.Size(-1, self._H))
        self.SetMinSize(wx.Size(-1, self._H))
        self.SetBackgroundColour(_SECT_BG)
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
        bg = wx.Colour(188, 185, 180) if self._hover else _SECT_BG
        dc.SetBackground(wx.Brush(bg))
        dc.Clear()

        r, g, b = _hex_to_rgb(self._color)
        led_cx, led_cy, led_r = 11, H // 2, 5

        if self._active:
            dc.SetPen(wx.Pen(wx.Colour(r, g, b), 1))
            dc.SetBrush(wx.Brush(wx.Colour(r, g, b)))
        else:
            dc.SetPen(wx.Pen(wx.Colour(r // 5, g // 5, b // 5), 1))
            dc.SetBrush(wx.Brush(wx.Colour(r // 6, g // 6, b // 6)))
        dc.DrawCircle(led_cx, led_cy, led_r)

        dc.SetFont(wx.Font(5, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        num = str(self._idx + 1)
        nw, nh = dc.GetTextExtent(num)
        dc.SetTextForeground(wx.Colour(r, g, b) if self._active else _TEXT_DIM)
        dc.DrawText(num, led_cx - nw // 2, led_cy - nh // 2)

        short = self._net[:20]
        dc.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(_TEXT if self._active else _TEXT_DIM)
        dc.DrawText(short, 22, (H - dc.GetTextExtent(short)[1]) // 2)


# ---------------------------------------------------------------------------
# _LedDot — small painted LED used in the dark header
# ---------------------------------------------------------------------------

class _LedDot(wx.Panel):
    def __init__(self, parent: wx.Panel, color: wx.Colour, label: str):
        super().__init__(parent, size=wx.Size(30, 42))
        self.SetMinSize(wx.Size(30, 42))
        self.SetBackgroundColour(parent.GetBackgroundColour())
        self._color = color
        self._label = label
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        W, H = self.GetClientSize()
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()
        r, g, b = self._color.Red(), self._color.Green(), self._color.Blue()
        cx, cy, rad = W // 2, 8, 6
        dc.SetPen(wx.Pen(wx.Colour(r // 4, g // 4, b // 4), 1))
        dc.SetBrush(wx.Brush(wx.Colour(r // 4, g // 4, b // 4)))
        dc.DrawCircle(cx, cy, rad + 3)
        dc.SetPen(wx.Pen(self._color, 1))
        dc.SetBrush(wx.Brush(self._color))
        dc.DrawCircle(cx, cy, rad)
        dc.SetFont(wx.Font(5, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(_SECT_LBL)
        lw = dc.GetTextExtent(self._label)[0]
        dc.DrawText(self._label, (W - lw) // 2, cy + rad + 4)


# ---------------------------------------------------------------------------
# _BncConnector — decorative BNC input on the bottom strip
# ---------------------------------------------------------------------------

class _BncConnector(wx.Panel):
    def __init__(self, parent: wx.Panel, label: str, color_hex: str):
        super().__init__(parent, size=wx.Size(44, 44))
        self.SetMinSize(wx.Size(44, 44))
        self.SetBackgroundColour(parent.GetBackgroundColour())
        self._label = label
        self._color = color_hex
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        W, H = self.GetClientSize()
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        cx, cy = W // 2, H // 2 - 4
        r, g, b = _hex_to_rgb(self._color)

        # Outer shell
        dc.SetPen(wx.Pen(wx.Colour(24, 24, 26), 1))
        dc.SetBrush(wx.Brush(wx.Colour(44, 44, 48)))
        dc.DrawCircle(cx, cy, 15)

        # Colored channel ring
        dc.SetPen(wx.Pen(wx.Colour(r // 2, g // 2, b // 2), 3))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.DrawCircle(cx, cy, 11)

        # Insulator
        dc.SetPen(wx.Pen(wx.Colour(175, 170, 162), 1))
        dc.SetBrush(wx.Brush(wx.Colour(205, 200, 190)))
        dc.DrawCircle(cx, cy, 7)

        # Centre pin
        dc.SetPen(wx.Pen(wx.Colour(175, 172, 165), 1))
        dc.SetBrush(wx.Brush(wx.Colour(195, 192, 185)))
        dc.DrawCircle(cx, cy, 3)

        # Label
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(wx.Colour(r, g, b))
        lw = dc.GetTextExtent(self._label)[0]
        dc.DrawText(self._label, (W - lw) // 2, H - 11)


# ---------------------------------------------------------------------------
# WaveformFrame — the full KiScope oscilloscope window
# ---------------------------------------------------------------------------

class WaveformFrame(wx.Frame):
    """KiScope oscilloscope-style waveform viewer, Tektronix 2430A inspired."""

    def __init__(self, parent,
                 traces: Dict[str, TransientTrace],
                 on_probe_toggle: Optional[Callable[[bool], None]] = None):
        super().__init__(parent, title='KiScope',
                         size=(1080, 660),
                         style=wx.DEFAULT_FRAME_STYLE)
        self._traces          = traces
        self._on_probe_toggle = on_probe_toggle
        self._probe_active    = False
        self._ch_buttons:  Dict[str, ChannelButton] = {}
        self._probe_btn:   Optional[wx.ToggleButton] = None

        self._net_colors: Dict[str, str] = {
            name: _CH_COLORS[i % len(_CH_COLORS)]
            for i, name in enumerate(sorted(traces))
        }
        self._visible: Dict[str, bool] = {n: False for n in traces}

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
        outer.Add(self._make_header(body),      0, wx.EXPAND)
        outer.Add(self._make_main_row(body),    1, wx.EXPAND | wx.ALL, 5)
        outer.Add(self._make_bottom_strip(body), 0, wx.EXPAND)
        body.SetSizer(outer)
        fsz = wx.BoxSizer(wx.VERTICAL)
        fsz.Add(body, 1, wx.EXPAND)
        self.SetSizer(fsz)
        self.Layout()

    # ── Header ─ dark top band ──────────────────────────────────────────

    def _make_header(self, parent: wx.Panel) -> wx.Panel:
        hdr = wx.Panel(parent)
        hdr.SetBackgroundColour(_HDR_DARK)
        hdr.SetMinSize(wx.Size(-1, 52))
        sz = wx.BoxSizer(wx.HORIZONTAL)

        logo = wx.StaticText(hdr, label='KiScope')
        logo.SetFont(wx.Font(20, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        logo.SetForegroundColour(wx.Colour(255, 255, 255))
        logo.SetBackgroundColour(_HDR_DARK)

        sub = wx.StaticText(hdr, label='DIGITAL STORAGE OSCILLOSCOPE  ·  DSO BB-1')
        sub.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        sub.SetForegroundColour(wx.Colour(155, 153, 148))
        sub.SetBackgroundColour(_HDR_DARK)

        col = wx.BoxSizer(wx.VERTICAL)
        col.Add(logo, 0)
        col.Add(sub, 0, wx.TOP, 3)
        sz.Add(col, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 14)
        sz.AddStretchSpacer()

        if self._traces:
            all_t = [t for tr in self._traces.values() for t in (tr.times or [])]
            if all_t:
                t_tot    = max(all_t)
                info_txt = (f'{len(self._traces)} nets captured  ·  '
                            f'span {_fmt_eng(t_tot, "s")}')
                info = wx.StaticText(hdr, label=info_txt)
                info.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE,
                                     wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
                info.SetForegroundColour(wx.Colour(0, 180, 65))
                info.SetBackgroundColour(_HDR_DARK)
                sz.Add(info, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 20)

        pwr = _LedDot(hdr, _LED_GRN, 'PWR')
        sz.Add(pwr, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 14)

        hdr.SetSizer(sz)
        return hdr

    # ── Main row: screen + right-side sections ─────────────────────────

    def _make_main_row(self, parent: wx.Panel) -> wx.BoxSizer:
        row = wx.BoxSizer(wx.HORIZONTAL)

        bezel = wx.Panel(parent)
        bezel.SetBackgroundColour(_BEZEL)
        b_sz = wx.BoxSizer(wx.VERTICAL)
        self._screen = OscopeScreen(bezel, self._traces,
                                    self._net_colors, self._visible)
        self._screen.set_t_div(_T_DIVS[self._init_t_idx])
        self._screen.set_v_div(_V_DIVS[self._init_v_idx])
        b_sz.Add(self._screen, 1, wx.EXPAND | wx.ALL, 8)
        bezel.SetSizer(b_sz)

        row.Add(bezel, 1, wx.EXPAND)
        row.Add(self._make_right_panel(parent), 0, wx.EXPAND | wx.LEFT, 5)
        return row

    # ── Right panel — stacked VERTICAL / HORIZONTAL / TRIGGER sections ──

    def _make_right_panel(self, parent: wx.Panel) -> wx.Panel:
        rp = wx.Panel(parent)
        rp.SetBackgroundColour(_BODY)
        rp.SetMinSize(wx.Size(216, -1))
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(self._make_vertical_section(rp),   1, wx.EXPAND)
        sz.Add(self._make_horizontal_section(rp), 0, wx.EXPAND | wx.TOP, 4)
        sz.Add(self._make_trigger_section(rp),    0, wx.EXPAND | wx.TOP, 4)
        rp.SetSizer(sz)
        return rp

    def _make_vertical_section(self, parent: wx.Panel) -> wx.Panel:
        sect = wx.Panel(parent)
        sect.SetBackgroundColour(_SECT_BG)
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(_SectionHeader(sect, 'VERTICAL'), 0, wx.EXPAND)

        self._v_knob = KnobWidget(sect, 'VOLTS/DIV', _V_DIVS, 'V',
                                   on_change=self._screen.set_v_div)
        self._v_knob.set_index(self._init_v_idx)
        sz.Add(self._v_knob, 0, wx.ALIGN_CENTRE_HORIZONTAL | wx.TOP, 5)

        sz.Add(wx.StaticLine(sect), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        ch_lbl = wx.StaticText(sect, label='CHANNELS')
        ch_lbl.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        ch_lbl.SetForegroundColour(_TEXT_DIM)
        ch_lbl.SetBackgroundColour(_SECT_BG)
        sz.Add(ch_lbl, 0, wx.LEFT | wx.TOP, 8)

        scr = wx.ScrolledWindow(sect, style=wx.VSCROLL)
        scr.SetScrollRate(0, 10)
        scr.SetBackgroundColour(_SECT_BG)
        scr_sz = wx.BoxSizer(wx.VERTICAL)
        for i, net in enumerate(sorted(self._traces)):
            btn = ChannelButton(scr, i, net, self._net_colors[net],
                                active=False, on_click=self._on_channel_click)
            scr_sz.Add(btn, 0, wx.EXPAND | wx.TOP, 1)
            self._ch_buttons[net] = btn
        scr.SetSizer(scr_sz)
        scr.FitInside()
        sz.Add(scr, 1, wx.EXPAND | wx.TOP, 4)

        sz.Add(wx.StaticLine(sect), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)

        self._probe_btn = wx.ToggleButton(sect, label='⦿  PROBE')
        self._probe_btn.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                                        wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self._probe_btn.SetToolTip(
            'Activate probe mode — click any net on the breadboard')
        self._probe_btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_probe_btn)
        self._style_probe_btn(False)
        sz.Add(self._probe_btn, 0, wx.EXPAND | wx.ALL, 8)

        clr = wx.Button(sect, label='Clear all', style=wx.BORDER_NONE)
        clr.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        clr.SetForegroundColour(_TEXT_DIM)
        clr.SetBackgroundColour(_SECT_BG)
        clr.Bind(wx.EVT_BUTTON, self._on_clear_all)
        sz.Add(clr, 0, wx.ALIGN_CENTRE | wx.BOTTOM, 6)

        sect.SetSizer(sz)
        return sect

    def _make_horizontal_section(self, parent: wx.Panel) -> wx.Panel:
        sect = wx.Panel(parent)
        sect.SetBackgroundColour(_SECT_BG)
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(_SectionHeader(sect, 'HORIZONTAL'), 0, wx.EXPAND)

        knob_row = wx.BoxSizer(wx.HORIZONTAL)
        self._t_knob = KnobWidget(sect, 'TIME/DIV', _T_DIVS, 's',
                                   on_change=self._screen.set_t_div)
        self._t_knob.set_index(self._init_t_idx)
        pos_knob = KnobWidget(sect, 'POSITION',
                              [-4.0, -3.0, -2.0, -1.0, 0.0,
                               1.0, 2.0, 3.0, 4.0], 'div')
        pos_knob.set_index(4)
        knob_row.Add(self._t_knob, 0, wx.LEFT | wx.BOTTOM, 5)
        knob_row.Add(pos_knob,    0, wx.LEFT | wx.BOTTOM, 3)
        sz.Add(knob_row, 0, wx.ALIGN_CENTRE_HORIZONTAL | wx.TOP, 4)

        sect.SetSizer(sz)
        return sect

    def _make_trigger_section(self, parent: wx.Panel) -> wx.Panel:
        sect = wx.Panel(parent)
        sect.SetBackgroundColour(_SECT_BG)
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(_SectionHeader(sect, 'TRIGGER'), 0, wx.EXPAND)

        inner = wx.BoxSizer(wx.HORIZONTAL)

        trig_knob = KnobWidget(sect, 'TRIG LVL', _V_DIVS, 'V')
        trig_knob.set_index(len(_V_DIVS) // 2)
        inner.Add(trig_knob, 0, wx.LEFT | wx.BOTTOM, 5)

        btn_col = wx.BoxSizer(wx.VERTICAL)

        auto_btn = wx.Button(sect, label='AUTO', size=wx.Size(58, 24))
        auto_btn.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        auto_btn.Bind(wx.EVT_BUTTON, self._on_auto)
        auto_btn.SetToolTip('Auto-fit all visible traces')

        run_btn = wx.Button(sect, label='▶ RUN', size=wx.Size(58, 24))
        run_btn.SetBackgroundColour(wx.Colour(0, 72, 24))
        run_btn.SetForegroundColour(wx.Colour(0, 215, 72))
        run_btn.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        run_btn.SetToolTip('Simulation already complete')

        btn_col.Add(auto_btn, 0, wx.LEFT | wx.TOP, 6)
        btn_col.Add(run_btn,  0, wx.LEFT | wx.TOP, 4)
        inner.Add(btn_col, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 2)

        sz.Add(inner, 0, wx.ALIGN_CENTRE_HORIZONTAL | wx.TOP, 4)
        sect.SetSizer(sz)
        return sect

    # ── Bottom strip — BNC connectors ───────────────────────────────────

    def _make_bottom_strip(self, parent: wx.Panel) -> wx.Panel:
        strip = wx.Panel(parent)
        strip.SetBackgroundColour(_SECT_BG)
        strip.SetMinSize(wx.Size(-1, 50))
        sz = wx.BoxSizer(wx.HORIZONTAL)

        sz.Add(_BncConnector(strip, 'CH 1', _CH_COLORS[0]),
               0, wx.LEFT | wx.TOP | wx.BOTTOM, 6)
        sz.Add(_BncConnector(strip, 'CH 2', _CH_COLORS[1]),
               0, wx.LEFT | wx.TOP | wx.BOTTOM, 4)
        sz.AddStretchSpacer()

        model_lbl = wx.StaticText(strip, label='KiScope DSO BB-1')
        model_lbl.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                                  wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        model_lbl.SetForegroundColour(_TEXT_DIM)
        model_lbl.SetBackgroundColour(_SECT_BG)
        sz.Add(model_lbl, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 16)

        strip.SetSizer(sz)
        return strip

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
            self._probe_btn.SetBackgroundColour(wx.Colour(80, 52, 0))
            self._probe_btn.SetForegroundColour(wx.Colour(255, 198, 55))
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
        self._screen.set_t_div(None)
        self._screen.set_v_div(None)
        self._t_knob.set_index(self._init_t_idx)
        self._v_knob.set_index(self._init_v_idx)

    def _on_close(self, evt) -> None:
        if self._probe_active and self._on_probe_toggle:
            self._on_probe_toggle(False)
        evt.Skip()


# ---------------------------------------------------------------------------
# _SectionHeader — dark horizontal label bar between sections
# ---------------------------------------------------------------------------

class _SectionHeader(wx.Panel):
    def __init__(self, parent: wx.Panel, label: str):
        super().__init__(parent)
        self.SetBackgroundColour(_HDR_DARK)
        self.SetMinSize(wx.Size(-1, 18))
        sz = wx.BoxSizer(wx.HORIZONTAL)
        lbl = wx.StaticText(self, label=label)
        lbl.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        lbl.SetForegroundColour(_SECT_LBL)
        lbl.SetBackgroundColour(_HDR_DARK)
        sz.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 8)
        self.SetSizer(sz)
