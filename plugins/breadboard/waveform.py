"""
WaveformFrame — KiScope digital-storage oscilloscope UI for transient analysis.

Public API:
    frame = WaveformFrame(parent, traces, on_probe_toggle=cb, warnings=[...])
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

# Cursor time-position steps (screen divisions, 0 = left edge, 10 = right edge)
_CURSOR_POS: List[float] = [round(i * 0.25, 2) for i in range(41)]  # 0.0 … 10.0

# CRT display knob tables
_INTENSITY_DIVS: List[float] = [0.10, 0.20, 0.30, 0.40, 0.50,
                                 0.60, 0.70, 0.80, 0.90, 1.00]
_FOCUS_DIVS: List[float] = [0.30, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00, 2.50, 3.00]

_NH = 10   # graticule divisions, horizontal
_NV = 8    # graticule divisions, vertical

_MEAS_H = 84   # pixel height of measurement overlay when enabled


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


def _detect_period(t_vals: list, v_vals: list) -> Optional[float]:
    """Estimate signal period from rising mean-crossings. Returns None if not periodic."""
    if len(v_vals) < 4:
        return None
    mean_v = sum(v_vals) / len(v_vals)
    crossings = []
    for i in range(1, len(v_vals)):
        if v_vals[i - 1] < mean_v <= v_vals[i] and t_vals[i] > t_vals[i - 1]:
            frac = (mean_v - v_vals[i - 1]) / (v_vals[i] - v_vals[i - 1])
            crossings.append(t_vals[i - 1] + frac * (t_vals[i] - t_vals[i - 1]))
    if len(crossings) < 2:
        return None
    periods = [crossings[k + 1] - crossings[k] for k in range(len(crossings) - 1)]
    return sum(periods) / len(periods)


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
        self.SetMinSize(wx.Size(420, 300))
        self._traces         = traces
        self._channels       = channels      # shared ref
        self._t_div:         Optional[float] = None
        self._h_pos:         float           = 0.0
        self._meas_enabled:  bool            = False
        self._cursors_enabled: bool          = False
        self._cursor1_div:   float           = 3.0
        self._cursor2_div:   float           = 7.0
        self._intensity:     float           = 1.0
        self._focus:         float           = 1.0
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE,  lambda _: self.Refresh())

    def set_t_div(self, v: float) -> None:
        self._t_div = v
        self.Refresh()

    def set_h_pos(self, v: float) -> None:
        self._h_pos = v
        self.Refresh()

    def set_measurements_enabled(self, enabled: bool) -> None:
        self._meas_enabled = enabled
        self.Refresh()

    def set_cursors_enabled(self, enabled: bool) -> None:
        self._cursors_enabled = enabled
        self.Refresh()

    def set_cursor_pos(self, which: int, div_pos: float) -> None:
        if which == 1:
            self._cursor1_div = div_pos
        else:
            self._cursor2_div = div_pos
        self.Refresh()

    def set_intensity(self, v: float) -> None:
        self._intensity = v
        self.Refresh()

    def set_focus(self, v: float) -> None:
        self._focus = v
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

        meas_h = _MEAS_H if self._meas_enabled else 0
        ph_plot = ph - meas_h   # trace area height

        self._draw_graticule(dc, p, p, pw, ph_plot)

        active = [
            (ch, self._traces[ch.net_name])
            for ch in self._channels
            if ch.net_name and ch.net_name in self._traces
               and self._traces[ch.net_name].times
        ]

        if not active:
            dc.SetTextForeground(wx.Colour(0, 52, 18))
            dc.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_ITALIC, wx.FONTWEIGHT_NORMAL))
            msg = 'Click  ⦿ PROBE  on a channel, then click a net on the breadboard'
            tw, th = dc.GetTextExtent(msg)
            dc.DrawText(msg, (W - tw) // 2, (H - th) // 2)
            if meas_h:
                self._draw_meas_panel(dc, p, p + ph_plot, pw, meas_h, active)
            return

        t_max    = max(max(tr.times) for _, tr in active)
        t_window = self._t_div * _NH if self._t_div else (t_max or 1e-3)
        t_off    = self._h_pos * t_window / _NH


        div_h = ph_plot / _NV   # pixels per division

        def tx(t: float) -> float:
            return p + (t - t_off) / t_window * pw

        try:
            gc = wx.GraphicsContext.Create(dc)
        except Exception:
            gc = None

        # 0 V reference markers — always visible arrow on left edge, clamped when
        # the zero line is outside the visible area.
        div_h = ph_plot / _NV
        aw = 9
        for ch in self._channels:
            if not (ch.net_name and ch.net_name in self._traces):
                continue
            r, g, b = _hex_to_rgb(ch.color)
            zero_y = max(p + aw, min(p + ph_plot - aw,
                                     int(p + ph_plot / 2 - ch.position * div_h)))
            dc.SetBrush(wx.Brush(wx.Colour(r, g, b)))
            dc.SetPen(wx.Pen(wx.Colour(r // 3, g // 3, b // 3), 1))
            dc.DrawPolygon([wx.Point(p,      zero_y),
                            wx.Point(p - aw, zero_y - 5),
                            wx.Point(p - aw, zero_y + 5)])

        # Collect pts per channel first (used for both drawing and clip detection)
        channel_pts = []
        for ch, trace in active:
            t_vals = list(trace.times)
            v_vals = list(trace.values)
            if ch.coupling == 'AC' and v_vals:
                mean   = sum(v_vals) / len(v_vals)
                v_vals = [v - mean for v in v_vals]

            v_div  = ch.v_div
            zero_y = p + ph_plot / 2 - ch.position * div_h
            scale  = div_h / v_div

            pts = [(tx(t), zero_y - v * scale)
                   for t, v in zip(t_vals, v_vals)
                   if p - 4 <= tx(t) <= p + pw + 4]
            channel_pts.append((ch, pts))

        for ch, pts in channel_pts:
            if len(pts) < 2:
                continue
            r, g, b = _hex_to_rgb(ch.color)
            self._draw_phosphor(dc, gc, pts, r, g, b)

        self._draw_clip_arrows(dc, p, pw, ph_plot, channel_pts)

        if self._cursors_enabled:
            self._draw_cursors(dc, p, pw, ph_plot, active, t_off, t_window)

        # Per-channel labels (right side, top-down)
        y_lbl = p + 5
        dc.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        for ch, _ in channel_pts:
            r, g, b = _hex_to_rgb(ch.color)
            ch_idx = self._channels.index(ch)
            net    = ch.net_name or ''
            lbl    = f'CH{ch_idx + 1}  {net}  {_fmt_eng(ch.v_div, "V")}/div'
            lw, lh = dc.GetTextExtent(lbl)
            # Dark background pill for readability
            dc.SetBrush(wx.Brush(wx.Colour(0, 0, 0, 160)))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawRectangle(p + pw - lw - 10, y_lbl - 1, lw + 8, lh + 2)
            dc.SetTextForeground(wx.Colour(r, g, b))
            dc.DrawText(lbl, p + pw - lw - 6, y_lbl)
            y_lbl += lh + 4

        # Time readout
        t_lbl = _fmt_eng(self._t_div or t_window / _NH, 's') + '/div'
        dc.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(wx.Colour(0, 148, 52))
        dc.DrawText(t_lbl, p + 4, p + ph_plot - 14)

        if meas_h:
            self._draw_meas_panel(dc, p, p + ph_plot, pw, meas_h, active)

    def _draw_clip_arrows(self, dc: wx.DC, p: int, pw: int, ph_plot: int,
                          channel_pts: list) -> None:
        """Draw small triangular arrows at top/bottom edge for clipped traces."""
        arrow_w, arrow_h = 10, 7
        gap = 14   # horizontal spacing between arrows

        clip_up = [(ch, pts) for ch, pts in channel_pts
                   if pts and any(y < p for _, y in pts)]
        clip_dn = [(ch, pts) for ch, pts in channel_pts
                   if pts and any(y > p + ph_plot for _, y in pts)]

        for slot, (ch, _) in enumerate(clip_up):
            r, g, b = _hex_to_rgb(ch.color)
            cx = p + 14 + slot * gap
            ty = p + 3
            dc.SetBrush(wx.Brush(wx.Colour(r, g, b)))
            dc.SetPen(wx.Pen(wx.Colour(r // 3, g // 3, b // 3), 1))
            dc.DrawPolygon([wx.Point(cx, ty),
                            wx.Point(cx - arrow_w // 2, ty + arrow_h),
                            wx.Point(cx + arrow_w // 2, ty + arrow_h)])

        for slot, (ch, _) in enumerate(clip_dn):
            r, g, b = _hex_to_rgb(ch.color)
            cx = p + 14 + slot * gap
            by = p + ph_plot - 3
            dc.SetBrush(wx.Brush(wx.Colour(r, g, b)))
            dc.SetPen(wx.Pen(wx.Colour(r // 3, g // 3, b // 3), 1))
            dc.DrawPolygon([wx.Point(cx, by),
                            wx.Point(cx - arrow_w // 2, by - arrow_h),
                            wx.Point(cx + arrow_w // 2, by - arrow_h)])

    # ── cursor overlay ────────────────────────────────────────────────────

    @staticmethod
    def _interp_v(t_vals: list, v_vals: list, t: float) -> float:
        if not t_vals:
            return 0.0
        if t <= t_vals[0]:
            return v_vals[0]
        if t >= t_vals[-1]:
            return v_vals[-1]
        lo, hi = 0, len(t_vals) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if t_vals[mid] <= t:
                lo = mid
            else:
                hi = mid
        span = t_vals[hi] - t_vals[lo]
        frac = (t - t_vals[lo]) / span if span else 0.0
        return v_vals[lo] + frac * (v_vals[hi] - v_vals[lo])

    def _draw_cursors(self, dc: wx.DC,
                      p: int, pw: int, ph_plot: int,
                      active: list, t_off: float, t_window: float) -> None:
        c1 = self._cursor1_div
        c2 = self._cursor2_div
        cx1 = p + int(c1 / 10 * pw)
        cx2 = p + int(c2 / 10 * pw)
        t1  = t_off + c1 / 10 * t_window
        t2  = t_off + c2 / 10 * t_window

        # Vertical cursor lines
        dc.SetPen(wx.Pen(wx.Colour(255, 220, 0), 1))
        dc.DrawLine(cx1, p, cx1, p + ph_plot)
        dc.SetPen(wx.Pen(wx.Colour(0, 210, 230), 1))
        dc.DrawLine(cx2, p, cx2, p + ph_plot)

        # Horizontal tick marks where each cursor crosses each trace
        div_h = ph_plot / _NV
        readings: list = []
        for ch, trace in active:
            t_vals = list(trace.times)
            v_vals = list(trace.values)
            if ch.coupling == 'AC' and v_vals:
                mean   = sum(v_vals) / len(v_vals)
                v_vals = [v - mean for v in v_vals]
            zero_y = p + ph_plot / 2 - ch.position * div_h
            scale  = div_h / ch.v_div
            r, g, b = _hex_to_rgb(ch.color)
            v1 = self._interp_v(t_vals, v_vals, t1)
            v2 = self._interp_v(t_vals, v_vals, t2)
            readings.append((ch, v1, v2))
            for cx, v in [(cx1, v1), (cx2, v2)]:
                vy = int(zero_y - v * scale)
                if p <= vy <= p + ph_plot:
                    dc.SetPen(wx.Pen(wx.Colour(r, g, b), 2))
                    dc.DrawLine(cx - 6, vy, cx + 6, vy)

        # Readout box — top-left corner, dark background
        lines = [
            (f'C1  {_fmt_eng(t1, "s")}', wx.Colour(255, 220, 0)),
        ]
        for ch, v1, _ in readings:
            r, g, b = _hex_to_rgb(ch.color)
            idx = self._channels.index(ch)
            lines.append((f'  CH{idx+1} {_fmt_eng(v1, "V")}', wx.Colour(r, g, b)))
        lines.append((f'C2  {_fmt_eng(t2, "s")}', wx.Colour(0, 210, 230)))
        for ch, _, v2 in readings:
            r, g, b = _hex_to_rgb(ch.color)
            idx = self._channels.index(ch)
            lines.append((f'  CH{idx+1} {_fmt_eng(v2, "V")}', wx.Colour(r, g, b)))
        lines.append((f'ΔT  {_fmt_eng(t2 - t1, "s")}', wx.Colour(180, 180, 180)))
        if readings:
            dv = readings[0][2] - readings[0][1]
            lines.append((f'ΔV  {_fmt_eng(dv, "V")}', wx.Colour(180, 180, 180)))

        dc.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        row_h = 11
        pad   = 4
        box_w = max(dc.GetTextExtent(txt)[0] for txt, _ in lines) + pad * 2
        box_h = len(lines) * row_h + pad * 2
        bx, by = p + 2, p + 2
        dc.SetBrush(wx.Brush(wx.Colour(0, 6, 2)))
        dc.SetPen(wx.Pen(wx.Colour(0, 60, 20), 1))
        dc.DrawRectangle(bx, by, box_w, box_h)
        y = by + pad
        for txt, col in lines:
            dc.SetTextForeground(col)
            dc.DrawText(txt, bx + pad, y)
            y += row_h

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
        i = self._intensity   # 0.1–1.0  brightness multiplier
        f = self._focus       # 0.3–3.0  glow-spread multiplier
        ir = int(r * i); ig = int(g * i); ib = int(b * i)
        if gc is not None:
            for width, col in [
                (8.0 * f, wx.Colour(ir // 8, ig // 8, ib // 8)),
                (3.5 * f, wx.Colour(ir // 3, ig // 3, ib // 3)),
                (1.5,     wx.Colour(ir,       ig,      ib)),
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
            dc.SetPen(wx.Pen(wx.Colour(ir, ig, ib), 2))
            dc.DrawLines([(int(x), int(y)) for x, y in pts])

    # ── measurement overlay ────────────────────────────────────────────

    def _draw_meas_panel(self, dc: wx.DC, ox: int, oy: int,
                         pw: int, mh: int,
                         active: list) -> None:
        # Dark background
        dc.SetBrush(wx.Brush(wx.Colour(0, 6, 2)))
        dc.SetPen(wx.Pen(wx.Colour(0, 60, 20), 1))
        dc.DrawRectangle(ox, oy, pw, mh)

        if not active:
            return

        n = len(active)
        col_w = pw // n

        dc.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        for col_i, (ch, trace) in enumerate(active):
            t_vals = list(trace.times)
            v_vals = list(trace.values)
            if ch.coupling == 'AC' and v_vals:
                mean   = sum(v_vals) / len(v_vals)
                v_vals = [v - mean for v in v_vals]

            m = self._compute_meas(t_vals, v_vals)
            r, g, b = _hex_to_rgb(ch.color)
            x0 = ox + col_i * col_w + 4
            y0 = oy + 4

            # Channel name header
            ch_idx = self._channels.index(ch)
            dc.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
            dc.SetTextForeground(wx.Colour(r, g, b))
            dc.DrawText(f'CH{ch_idx + 1}  {ch.net_name or ""}', x0, y0)
            y0 += 12

            dc.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE,
                               wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

            if m is None:
                dc.SetTextForeground(wx.Colour(0, 80, 30))
                dc.DrawText('no data', x0, y0)
                continue

            lbl_col = wx.Colour(0, 110, 40)
            val_col = wx.Colour(min(r + 60, 255), min(g + 60, 255), min(b + 60, 255))
            rows = [
                ('Vpp',    _fmt_eng(m['vpp'],    'V')),
                ('Vmax',   _fmt_eng(m['vmax'],   'V')),
                ('Vmin',   _fmt_eng(m['vmin'],   'V')),
                ('Vrms',   _fmt_eng(m['vrms'],   'V')),
                ('Freq',   _fmt_eng(m['freq'],   'Hz') if m['freq']   is not None else '---'),
                ('Period', _fmt_eng(m['period'], 's')  if m['period'] is not None else '---'),
            ]
            label_w = 32
            for label, val in rows:
                dc.SetTextForeground(lbl_col)
                dc.DrawText(label, x0, y0)
                dc.SetTextForeground(val_col)
                dc.DrawText(val, x0 + label_w, y0)
                y0 += 11

    @staticmethod
    def _compute_meas(t_vals: list, v_vals: list) -> Optional[dict]:
        if len(v_vals) < 4:
            return None
        vmax = max(v_vals)
        vmin = min(v_vals)
        vpp  = vmax - vmin
        vrms = math.sqrt(sum(v * v for v in v_vals) / len(v_vals))

        # Rising zero-crossings relative to the signal mean
        mean_v = sum(v_vals) / len(v_vals)
        crossings = []
        for i in range(1, len(v_vals)):
            if v_vals[i - 1] < mean_v <= v_vals[i] and t_vals[i] > t_vals[i - 1]:
                frac = (mean_v - v_vals[i - 1]) / (v_vals[i] - v_vals[i - 1])
                crossings.append(t_vals[i - 1] + frac * (t_vals[i] - t_vals[i - 1]))

        period = freq = None
        if len(crossings) >= 2:
            periods = [crossings[k + 1] - crossings[k] for k in range(len(crossings) - 1)]
            period = sum(periods) / len(periods)
            if period > 0:
                freq = 1.0 / period

        return dict(vpp=vpp, vmax=vmax, vmin=vmin, vrms=vrms,
                    freq=freq, period=period)


# ---------------------------------------------------------------------------
# _PushButton — vintage illuminated momentary push-button
# ---------------------------------------------------------------------------

class _PushButton(wx.Panel):
    """Backlit illuminated push-button, vintage test-equipment style."""

    def __init__(self, parent, label: str, color: wx.Colour,
                 size: Tuple[int, int] = (60, 58),
                 on_click: Optional[Callable] = None):
        super().__init__(parent, size=wx.Size(*size))
        self.SetMinSize(wx.Size(*size))
        self.SetBackgroundColour(_BODY)
        self._label   = label
        self._color   = color
        self._cb      = on_click
        self._pressed = False
        self.Bind(wx.EVT_PAINT,     self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_down)
        self.Bind(wx.EVT_LEFT_UP,   self._on_up)
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))

    def _on_down(self, _evt) -> None:
        self._pressed = True
        self.Refresh()

    def _on_up(self, _evt) -> None:
        self._pressed = False
        self.Refresh()
        if self._cb:
            self._cb()

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        W, H = self.GetClientSize()
        dc.SetBackground(wx.Brush(_BODY))
        dc.Clear()

        r, g, b = self._color.Red(), self._color.Green(), self._color.Blue()

        # LED dot at top centre
        lcy = 7
        if self._pressed:
            dc.SetPen(wx.Pen(wx.Colour(r // 2, g // 2, b // 2), 1))
            dc.SetBrush(wx.Brush(self._color))
        else:
            dc.SetPen(wx.Pen(wx.Colour(r // 5, g // 5, b // 5), 1))
            dc.SetBrush(wx.Brush(wx.Colour(r // 5, g // 5, b // 5)))
        dc.DrawCircle(W // 2, lcy, 4)

        # Button body
        bx, by = 3, 16
        bw, bh = W - 6, H - 20

        face      = wx.Colour(26, 24, 22) if self._pressed else wx.Colour(46, 44, 40)
        highlight = wx.Colour(18, 16, 14) if self._pressed else wx.Colour(68, 66, 60)
        shadow    = wx.Colour(16, 14, 12)

        # Drop shadow
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(wx.Colour(8, 8, 10)))
        dc.DrawRoundedRectangle(bx + 2, by + 2, bw, bh, 5)

        # Body
        dc.SetPen(wx.Pen(shadow, 1))
        dc.SetBrush(wx.Brush(face))
        dc.DrawRoundedRectangle(bx, by, bw, bh, 5)

        # Highlight streak (top edge)
        dc.SetPen(wx.Pen(highlight, 1))
        dc.DrawLine(bx + 5, by + 1, bx + bw - 6, by + 1)

        # Translucent legend plate (inner rectangle, slightly lighter)
        px, py = bx + 4, by + 4
        pw, ph = bw - 8, bh - 8
        plate_col = wx.Colour(r // 6, g // 6, b // 6) if not self._pressed \
                    else wx.Colour(r // 4, g // 4, b // 4)
        dc.SetPen(wx.Pen(plate_col, 1))
        dc.SetBrush(wx.Brush(plate_col))
        dc.DrawRoundedRectangle(px, py, pw, ph, 3)

        # Label text on the legend plate
        lines = self._label.split('\n')
        lbl_col = wx.Colour(min(r + 80, 255), min(g + 80, 255), min(b + 80, 255)) \
                  if self._pressed else self._color
        dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        total_h = len(lines) * 12
        ty = py + (ph - total_h) // 2
        for line in lines:
            dc.SetTextForeground(lbl_col)
            tw = dc.GetTextExtent(line)[0]
            dc.DrawText(line, px + (pw - tw) // 2, ty)
            ty += 12


# ---------------------------------------------------------------------------
# _ToggleSwitchWidget — vintage lever toggle switch
# ---------------------------------------------------------------------------

class _ToggleSwitchWidget(wx.Panel):
    """Physical lever-style toggle switch, horizontal orientation."""
    _W, _H = 72, 48

    def __init__(self, parent, label: str = '',
                 on_change: Optional[Callable[[bool], None]] = None):
        super().__init__(parent, size=wx.Size(self._W, self._H))
        self.SetMinSize(wx.Size(self._W, self._H))
        self.SetBackgroundColour(_SECT_BG)
        self._state: bool = False
        self._label       = label
        self._cb          = on_change
        self.Bind(wx.EVT_PAINT,     self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_click)
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))

    def GetValue(self) -> bool:
        return self._state

    def SetValue(self, v: bool) -> None:
        self._state = bool(v)
        self.Refresh()

    def _on_click(self, _evt) -> None:
        self._state = not self._state
        self.Refresh()
        if self._cb:
            self._cb(self._state)

    def _on_paint(self, _evt) -> None:
        dc = wx.PaintDC(self)
        W, H = self.GetClientSize()
        dc.SetBackground(wx.Brush(_SECT_BG))
        dc.Clear()

        hw, hh = 48, 20   # housing: wide slot
        hx = (W - hw) // 2
        hy = (H - hh) // 2 - 6   # shifted up slightly to leave room for label below

        # Outer housing — dark recessed slot
        dc.SetPen(wx.Pen(wx.Colour(10, 10, 12), 2))
        dc.SetBrush(wx.Brush(wx.Colour(16, 16, 18)))
        dc.DrawRoundedRectangle(hx, hy, hw, hh, 4)

        # Centre ridge — vertical divider line
        dc.SetPen(wx.Pen(wx.Colour(8, 8, 10), 1))
        dc.DrawLine(hx + hw // 2, hy + 3, hx + hw // 2, hy + hh - 3)

        # Lever slides left (OFF) / right (ON)
        lw, lh = 22, 14
        ly = hy + (hh - lh) // 2
        if self._state:
            lx        = hx + hw - lw - 3   # right = ON
            face      = wx.Colour(205, 200, 190)
            highlight = wx.Colour(245, 242, 236)
            shadow    = wx.Colour(120, 118, 112)
            txt_col   = wx.Colour(35, 32, 28)
        else:
            lx        = hx + 3              # left = OFF
            face      = wx.Colour(80, 78, 74)
            highlight = wx.Colour(108, 106, 100)
            shadow    = wx.Colour(40, 38, 35)
            txt_col   = wx.Colour(180, 176, 168)

        # Lever shadow
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(wx.Colour(6, 6, 8)))
        dc.DrawRoundedRectangle(lx + 2, ly + 2, lw, lh, 3)

        # Lever body
        dc.SetPen(wx.Pen(shadow, 1))
        dc.SetBrush(wx.Brush(face))
        dc.DrawRoundedRectangle(lx, ly, lw, lh, 3)

        # Top highlight streak
        dc.SetPen(wx.Pen(highlight, 1))
        dc.DrawLine(lx + 2, ly + 1, lx + lw - 3, ly + 1)

        # ON / OFF label on lever
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(txt_col)
        lbl = 'ON' if self._state else 'OFF'
        tw, th = dc.GetTextExtent(lbl)
        dc.DrawText(lbl, lx + (lw - tw) // 2, ly + (lh - th) // 2)

        # Component label below housing
        if self._label:
            dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
            dc.SetTextForeground(_TEXT_DIM)
            tw = dc.GetTextExtent(self._label)[0]
            dc.DrawText(self._label, (W - tw) // 2, hy + hh + 4)

        # Small LED dot below label — green when on, dark when off
        led_y = hy + hh + (16 if self._label else 6)
        if self._state:
            dc.SetPen(wx.Pen(wx.Colour(0, 140, 36), 1))
            dc.SetBrush(wx.Brush(_LED_GRN))
        else:
            dc.SetPen(wx.Pen(wx.Colour(15, 32, 15), 1))
            dc.SetBrush(wx.Brush(wx.Colour(0, 32, 8)))
        dc.DrawCircle(W // 2, led_y, 3)


# ---------------------------------------------------------------------------
# KnobWidget — Tektronix-style rotary knob
# ---------------------------------------------------------------------------

class KnobWidget(wx.Panel):
    """Knob with dark outer ring and white indicator. compact=True for channel sections."""

    def __init__(self, parent, label: str, divs: List[float], unit: str,
                 on_change: Optional[Callable] = None, compact: bool = False,
                 size_factor: float = 1.0,
                 val_fmt: Optional[Callable[[float], str]] = None):
        sf = size_factor
        Rr = int((15 if compact else 26) * sf)
        Rf = int((11 if compact else 19) * sf)
        w  = int((54 if compact else 76) * sf)
        h  = int((64 if compact else 90) * sf)
        super().__init__(parent, size=wx.Size(w, h))
        self.SetMinSize(wx.Size(w, h))
        self.SetBackgroundColour(_SECT_BG)
        self._Rr        = Rr
        self._Rf        = Rf
        self._sf        = sf
        self._label     = label
        self._divs      = divs
        self._unit      = unit
        self._idx       = len(divs) // 2
        self._on_change = on_change
        self._compact   = compact
        self._val_fmt   = val_fmt
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
        sf = self._sf
        cx = W // 2
        cy = Rr + int((7 if self._compact else 10) * sf)

        # Shadow
        dc.SetPen(wx.Pen(_KNOB_SHAD, 1))
        dc.SetBrush(wx.Brush(_KNOB_SHAD))
        dc.DrawCircle(cx + int(2 * sf), cy + int(2 * sf), Rr + int(2 * sf))

        # Outer ring
        dc.SetPen(wx.Pen(wx.Colour(52, 52, 56), 1))
        dc.SetBrush(wx.Brush(_KNOB_RING))
        dc.DrawCircle(cx, cy, Rr)

        # Serration notches
        n_notch = 16 if self._compact else 20
        dc.SetPen(wx.Pen(wx.Colour(48, 48, 52), 1))
        for k in range(n_notch):
            a  = math.radians(k * 360 / n_notch)
            x1 = cx + int((Rr - int(3 * sf)) * math.cos(a))
            y1 = cy + int((Rr - int(3 * sf)) * math.sin(a))
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
        ix    = cx + int((Rf - int(3 * sf)) * math.cos(angle))
        iy    = cy + int((Rf - int(3 * sf)) * math.sin(angle))
        dc.SetPen(wx.Pen(_IND_LINE, max(1, int(2 * sf)), wx.PENSTYLE_SOLID))
        dc.DrawLine(cx, cy, ix, iy)

        # Rivet
        dc.SetPen(wx.Pen(wx.Colour(50, 50, 48), 1))
        dc.SetBrush(wx.Brush(wx.Colour(62, 60, 58)))
        dc.DrawCircle(cx, cy, max(2, int(3 * sf)))

        # Scale ticks
        dc.SetPen(wx.Pen(_TEXT_DIM, 1))
        for k in range(9):
            a  = math.radians(-135 + k * 270 / 8 - 90)
            x1 = cx + int((Rr + int(2 * sf)) * math.cos(a))
            y1 = cy + int((Rr + int(2 * sf)) * math.sin(a))
            x2 = cx + int((Rr + int(5 * sf)) * math.cos(a))
            y2 = cy + int((Rr + int(5 * sf)) * math.sin(a))
            dc.DrawLine(x1, y1, x2, y2)

        lsz = max(7, int((7 if self._compact else 8) * sf))
        vsz = max(7, int((7 if self._compact else 8) * sf))

        dc.SetFont(wx.Font(lsz, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(_TEXT_DIM)
        lw = dc.GetTextExtent(self._label)[0]
        dc.DrawText(self._label, (W - lw) // 2, cy + Rr + int(5 * sf))

        if self._val_fmt is not None:
            val_str = self._val_fmt(self._divs[self._idx])
        else:
            val_str = _fmt_eng(self._divs[self._idx], self._unit) + '/div'
        dc.SetFont(wx.Font(vsz, wx.FONTFAMILY_TELETYPE,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(_TEXT)
        vw = dc.GetTextExtent(val_str)[0]
        dc.DrawText(val_str, (W - vw) // 2, cy + Rr + int(14 * sf))


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

        dc.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
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
# ChannelSection — per-channel control strip (vertical layout)
# ---------------------------------------------------------------------------

class ChannelSection(wx.Panel):
    """Per-channel controls stacked vertically: header, VOLTS/DIV, POSITION, coupling, probe."""

    def __init__(self, parent,
                 ch_idx:            int,
                 state:             _ChanState,
                 on_probe_clicked:  Callable,    # (ch_idx, active: bool) → None
                 on_v_div_change:   Callable,    # () → None
                 on_pos_change:     Callable,    # () → None
                 on_coupling_change: Callable):  # () → None
        super().__init__(parent)
        self.SetBackgroundColour(_SECT_BG)
        self._ch_idx  = ch_idx
        self._state   = state
        self._on_probe_clicked = on_probe_clicked

        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(self._make_header(),                              0, wx.EXPAND)
        sz.Add(self._make_v_knob(on_v_div_change),              0,
               wx.ALIGN_CENTRE_HORIZONTAL | wx.TOP, 4)
        sz.Add(self._make_p_knob(on_pos_change),                0,
               wx.ALIGN_CENTRE_HORIZONTAL | wx.TOP, 4)
        sz.Add(self._make_ctrl_row(on_coupling_change),         0,
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
        ch_lbl.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        ch_lbl.SetForegroundColour(wx.Colour(r, g, b))
        ch_lbl.SetBackgroundColour(_HDR_DARK)

        self._net_lbl = wx.StaticText(hdr, label=self._net_label())
        self._net_lbl.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE,
                                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self._net_lbl.SetForegroundColour(wx.WHITE)
        self._net_lbl.SetBackgroundColour(_HDR_DARK)

        sz.Add(ch_lbl,       0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 8)
        sz.AddStretchSpacer()
        sz.Add(self._net_lbl, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 8)
        hdr.SetSizer(sz)
        return hdr

    # ── knobs (separate, stacked) ──────────────────────────────────────

    def _make_v_knob(self, on_v_div_change) -> KnobWidget:
        def _v_changed(val):
            self._state.v_div_idx = _V_DIVS.index(val) if val in _V_DIVS \
                                    else self._state.v_div_idx
            on_v_div_change()

        self._v_knob = KnobWidget(self, 'VOLTS/DIV', _V_DIVS, 'V',
                                   on_change=_v_changed, compact=True)
        self._v_knob.set_index(self._state.v_div_idx)
        return self._v_knob

    def _make_p_knob(self, on_pos_change) -> KnobWidget:
        def _p_changed(val):
            self._state.pos_idx = _POS_DIVS.index(val) if val in _POS_DIVS \
                                  else self._state.pos_idx
            on_pos_change()

        self._p_knob = KnobWidget(self, 'POSITION', _POS_DIVS, 'div',
                                   on_change=_p_changed, compact=True)
        self._p_knob.set_index(self._state.pos_idx)
        return self._p_knob

    # ── coupling + probe ───────────────────────────────────────────────

    def _make_ctrl_row(self, on_coupling_change) -> wx.BoxSizer:
        col = wx.BoxSizer(wx.VERTICAL)

        def _coup_changed(val):
            self._state.coupling = val
            on_coupling_change()

        self._coup_btn = _CouplingButton(self, on_change=_coup_changed)
        self._coup_btn.set_coupling(self._state.coupling)

        self._probe_btn = wx.ToggleButton(self, label='⦿ PROBE',
                                          size=wx.Size(-1, 20))
        self._probe_btn.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                                        wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self._probe_btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_probe_toggle)
        self._style_probe(False)

        col.Add(self._coup_btn,  0, wx.ALIGN_CENTRE_HORIZONTAL | wx.BOTTOM, 3)
        col.Add(self._probe_btn, 0, wx.EXPAND)
        return col

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
        dc.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
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
        cx, cy = W // 2, 30
        r, g, b = _hex_to_rgb(self._color)

        dc.SetPen(wx.Pen(wx.Colour(24, 24, 26), 1))
        dc.SetBrush(wx.Brush(wx.Colour(44, 44, 48)))
        dc.DrawCircle(cx, cy, 22)
        dc.SetPen(wx.Pen(wx.Colour(r // 2, g // 2, b // 2), 4))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.DrawCircle(cx, cy, 17)
        dc.SetPen(wx.Pen(wx.Colour(175, 170, 162), 1))
        dc.SetBrush(wx.Brush(wx.Colour(205, 200, 190)))
        dc.DrawCircle(cx, cy, 11)
        dc.SetPen(wx.Pen(wx.Colour(175, 172, 165), 1))
        dc.SetBrush(wx.Brush(wx.Colour(195, 192, 185)))
        dc.DrawCircle(cx, cy, 4)

        dc.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(wx.Colour(r, g, b))
        lw = dc.GetTextExtent(self._label)[0]
        dc.DrawText(self._label, (W - lw) // 2, cy + 24)

        net_lbl = (self._net_name[:10] if self._net_name and len(self._net_name) > 10
                   else (self._net_name or '---'))
        dc.SetFont(wx.Font(10, wx.FONTFAMILY_TELETYPE,
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
        lbl.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT,
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
                 on_probe_toggle: Optional[Callable[[bool], None]] = None,
                 warnings: Optional[List[str]] = None,
                 num_channels: int = _NUM_CH):
        super().__init__(parent, title='KiScope',
                         size=(1080, 720),
                         style=wx.DEFAULT_FRAME_STYLE)
        self._traces          = traces
        self._on_probe_toggle = on_probe_toggle
        self._warnings        = warnings or []
        self._num_channels    = max(1, min(num_channels, _NUM_CH))
        self._probing_channel:   Optional[int]          = None
        self._channel_sections: List[ChannelSection]   = []
        self._bnc_connectors:   List[_BncConnector]    = []

        all_vals = [v for tr in traces.values() for v in (tr.values or [])]
        v_needed = ((max(all_vals) - min(all_vals)) / _NV) if all_vals else 1.0
        default_v_idx = _best_idx(_V_DIVS, v_needed)

        all_times = [t for tr in traces.values() for t in (tr.times or [])]
        t_needed  = (max(all_times) / _NH) if all_times else 1e-3
        self._init_t_idx = _best_idx(_T_DIVS, t_needed)

        sorted_nets = sorted(traces)
        self._channels: List[_ChanState] = [
            _ChanState(
                net_name=(sorted_nets[i] if i < len(sorted_nets) else None),
                color=_CH_COLORS[i % len(_CH_COLORS)],
                default_v_idx=default_v_idx,
            )
            for i in range(self._num_channels)
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

        # Screen created first so knob callbacks can reference it
        bezel = wx.Panel(body)
        bezel.SetBackgroundColour(_BEZEL)
        self._screen = OscopeScreen(bezel, self._traces, self._channels)
        self._screen.set_t_div(_T_DIVS[self._init_t_idx])
        b_sz = wx.BoxSizer(wx.VERTICAL)
        b_sz.Add(self._screen, 1, wx.EXPAND | wx.ALL, 8)
        bezel.SetSizer(b_sz)

        # CRT knobs column (FOCUS + INTEN + AUTOSET) left of the screen
        crt_knobs = wx.Panel(body)
        crt_knobs.SetBackgroundColour(_BODY)
        ck_sz = wx.BoxSizer(wx.VERTICAL)
        ck_sz.AddStretchSpacer()
        self._focus_knob = KnobWidget(
            crt_knobs, 'FOCUS', _FOCUS_DIVS, '',
            val_fmt=lambda v: f'{v:.2g}×',
            on_change=self._screen.set_focus, compact=True)
        self._focus_knob.set_index(_FOCUS_DIVS.index(1.0))
        ck_sz.Add(self._focus_knob, 0, wx.ALIGN_CENTRE_HORIZONTAL | wx.BOTTOM, 12)
        self._inten_knob = KnobWidget(
            crt_knobs, 'INTEN', _INTENSITY_DIVS, '',
            val_fmt=lambda v: f'{int(v * 100)}%',
            on_change=self._screen.set_intensity, compact=True)
        self._inten_knob.set_index(len(_INTENSITY_DIVS) - 1)
        ck_sz.Add(self._inten_knob, 0, wx.ALIGN_CENTRE_HORIZONTAL | wx.BOTTOM, 16)
        _autoset_btn = _PushButton(
            crt_knobs, 'AUTO\nSET',
            color=wx.Colour(220, 140, 0),
            size=(60, 58),
            on_click=self._on_autoset,
        )
        _autoset_btn.SetToolTip('Auto-scale all channels to fit the waveforms on screen')
        ck_sz.Add(_autoset_btn, 0, wx.ALIGN_CENTRE_HORIZONTAL | wx.BOTTOM, 12)
        ck_sz.AddStretchSpacer()
        crt_knobs.SetSizer(ck_sz)

        # Left area: CRT knobs + bezel side by side
        crt_row = wx.BoxSizer(wx.HORIZONTAL)
        crt_row.Add(crt_knobs, 0, wx.EXPAND)
        crt_row.Add(bezel, 1, wx.EXPAND | wx.ALL, 5)

        left_sz = wx.BoxSizer(wx.VERTICAL)
        left_sz.Add(crt_row, 1, wx.EXPAND)

        # Right panel: horizontal controls → measure → channels
        right = wx.Panel(body)
        right.SetBackgroundColour(_BODY)
        right.SetMinSize(wx.Size(380, -1))
        right_sz = wx.BoxSizer(wx.VERTICAL)
        right_sz.Add(self._make_top_controls(right), 0, wx.EXPAND | wx.ALL, 4)
        right_sz.Add(self._make_measure_section(right), 0, wx.EXPAND | wx.ALL, 4)
        right_sz.AddStretchSpacer(1)
        right_sz.Add(self._make_bottom_channels(right), 0, wx.EXPAND | wx.ALL, 4)
        right.SetSizer(right_sz)

        # Two-column body, header spans full width above
        main = wx.BoxSizer(wx.HORIZONTAL)
        main.Add(left_sz, 1, wx.EXPAND)
        main.Add(right,   0, wx.EXPAND | wx.RIGHT | wx.BOTTOM, 4)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(self._make_header(body), 0, wx.EXPAND)
        if self._warnings:
            outer.Add(self._make_warning_bar(body), 0, wx.EXPAND)
        outer.Add(main, 1, wx.EXPAND)
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
        sub.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        sub.SetForegroundColour(wx.Colour(155, 153, 148))
        sub.SetBackgroundColour(_HDR_DARK)

        sz.Add(logo, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 14)
        sz.Add(sub,  0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 12)
        sz.AddStretchSpacer()

        if self._traces:
            all_t = [t for tr in self._traces.values() for t in (tr.times or [])]
            if all_t:
                info = wx.StaticText(hdr,
                    label=f'{len(self._traces)} nets  ·  span {_fmt_eng(max(all_t), "s")}')
                info.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                                     wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
                info.SetForegroundColour(wx.Colour(0, 180, 65))
                info.SetBackgroundColour(_HDR_DARK)
                sz.Add(info, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 20)

        pwr = _LedDot(hdr, _LED_GRN, 'PWR')
        sz.Add(pwr, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 14)
        hdr.SetSizer(sz)
        return hdr

    # ── simulation warning bar ──────────────────────────────────────────

    def _make_warning_bar(self, parent: wx.Panel) -> wx.Panel:
        n = len(self._warnings)
        # First warning gives the most useful detail (e.g. "R1 (LED): no SPICE model")
        first = self._warnings[0] if self._warnings else ''
        extra = f' (+{n - 1} more)' if n > 1 else ''
        text = f'⚠  {first}{extra}  —  results may be inaccurate'

        bar = wx.Panel(parent)
        bar.SetBackgroundColour(wx.Colour(80, 55, 10))
        sz = wx.BoxSizer(wx.HORIZONTAL)

        lbl = wx.StaticText(bar, label=text)
        lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        lbl.SetForegroundColour(wx.Colour(255, 200, 80))
        lbl.SetBackgroundColour(wx.Colour(80, 55, 10))
        sz.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT | wx.TOP | wx.BOTTOM, 5)

        bar.SetSizer(sz)
        return bar

    # ── top controls: horizontal section + measurements ─────────────────

    def _make_top_controls(self, parent: wx.Panel) -> wx.Panel:
        ctrl = wx.Panel(parent)
        ctrl.SetBackgroundColour(_SECT_BG)
        outer_sz = wx.BoxSizer(wx.VERTICAL)

        # Full-width HORIZONTAL banner
        hdr_lbl = wx.Panel(ctrl)
        hdr_lbl.SetBackgroundColour(_HDR_DARK)
        hl_sz = wx.BoxSizer(wx.HORIZONTAL)
        hl_txt = wx.StaticText(hdr_lbl, label='HORIZONTAL')
        hl_txt.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        hl_txt.SetForegroundColour(_SECT_LBL)
        hl_txt.SetBackgroundColour(_HDR_DARK)
        hl_sz.Add(hl_txt, 0, wx.ALIGN_CENTRE_VERTICAL | wx.ALL, 5)
        hdr_lbl.SetSizer(hl_sz)
        outer_sz.Add(hdr_lbl, 0, wx.EXPAND)

        # Dials row: large TIME/DIV + compact POSITION
        dials_sz = wx.BoxSizer(wx.HORIZONTAL)

        self._t_knob = KnobWidget(ctrl, 'TIME/DIV', _T_DIVS, 's',
                                   on_change=self._screen.set_t_div,
                                   compact=False, size_factor=2.0)
        self._t_knob.set_index(self._init_t_idx)
        dials_sz.Add(self._t_knob, 0, wx.ALIGN_CENTRE_VERTICAL | wx.ALL, 6)

        def _h_pos_changed(val):
            self._screen.set_h_pos(val)

        self._h_knob = KnobWidget(ctrl, 'POSITION', _POS_DIVS, 'div',
                                   on_change=_h_pos_changed, compact=True)
        self._h_knob.set_index(len(_POS_DIVS) // 2)
        dials_sz.Add(self._h_knob, 0, wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, 6)

        outer_sz.Add(dials_sz, 0)

        ctrl.SetSizer(outer_sz)
        return ctrl

    # ── middle: measure section ─────────────────────────────────────────

    def _make_measure_section(self, parent: wx.Panel) -> wx.Panel:
        ctrl = wx.Panel(parent)
        ctrl.SetBackgroundColour(_SECT_BG)
        outer_sz = wx.BoxSizer(wx.VERTICAL)

        # Full-width MEASURE banner
        hdr = wx.Panel(ctrl)
        hdr.SetBackgroundColour(_HDR_DARK)
        h_sz = wx.BoxSizer(wx.HORIZONTAL)
        h_txt = wx.StaticText(hdr, label='MEASURE')
        h_txt.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT,
                              wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        h_txt.SetForegroundColour(_SECT_LBL)
        h_txt.SetBackgroundColour(_HDR_DARK)
        h_sz.Add(h_txt, 0, wx.ALIGN_CENTRE_VERTICAL | wx.ALL, 5)
        hdr.SetSizer(h_sz)
        outer_sz.Add(hdr, 0, wx.EXPAND)

        # Single row: MEAS switch · CUR switch · CUR1 knob · CUR2 knob
        row_sz = wx.BoxSizer(wx.HORIZONTAL)

        self._meas_toggle = _ToggleSwitchWidget(
            ctrl, label='MEAS',
            on_change=self._screen.set_measurements_enabled)
        self._meas_toggle.SetToolTip('Enable / disable measurement overlay')
        row_sz.Add(self._meas_toggle, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT | wx.TOP | wx.BOTTOM, 6)

        self._cur_toggle = _ToggleSwitchWidget(
            ctrl, label='CUR',
            on_change=self._screen.set_cursors_enabled)
        self._cur_toggle.SetToolTip('Enable / disable time cursors')
        row_sz.Add(self._cur_toggle, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT | wx.TOP | wx.BOTTOM, 6)

        _cur_fmt = lambda v: f'{v:.2f} div'
        self._c1_knob = KnobWidget(ctrl, 'CUR 1', _CURSOR_POS, '',
                                    val_fmt=_cur_fmt, compact=True,
                                    on_change=lambda v: self._on_cursor_move(1, v))
        self._c1_knob.set_index(12)   # 3.0 div
        row_sz.Add(self._c1_knob, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 6)

        self._c2_knob = KnobWidget(ctrl, 'CUR 2', _CURSOR_POS, '',
                                    val_fmt=_cur_fmt, compact=True,
                                    on_change=lambda v: self._on_cursor_move(2, v))
        self._c2_knob.set_index(28)   # 7.0 div
        row_sz.Add(self._c2_knob, 0, wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, 4)

        outer_sz.Add(row_sz, 0)
        ctrl.SetSizer(outer_sz)
        return ctrl

    # ── bottom: 4 channel columns side by side ──────────────────────────

    def _make_bottom_channels(self, parent: wx.Panel) -> wx.Panel:
        strip = wx.Panel(parent)
        strip.SetBackgroundColour(_SECT_BG)
        sz = wx.BoxSizer(wx.HORIZONTAL)

        for i, ch in enumerate(self._channels):
            col_sz = wx.BoxSizer(wx.VERTICAL)

            sect = ChannelSection(
                strip, i, ch,
                on_probe_clicked   = self._on_ch_probe_clicked,
                on_v_div_change    = self._screen.Refresh,
                on_pos_change      = self._screen.Refresh,
                on_coupling_change = self._screen.Refresh,
            )
            self._channel_sections.append(sect)

            bnc = _BncConnector(strip, f'CH {i + 1}',
                                _CH_COLORS[i % len(_CH_COLORS)],
                                ch.net_name)
            self._bnc_connectors.append(bnc)

            col_sz.Add(sect, 0, wx.EXPAND)
            col_sz.Add(bnc,  0, wx.ALIGN_CENTRE_HORIZONTAL | wx.BOTTOM, 4)

            sz.Add(col_sz, 0, wx.LEFT, 6 if i == 0 else 2)

            if i < self._num_channels - 1:
                sep = wx.StaticLine(strip, style=wx.LI_VERTICAL)
                sz.Add(sep, 0, wx.EXPAND | wx.TOP | wx.BOTTOM | wx.LEFT, 4)

        sz.AddStretchSpacer()

        brand = wx.StaticText(strip, label='KiScope DSO BB-1')
        brand.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT,
                              wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        brand.SetForegroundColour(_TEXT_DIM)
        brand.SetBackgroundColour(_SECT_BG)
        sz.Add(brand, 0, wx.ALIGN_BOTTOM | wx.RIGHT | wx.BOTTOM, 12)

        strip.SetSizer(sz)
        return strip

    # ── event handlers ─────────────────────────────────────────────────

    def _on_cursor_move(self, which: int, div_pos: float) -> None:
        self._screen.set_cursor_pos(which, div_pos)

    def _on_autoset(self, _evt=None) -> None:
        """Stack each active channel in its own vertical band; show ~3 periods."""
        active_chs = [ch for ch in self._channels
                      if ch.net_name and ch.net_name in self._traces
                      and self._traces[ch.net_name].times]
        if not active_chs:
            return

        n = len(active_chs)
        # Each channel occupies _NV/n divisions; use 75 % of that for headroom
        band_div = _NV / n * 0.75

        for slot, ch in enumerate(active_chs):
            vals = list(self._traces[ch.net_name].values)
            if not vals:
                continue

            # V/DIV: fit peak-to-peak inside the channel's vertical band
            vpp    = max(vals) - min(vals)
            needed = (vpp / band_div) if band_div > 0 and vpp > 0 else 0.001
            ch.v_div_idx = _best_idx(_V_DIVS, needed)
            v_div  = _V_DIVS[ch.v_div_idx]

            # Band centre in screen divisions (positive = up from screen centre)
            # slot 0 → top band, slot n-1 → bottom band
            band_centre = 4.0 - (slot + 0.5) * _NV / n

            # Position offset so the signal mean lands at the band centre
            mean = 0.0 if ch.coupling == 'AC' else sum(vals) / len(vals)
            target_pos = band_centre - mean / v_div
            best_pos   = min(_POS_DIVS, key=lambda p: abs(p - target_pos))
            ch.pos_idx = _POS_DIVS.index(best_pos)

            # Sync the knob widgets
            sect = self._channel_sections[self._channels.index(ch)]
            sect._v_knob.set_index(ch.v_div_idx)
            sect._p_knob.set_index(ch.pos_idx)

        # TIME/DIV: aim for 3 periods of the fastest periodic signal on screen.
        # Fall back to fitting the full simulation span if no period is found.
        best_t_div: Optional[float] = None
        for ch in active_chs:
            t_vals = list(self._traces[ch.net_name].times)
            v_vals = list(self._traces[ch.net_name].values)
            period = _detect_period(t_vals, v_vals)
            if period and period > 0:
                candidate = period * 3 / _NH   # 3 periods across 10 divisions
                if best_t_div is None or candidate < best_t_div:
                    best_t_div = candidate

        if best_t_div is None:
            all_times = [t for ch in active_chs
                         for t in self._traces[ch.net_name].times]
            t_span    = max(all_times) - min(all_times)
            best_t_div = t_span / (_NH * 0.8)

        new_t_idx = _best_idx(_T_DIVS, best_t_div)
        self._t_knob.set_index(new_t_idx)
        self._h_knob.set_index(len(_POS_DIVS) // 2)
        self._screen.set_t_div(_T_DIVS[new_t_idx])
        self._screen.set_h_pos(0.0)
        self._screen.Refresh()

    def _on_ch_probe_clicked(self, ch_idx: int, active: bool) -> None:
        if active:
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
