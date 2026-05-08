"""
WaveformFrame — KiScope digital-storage oscilloscope UI for transient analysis.

Public API (unchanged from the outside):
    frame = WaveformFrame(parent, traces, on_probe_toggle=cb)
    frame.Show()
    frame.toggle_net('VCC')      # assign to probing channel / next empty channel
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

_BODY      = wx.Colour(175, 172, 166)
_HDR_DARK  = wx.Colour(32, 32, 35)
_BEZEL     = wx.Colour(16, 16, 18)
_SCREEN    = wx.Colour(3, 14, 5)
_GRID_MAJ  = wx.Colour(12, 36, 15)
_GRID_CTR  = wx.Colour(18, 54, 22)
_SECT_BG   = wx.Colour(150, 147, 142)
_SECT_LBL  = wx.Colour(215, 213, 208)
_TEXT      = wx.Colour(22, 22, 22)
_TEXT_DIM  = wx.Colour(86, 84, 80)
_KNOB_RING = wx.Colour(36, 36, 40)
_KNOB_FACE = wx.Colour(80, 78, 74)
_KNOB_SHAD = wx.Colour(20, 20, 22)
_IND_LINE  = wx.Colour(244, 243, 236)
_LED_GRN   = wx.Colour(0, 225, 65)
_BTN_FACE  = wx.Colour(135, 132, 128)
_BTN_DOWN  = wx.Colour(46, 44, 41)

# Channel colours — fixed per slot, Tektronix style
_CH_COLORS = ['#f0e020', '#00ccff', '#ff7700', '#ff44ff']

_NUM_CH = 4   # number of scope channels

# TIME/DIV table (seconds)
_T_DIVS: List[float] = [
    1e-9, 2e-9, 5e-9, 1e-8, 2e-8, 5e-8,
    1e-7, 2e-7, 5e-7, 1e-6, 2e-6, 5e-6,
    1e-5, 2e-5, 5e-5, 1e-4, 2e-4, 5e-4,
    1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2,
    0.1, 0.2, 0.5, 1.0, 2.0, 5.0,
]
# VOLTS/DIV table
_V_DIVS: List[float] = [
    1e-3, 2e-3, 5e-3, 0.01, 0.02, 0.05,
    0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0,
]
# Vertical / horizontal POSITION steps (in divisions)
_POS_DIVS: List[float] = [x * 0.5 for x in range(-10, 11)]   # −5 … +5

_NH = 10   # graticule divisions, horizontal
_NV = 8    # graticule divisions, vertical


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
# _ChanState — per-channel state (shared between ChannelSection and screen)
# ---------------------------------------------------------------------------

class _ChanState:
    __slots__ = ('net_name', 'v_div_idx', 'pos_idx', 'coupling', 'color')

    def __init__(self, net_name: Optional[str], color: str,
                 default_v_idx: int):
        self.net_name:  Optional[str] = net_name
        self.v_div_idx: int           = default_v_idx
        self.pos_idx:   int           = len(_POS_DIVS) // 2   # 0.0 div
        self.coupling:  str           = 'DC'
        self.color:     str           = color

    @property
    def v_div(self) -> float:
        return _V_DIVS[self.v_div_idx]

    @property
    def position(self) -> float:
        return _POS_DIVS[self.pos_idx]


# ---------------------------------------------------------------------------
# OscopeScreen — phosphor CRT with per-channel independent scaling
# ---------------------------------------------------------------------------

class OscopeScreen(wx.Panel):
    _PAD = 10

    def __init__(self, parent,
                 traces:   Dict[str, TransientTrace],
                 channels: List[_ChanState]):
        super().__init__(parent)
        self.SetBackgroundColour(_SCREEN)
        self.SetMinSize(wx.Size(420, 280))
        self._traces   = traces
        self._channels = channels      # shared ref
        self._t_div:   Optional[float] = None
        self._h_pos:   float           = 0.0   # horizontal shift in divisions
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE,  lambda _: self.Refresh())

    def set_t_div(self, v: float) -> None:
        self._t_div = v
        self.Refresh()

    def set_h_pos(self, v: float) -> None:
        self._h_pos = v
        self.Refresh()

    # ------------------------------------------------------------------

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

        active = [
            (ch, self._traces[ch.net_name])
            for ch in self._channels
            if ch.net_name and ch.net_name in self._traces
               and self._traces[ch.net_name].times
        ]

        if not active:
            dc.SetTextForeground(wx.Colour(0, 52, 18))
            dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_ITALIC, wx.FONTWEIGHT_NORMAL))
            msg = 'Click  ⦿ PROBE  on a channel, then click a net on the breadboard'
            tw, th = dc.GetTextExtent(msg)
            dc.DrawText(msg, (W - tw) // 2, (H - th) // 2)
            return

        t_max    = max(max(tr.times) for _, tr in active)
        t_window = self._t_div * _NH if self._t_div else (t_max or 1e-3)
        t_off    = self._h_pos * t_window / _NH

        div_h = ph / _NV   # pixels per division

        def tx(t: float) -> float:
            return p + (t - t_off) / t_window * pw

        try:
            gc = wx.GraphicsContext.Create(dc)
        except Exception:
            gc = None

        for ch, trace in active:
            t_vals = list(trace.times)
            v_vals = list(trace.values)
            if ch.coupling == 'AC' and v_vals:
                mean   = sum(v_vals) / len(v_vals)
                v_vals = [v - mean for v in v_vals]

            v_div  = ch.v_div
            zero_y = p + ph / 2 - ch.position * div_h
            scale  = div_h / v_div   # px per volt

            pts = [(tx(t), zero_y - v * scale)
                   for t, v in zip(t_vals, v_vals)
                   if p - 4 <= tx(t) <= p + pw + 4]
            if len(pts) < 2:
                continue

            r, g, b = _hex_to_rgb(ch.color)
            self._draw_phosphor(dc, gc, pts, r, g, b)

        # Time readout
        t_lbl = _fmt_eng(self._t_div or t_window / _NH, 's') + '/div'
        dc.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(wx.Colour(0, 148, 52))
        dc.DrawText(t_lbl, p + 4, p + ph - 14)

    def _draw_graticule(self, dc: wx.DC,
                        ox: int, oy: int, pw: int, ph: int) -> None:
        dc.SetPen(wx.Pen(_GRID_MAJ, 1))
        for i in range(_NH + 1):
            x = ox + round(i * pw / _NH)
            dc.DrawLine(x, oy, x, oy + ph)
        for i in range(_NV + 1):
            y = oy + round(i * ph / _NV)
            dc.DrawLine(ox, y, ox + pw, y)

        dc.SetPen(wx.Pen(_GRID_CTR, 1))
        cx, cy = ox + pw // 2, oy + ph // 2
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
            for width, col in [
                (8.0, wx.Colour(r // 8, g // 8, b // 8)),
                (3.5, wx.Colour(r // 3, g // 3, b // 3)),
                (1.5, wx.Colour(r,      g,      b)),
            ]:
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
    """Knob with dark outer ring and white indicator. compact=True for channel sections."""

    def __init__(self, parent, label: str, divs: List[float], unit: str,
                 on_change: Optional[Callable] = None, compact: bool = False):
        Rr = 15 if compact else 26
        Rf = 11 if compact else 19
        w  = 54 if compact else 76
        h  = 64 if compact else 90
        super().__init__(parent, size=wx.Size(w, h))
        self.SetMinSize(wx.Size(w, h))
        self.SetBackgroundColour(_SECT_BG)
        self._Rr        = Rr
        self._Rf        = Rf
        self._label     = label
        self._divs      = divs
        self._unit      = unit
        self._idx       = len(divs) // 2
        self._on_change = on_change
        self._compact        = compact
        self._drag_start_xy: Optional[Tuple[int, int]] = None
        self._drag_start_idx: int = 0
        self.Bind(wx.EVT_PAINT,      self._on_paint)
        self.Bind(wx.EVT_MOUSEWHEEL, self._on_wheel)
        self.Bind(wx.EVT_LEFT_DOWN,  self._on_mouse_down)
        self.Bind(wx.EVT_LEFT_UP,    self._on_mouse_up)
        self.Bind(wx.EVT_MOTION,     self._on_motion)

    @property
    def value(self) -> float:
        return self._divs[self._idx]

    def set_index(self, idx: int) -> None:
        self._idx = max(0, min(idx, len(self._divs) - 1))
        self.Refresh()

    def _on_wheel(self, evt: wx.MouseEvent) -> None:
        self._step(1 if evt.GetWheelRotation() > 0 else -1)

    def _on_mouse_down(self, evt: wx.MouseEvent) -> None:
        self._drag_start_xy  = (evt.GetX(), evt.GetY())
        self._drag_start_idx = self._idx
        self.CaptureMouse()
        evt.Skip()

    def _on_mouse_up(self, evt: wx.MouseEvent) -> None:
        if self.HasCapture():
            self.ReleaseMouse()
        self._drag_start_xy = None
        evt.Skip()

    def _on_motion(self, evt: wx.MouseEvent) -> None:
        if self._drag_start_xy is None or not evt.LeftIsDown():
            return
        dx =  evt.GetX() - self._drag_start_xy[0]
        dy = -(evt.GetY() - self._drag_start_xy[1])   # up = positive
        delta = dx if abs(dx) >= abs(dy) else dy
        new_idx = self._drag_start_idx + int(delta / 8)
        new_idx = max(0, min(new_idx, len(self._divs) - 1))
        if new_idx != self._idx:
            self._idx = new_idx
            self.Refresh()
            if self._on_change:
                self._on_change(self._divs[self._idx])

    def _step(self, direction: int) -> None:
        self._idx = max(0, min(self._idx + direction, len(self._divs) - 1))
        self.Refresh()
        if self._on_change:
            self._on_change(self._divs[self._idx])

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        W, H = self.GetClientSize()
        dc.SetBackground(wx.Brush(_SECT_BG))
        dc.Clear()

        Rr = self._Rr
        Rf = self._Rf
        cx = W // 2
        cy = Rr + (7 if self._compact else 10)

        # Shadow
        dc.SetPen(wx.Pen(_KNOB_SHAD, 1))
        dc.SetBrush(wx.Brush(_KNOB_SHAD))
        dc.DrawCircle(cx + 2, cy + 2, Rr + 2)

        # Outer ring
        dc.SetPen(wx.Pen(wx.Colour(52, 52, 56), 1))
        dc.SetBrush(wx.Brush(_KNOB_RING))
        dc.DrawCircle(cx, cy, Rr)

        # Serration notches
        n_notch = 16 if self._compact else 20
        dc.SetPen(wx.Pen(wx.Colour(48, 48, 52), 1))
        for k in range(n_notch):
            a  = math.radians(k * 360 / n_notch)
            x1 = cx + int((Rr - 3) * math.cos(a))
            y1 = cy + int((Rr - 3) * math.sin(a))
            x2 = cx + int(Rr * math.cos(a))
            y2 = cy + int(Rr * math.sin(a))
            dc.DrawLine(x1, y1, x2, y2)

        # Face
        dc.SetPen(wx.Pen(wx.Colour(96, 94, 90), 1))
        dc.SetBrush(wx.Brush(_KNOB_FACE))
        dc.DrawCircle(cx, cy, Rf)

        # Indicator
        n     = max(len(self._divs) - 1, 1)
        angle = math.radians(-135 + (self._idx / n) * 270 - 90)
        ix    = cx + int((Rf - 3) * math.cos(angle))
        iy    = cy + int((Rf - 3) * math.sin(angle))
        dc.SetPen(wx.Pen(_IND_LINE, 2, wx.PENSTYLE_SOLID))
        dc.DrawLine(cx, cy, ix, iy)

        # Rivet
        dc.SetPen(wx.Pen(wx.Colour(50, 50, 48), 1))
        dc.SetBrush(wx.Brush(wx.Colour(62, 60, 58)))
        dc.DrawCircle(cx, cy, 3)

        # Scale ticks
        dc.SetPen(wx.Pen(_TEXT_DIM, 1))
        for k in range(9):
            a  = math.radians(-135 + k * 270 / 8 - 90)
            x1 = cx + int((Rr + 2) * math.cos(a))
            y1 = cy + int((Rr + 2) * math.sin(a))
            x2 = cx + int((Rr + 5) * math.cos(a))
            y2 = cy + int((Rr + 5) * math.sin(a))
            dc.DrawLine(x1, y1, x2, y2)

        lsz = 5 if self._compact else 6
        vsz = 5 if self._compact else 6

        dc.SetFont(wx.Font(lsz, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(_TEXT_DIM)
        lw = dc.GetTextExtent(self._label)[0]
        dc.DrawText(self._label, (W - lw) // 2, cy + Rr + 5)

        val_str = _fmt_eng(self._divs[self._idx], self._unit) + '/div'
        dc.SetFont(wx.Font(vsz, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(_TEXT)
        vw = dc.GetTextExtent(val_str)[0]
        dc.DrawText(val_str, (W - vw) // 2, cy + Rr + 14)


# ---------------------------------------------------------------------------
# _CouplingButton — DC / AC two-segment illuminated push button
# ---------------------------------------------------------------------------

class _CouplingButton(wx.Panel):
    _SEG_W, _H = 28, 18

    def __init__(self, parent, on_change: Optional[Callable] = None):
        W = self._SEG_W * 2 + 3
        super().__init__(parent, size=wx.Size(W, self._H))
        self.SetMinSize(wx.Size(W, self._H))
        self.SetBackgroundColour(_SECT_BG)
        self._coupling  = 'DC'
        self._on_change = on_change
        self.Bind(wx.EVT_PAINT,     self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_click)

    @property
    def coupling(self) -> str:
        return self._coupling

    def set_coupling(self, v: str) -> None:
        self._coupling = v
        self.Refresh()

    def _on_click(self, evt: wx.MouseEvent) -> None:
        new = 'DC' if evt.GetX() < self._SEG_W + 1 else 'AC'
        if new != self._coupling:
            self._coupling = new
            self.Refresh()
            if self._on_change:
                self._on_change(new)

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        H  = self._H
        sw = self._SEG_W
        dc.SetBackground(wx.Brush(_SECT_BG))
        dc.Clear()

        dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))

        for i, lbl in enumerate(('DC', 'AC')):
            x   = i * (sw + 3)
            lit = (lbl == self._coupling)
            dc.SetBrush(wx.Brush(_BTN_DOWN if lit else _BTN_FACE))
            dc.SetPen(wx.Pen(wx.Colour(28, 28, 30) if lit
                             else wx.Colour(110, 108, 104), 1))
            dc.DrawRectangle(x, 0, sw, H)
            dc.SetTextForeground(_IND_LINE if lit else _TEXT_DIM)
            tw, th = dc.GetTextExtent(lbl)
            dc.DrawText(lbl, x + (sw - tw) // 2, (H - th) // 2)


# ---------------------------------------------------------------------------
# ChannelSection — per-channel control strip
# ---------------------------------------------------------------------------

class ChannelSection(wx.Panel):
    """Full per-channel controls: V/DIV, POSITION, AC/DC, PROBE."""

    def __init__(self, parent,
                 ch_idx:            int,
                 state:             _ChanState,
                 on_probe_clicked:  Callable,    # (ch_idx, active: bool) → None
                 on_v_div_change:   Callable,    # (val) → None
                 on_pos_change:     Callable,    # (val) → None
                 on_coupling_change: Callable):  # (str) → None
        super().__init__(parent)
        self.SetBackgroundColour(_SECT_BG)
        self._ch_idx  = ch_idx
        self._state   = state
        self._on_probe_clicked = on_probe_clicked

        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(self._make_header(),                         0, wx.EXPAND)
        sz.Add(self._make_knob_row(on_v_div_change,
                                   on_pos_change),          0,
               wx.ALIGN_CENTRE_HORIZONTAL | wx.TOP, 3)
        sz.Add(self._make_ctrl_row(on_coupling_change),     0,
               wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 5)
        self.SetSizer(sz)

    # ── header ─────────────────────────────────────────────────────────

    def _make_header(self) -> wx.Panel:
        hdr = wx.Panel(self)
        hdr.SetBackgroundColour(_HDR_DARK)
        hdr.SetMinSize(wx.Size(-1, 20))
        sz = wx.BoxSizer(wx.HORIZONTAL)

        r, g, b = _hex_to_rgb(self._state.color)
        ch_lbl = wx.StaticText(hdr, label=f'CH{self._ch_idx + 1}')
        ch_lbl.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        ch_lbl.SetForegroundColour(wx.Colour(r, g, b))
        ch_lbl.SetBackgroundColour(_HDR_DARK)

        self._net_lbl = wx.StaticText(hdr, label=self._net_label())
        self._net_lbl.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE,
                                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self._net_lbl.SetForegroundColour(_SECT_LBL)
        self._net_lbl.SetBackgroundColour(_HDR_DARK)

        sz.Add(ch_lbl,       0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 8)
        sz.AddStretchSpacer()
        sz.Add(self._net_lbl, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        hdr.SetSizer(sz)
        return hdr

    # ── knobs ──────────────────────────────────────────────────────────

    def _make_knob_row(self, on_v_div_change, on_pos_change) -> wx.BoxSizer:
        row = wx.BoxSizer(wx.HORIZONTAL)

        def _v_changed(val):
            self._state.v_div_idx = _V_DIVS.index(val) if val in _V_DIVS \
                                    else self._state.v_div_idx
            on_v_div_change()

        def _p_changed(val):
            self._state.pos_idx = _POS_DIVS.index(val) if val in _POS_DIVS \
                                  else self._state.pos_idx
            on_pos_change()

        self._v_knob = KnobWidget(self, 'VOLTS/DIV', _V_DIVS, 'V',
                                   on_change=_v_changed, compact=True)
        self._v_knob.set_index(self._state.v_div_idx)

        self._p_knob = KnobWidget(self, 'POSITION', _POS_DIVS, 'div',
                                   on_change=_p_changed, compact=True)
        self._p_knob.set_index(self._state.pos_idx)

        row.Add(self._v_knob, 0, wx.LEFT, 3)
        row.Add(self._p_knob, 0, wx.LEFT, 4)
        return row

    # ── coupling + probe ───────────────────────────────────────────────

    def _make_ctrl_row(self, on_coupling_change) -> wx.BoxSizer:
        row = wx.BoxSizer(wx.HORIZONTAL)

        def _coup_changed(val):
            self._state.coupling = val
            on_coupling_change()

        self._coup_btn = _CouplingButton(self, on_change=_coup_changed)
        self._coup_btn.set_coupling(self._state.coupling)

        self._probe_btn = wx.ToggleButton(self, label='⦿ PROBE',
                                          size=wx.Size(-1, 20))
        self._probe_btn.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                                        wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self._probe_btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_probe_toggle)
        self._style_probe(False)

        row.Add(self._coup_btn,  0, wx.ALIGN_CENTRE_VERTICAL)
        row.AddStretchSpacer()
        row.Add(self._probe_btn, 0, wx.ALIGN_CENTRE_VERTICAL)
        return row

    # ── public ─────────────────────────────────────────────────────────

    def set_net(self, net_name: Optional[str]) -> None:
        self._state.net_name = net_name
        self._net_lbl.SetLabel(self._net_label())
        self._net_lbl.GetParent().Layout()

    def set_probe_active(self, active: bool) -> None:
        self._probe_btn.SetValue(active)
        self._style_probe(active)

    # ── internal ───────────────────────────────────────────────────────

    def _net_label(self) -> str:
        n = self._state.net_name
        return (n[:14] if len(n) > 14 else n) if n else '---'

    def _on_probe_toggle(self, _evt) -> None:
        active = self._probe_btn.GetValue()
        self._style_probe(active)
        self._on_probe_clicked(self._ch_idx, active)

    def _style_probe(self, active: bool) -> None:
        if active:
            self._probe_btn.SetBackgroundColour(wx.Colour(80, 52, 0))
            self._probe_btn.SetForegroundColour(wx.Colour(255, 198, 55))
        else:
            self._probe_btn.SetBackgroundColour(wx.NullColour)
            self._probe_btn.SetForegroundColour(wx.NullColour)


# ---------------------------------------------------------------------------
# _LedDot — painted LED in the header
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
# _BncConnector — decorative BNC input
# ---------------------------------------------------------------------------

class _BncConnector(wx.Panel):
    def __init__(self, parent: wx.Panel, label: str, color_hex: str,
                 net_name: Optional[str] = None):
        super().__init__(parent, size=wx.Size(74, 86))
        self.SetMinSize(wx.Size(74, 86))
        self.SetBackgroundColour(parent.GetBackgroundColour())
        self._label    = label
        self._color    = color_hex
        self._net_name = net_name
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def set_net(self, net_name: Optional[str]) -> None:
        self._net_name = net_name
        self.Refresh()

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        W, H = self.GetClientSize()
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()
        cx, cy = W // 2, 30    # BNC centre
        r, g, b = _hex_to_rgb(self._color)

        # Outer shell
        dc.SetPen(wx.Pen(wx.Colour(24, 24, 26), 1))
        dc.SetBrush(wx.Brush(wx.Colour(44, 44, 48)))
        dc.DrawCircle(cx, cy, 22)
        # Coloured ring
        dc.SetPen(wx.Pen(wx.Colour(r // 2, g // 2, b // 2), 4))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.DrawCircle(cx, cy, 17)
        # Insulator
        dc.SetPen(wx.Pen(wx.Colour(175, 170, 162), 1))
        dc.SetBrush(wx.Brush(wx.Colour(205, 200, 190)))
        dc.DrawCircle(cx, cy, 11)
        # Centre pin
        dc.SetPen(wx.Pen(wx.Colour(175, 172, 165), 1))
        dc.SetBrush(wx.Brush(wx.Colour(195, 192, 185)))
        dc.DrawCircle(cx, cy, 4)

        # Channel label in channel colour
        dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(wx.Colour(r, g, b))
        lw = dc.GetTextExtent(self._label)[0]
        dc.DrawText(self._label, (W - lw) // 2, cy + 24)

        # Net name (or "---") in small dim text
        net_lbl = (self._net_name[:10] if self._net_name and len(self._net_name) > 10
                   else (self._net_name or '---'))
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(_TEXT_DIM)
        nw = dc.GetTextExtent(net_lbl)[0]
        dc.DrawText(net_lbl, (W - nw) // 2, cy + 36)


# ---------------------------------------------------------------------------
# _SectionHeader — dark bar between sections
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


# ---------------------------------------------------------------------------
# WaveformFrame — the full KiScope oscilloscope window
# ---------------------------------------------------------------------------

class WaveformFrame(wx.Frame):
    """KiScope oscilloscope-style waveform viewer, Tektronix 2430A inspired."""

    def __init__(self, parent,
                 traces: Dict[str, TransientTrace],
                 on_probe_toggle: Optional[Callable[[bool], None]] = None):
        super().__init__(parent, title='KiScope',
                         size=(1080, 720),
                         style=wx.DEFAULT_FRAME_STYLE)
        self._traces          = traces
        self._on_probe_toggle = on_probe_toggle
        self._probing_channel:   Optional[int]          = None
        self._channel_sections: List[ChannelSection]   = []
        self._bnc_connectors:   List[_BncConnector]    = []

        # Compute a sensible default V/DIV from all data
        all_vals = [v for tr in traces.values() for v in (tr.values or [])]
        v_needed = ((max(all_vals) - min(all_vals)) / _NV) if all_vals else 1.0
        default_v_idx = _best_idx(_V_DIVS, v_needed)

        all_times = [t for tr in traces.values() for t in (tr.times or [])]
        t_needed  = (max(all_times) / _NH) if all_times else 1e-3
        self._init_t_idx = _best_idx(_T_DIVS, t_needed)

        # Auto-assign the first N nets (sorted) to channels
        sorted_nets = sorted(traces)
        self._channels: List[_ChanState] = [
            _ChanState(
                net_name=(sorted_nets[i] if i < len(sorted_nets) else None),
                color=_CH_COLORS[i % len(_CH_COLORS)],
                default_v_idx=default_v_idx,
            )
            for i in range(_NUM_CH)
        ]

        self._build()
        self.SetMinSize(wx.Size(640, 480))
        self.SetIcon(wx.NullIcon)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    # ------------------------------------------------------------------
    # Public API

    @property
    def probing_channel(self) -> Optional[int]:
        return self._probing_channel

    @property
    def channel_net_names(self) -> List[Optional[str]]:
        return [ch.net_name for ch in self._channels]

    def toggle_net(self, net_name: str) -> None:
        """Assign net_name to the probing channel, or cycle through empty slots."""
        if net_name not in self._traces:
            return
        if self._probing_channel is not None:
            ch = self._channels[self._probing_channel]
            ch.net_name = net_name
            self._channel_sections[self._probing_channel].set_net(net_name)
        else:
            for i, ch in enumerate(self._channels):
                if ch.net_name is None:
                    ch.net_name = net_name
                    self._channel_sections[i].set_net(net_name)
                    break
            else:
                self._channels[0].net_name = net_name
                self._channel_sections[0].set_net(net_name)
        self._screen.Refresh()
        self._update_bnc_labels()

    def deactivate_probe(self) -> None:
        self._probing_channel = None
        for sect in self._channel_sections:
            sect.set_probe_active(False)

    def _update_bnc_labels(self) -> None:
        for i, bnc in enumerate(self._bnc_connectors):
            bnc.set_net(self._channels[i].net_name)

    # ------------------------------------------------------------------

    def _build(self) -> None:
        body = wx.Panel(self)
        body.SetBackgroundColour(_BODY)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(self._make_header(body),       0, wx.EXPAND)
        outer.Add(self._make_main_row(body),     1, wx.EXPAND | wx.ALL, 5)
        outer.Add(self._make_bottom_strip(body), 0, wx.EXPAND)
        body.SetSizer(outer)
        fsz = wx.BoxSizer(wx.VERTICAL)
        fsz.Add(body, 1, wx.EXPAND)
        self.SetSizer(fsz)
        self.Layout()

    # ── header ─────────────────────────────────────────────────────────

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
        col.Add(sub,  0, wx.TOP, 3)
        sz.Add(col, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 14)
        sz.AddStretchSpacer()

        if self._traces:
            all_t = [t for tr in self._traces.values() for t in (tr.times or [])]
            if all_t:
                info = wx.StaticText(hdr,
                    label=f'{len(self._traces)} nets  ·  span {_fmt_eng(max(all_t), "s")}')
                info.SetFont(wx.Font(7, wx.FONTFAMILY_TELETYPE,
                                     wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
                info.SetForegroundColour(wx.Colour(0, 180, 65))
                info.SetBackgroundColour(_HDR_DARK)
                sz.Add(info, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 20)

        pwr = _LedDot(hdr, _LED_GRN, 'PWR')
        sz.Add(pwr, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 14)
        hdr.SetSizer(sz)
        return hdr

    # ── main row: screen + right panel ─────────────────────────────────

    def _make_main_row(self, parent: wx.Panel) -> wx.BoxSizer:
        row = wx.BoxSizer(wx.HORIZONTAL)

        bezel = wx.Panel(parent)
        bezel.SetBackgroundColour(_BEZEL)
        b_sz = wx.BoxSizer(wx.VERTICAL)
        self._screen = OscopeScreen(bezel, self._traces, self._channels)
        self._screen.set_t_div(_T_DIVS[self._init_t_idx])
        b_sz.Add(self._screen, 1, wx.EXPAND | wx.ALL, 8)
        bezel.SetSizer(b_sz)

        row.Add(bezel, 1, wx.EXPAND)
        row.Add(self._make_right_panel(parent), 0, wx.EXPAND | wx.LEFT, 5)
        return row

    # ── right panel ────────────────────────────────────────────────────

    def _make_right_panel(self, parent: wx.Panel) -> wx.Panel:
        rp = wx.Panel(parent)
        rp.SetBackgroundColour(_BODY)
        rp.SetMinSize(wx.Size(120, -1))

        # Use a scrolled window so any number of channels fits
        scr = wx.ScrolledWindow(rp, style=wx.VSCROLL)
        scr.SetScrollRate(0, 8)
        scr.SetBackgroundColour(_BODY)

        scr_sz = wx.BoxSizer(wx.VERTICAL)

        # One section per channel
        for i, ch in enumerate(self._channels):
            sect = ChannelSection(
                scr, i, ch,
                on_probe_clicked   = self._on_ch_probe_clicked,
                on_v_div_change    = self._screen.Refresh,
                on_pos_change      = self._screen.Refresh,
                on_coupling_change = self._screen.Refresh,
            )
            self._channel_sections.append(sect)
            scr_sz.Add(sect, 0, wx.EXPAND | (wx.TOP if i else 0), 4 if i else 0)

        # HORIZONTAL section
        scr_sz.Add(self._make_horizontal_section(scr), 0,
                   wx.EXPAND | wx.TOP, 6)

        scr.SetSizer(scr_sz)
        scr.FitInside()

        rp_sz = wx.BoxSizer(wx.VERTICAL)
        rp_sz.Add(scr, 1, wx.EXPAND)
        rp.SetSizer(rp_sz)
        return rp

    def _make_horizontal_section(self, parent: wx.Window) -> wx.Panel:
        sect = wx.Panel(parent)
        sect.SetBackgroundColour(_SECT_BG)
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(_SectionHeader(sect, 'HORIZONTAL'), 0, wx.EXPAND)

        knob_row = wx.BoxSizer(wx.HORIZONTAL)

        self._t_knob = KnobWidget(sect, 'TIME/DIV', _T_DIVS, 's',
                                   on_change=self._screen.set_t_div,
                                   compact=True)
        self._t_knob.set_index(self._init_t_idx)

        def _h_pos_changed(val):
            self._screen.set_h_pos(val)

        self._h_knob = KnobWidget(sect, 'POSITION', _POS_DIVS, 'div',
                                   on_change=_h_pos_changed, compact=True)
        self._h_knob.set_index(len(_POS_DIVS) // 2)

        knob_row.Add(self._t_knob, 0, wx.LEFT | wx.BOTTOM, 4)
        knob_row.Add(self._h_knob, 0, wx.LEFT | wx.BOTTOM, 4)
        sz.Add(knob_row, 0, wx.ALIGN_CENTRE_HORIZONTAL | wx.TOP, 3)

        sect.SetSizer(sz)
        return sect

    # ── bottom strip ───────────────────────────────────────────────────

    def _make_bottom_strip(self, parent: wx.Panel) -> wx.Panel:
        strip = wx.Panel(parent)
        strip.SetBackgroundColour(_SECT_BG)
        strip.SetMinSize(wx.Size(-1, 90))
        sz = wx.BoxSizer(wx.HORIZONTAL)
        for i in range(_NUM_CH):
            bnc = _BncConnector(strip, f'CH {i + 1}',
                                _CH_COLORS[i % len(_CH_COLORS)],
                                self._channels[i].net_name)
            self._bnc_connectors.append(bnc)
            sz.Add(bnc, 0, wx.LEFT | wx.TOP | wx.BOTTOM, 5 if i else 8)
        sz.AddStretchSpacer()
        lbl = wx.StaticText(strip, label='KiScope DSO BB-1')
        lbl.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        lbl.SetForegroundColour(_TEXT_DIM)
        lbl.SetBackgroundColour(_SECT_BG)
        sz.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 16)
        strip.SetSizer(sz)
        return strip

    # ── event handlers ─────────────────────────────────────────────────

    def _on_ch_probe_clicked(self, ch_idx: int, active: bool) -> None:
        if active:
            # Deactivate probe on all other channels
            for i, sect in enumerate(self._channel_sections):
                if i != ch_idx:
                    sect.set_probe_active(False)
            self._probing_channel = ch_idx
            if self._on_probe_toggle:
                self._on_probe_toggle(True)
        else:
            self._probing_channel = None
            if self._on_probe_toggle:
                self._on_probe_toggle(False)

    def _on_close(self, evt) -> None:
        if self._probing_channel is not None and self._on_probe_toggle:
            self._on_probe_toggle(False)
        evt.Skip()
