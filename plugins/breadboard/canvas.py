"""
Breadboard canvas — wxPython panel that renders the breadboard and handles
all student interaction: drag-drop component placement, wire drawing, deletion.

Coordinate system
-----------------
Canvas pixels are computed from breadboard addresses by CanvasLayout.
  x  increases rightward  (column direction)
  y  increases downward

Layout (top to bottom):
  MARGIN
  Top power rails  (top_plus = red, top_minus = blue)
  RAIL_GAP
  Top tie-strip bank (rows a–e)
  CENTER_GAP   (the physical gap between the two banks)
  Bottom tie-strip bank (rows f–j)
  RAIL_GAP
  Bottom power rails (bot_plus = red, bot_minus = blue)
  MARGIN

Terminals (GND, V1, V2) are drawn as labelled boxes on the left side.

Interaction modes
-----------------
  MODE_SELECT  : left-click selects / moves placed components
  MODE_WIRE    : first click = wire start, second click = wire end
  MODE_DELETE  : left-click on a component or wire removes it

Placement flow (replaces drag-drop)
------------------------------------
  Click a card in the tray → canvas.begin_place(comp_def, ref) is called.
  A ghost preview follows the mouse.  Left-click on a valid hole places the
  component.  Right-click or Escape cancels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import wx

from .model import (
    Breadboard, PlacedComponent, Wire,
    TieHole, RailHole, Terminal, Hole,
    COLUMNS, TOP_ROWS, BOT_ROWS, ALL_ROWS,
    RAIL_NAMES, RAIL_LEN, RAIL_SPLIT, TERMINAL_NAMES,
    ComponentDef, ALL_DEFS,
    Netlist, guess_type_id,
    validate, IssueKind,
)

# ---------------------------------------------------------------------------
# Layout constants (pixels)
# ---------------------------------------------------------------------------
PITCH = 18          # distance between adjacent holes
HOLE_R = 3          # hole dot radius
RAIL_H = 18         # height of each power rail colour strip
RAIL_GAP = 8        # gap between rail area and tie-strip area
CENTER_GAP = 28     # gap between top and bottom tie-strip banks
MARGIN = 20         # outer margin
RAIL_BREAK_PX = 22  # extra pixel gap between the two rail halves
RAIL_GROUP_GAP = 27 # extra gap inserted between each group of 5 rail holes

# Binding posts (circular)
TERM_R = 18         # radius of binding-post circle
TERM_CX = TERM_R + 8   # x-centre of all binding posts (from canvas left edge)
TERM_COLORS = {
    'GND': ('#3a3a3a', '#707070'),   # (body colour, highlight ring colour)
    'V1':  ('#bb2020', '#ee7070'),
    'V2':  ('#1a3aaa', '#6688ee'),
}

WIRE_COLORS = [
    '#e8c020',  # yellow
    '#e04040',  # red
    '#4040e0',  # blue
    '#20c040',  # green
    '#e08020',  # orange
    '#c020c0',  # purple
    '#20c0c0',  # cyan
    '#808080',  # grey
]

MODE_SELECT = 'select'
MODE_WIRE = 'wire'
MODE_DELETE = 'delete'


# ---------------------------------------------------------------------------
# Layout helper
# ---------------------------------------------------------------------------

class CanvasLayout:
    """Maps breadboard addresses to canvas pixel coordinates."""

    def __init__(self):
        # --- y-coordinates ---
        top_minus_y = MARGIN
        top_plus_y  = top_minus_y + RAIL_H + 2

        tie_top_start_y = top_plus_y + RAIL_H + RAIL_GAP

        self._row_y: Dict[str, int] = {}
        for i, row in enumerate(TOP_ROWS):
            self._row_y[row] = tie_top_start_y + i * PITCH

        tie_bot_start_y = self._row_y['e'] + PITCH + CENTER_GAP
        for i, row in enumerate(BOT_ROWS):
            self._row_y[row] = tie_bot_start_y + i * PITCH

        bot_plus_y  = self._row_y['j'] + PITCH + RAIL_GAP
        bot_minus_y = bot_plus_y + RAIL_H + 2

        self._rail_y = {
            'top_plus':  top_plus_y  + RAIL_H // 2,
            'top_minus': top_minus_y + RAIL_H // 2,
            'bot_plus':  bot_plus_y  + RAIL_H // 2,
            'bot_minus': bot_minus_y + RAIL_H // 2,
        }

        self.total_height = bot_minus_y + RAIL_H + MARGIN

        # --- x-coordinates ---
        # Binding posts sit on the left; board starts well to the right of them
        self.board_left = TERM_CX + TERM_R + 36   # x of tie-strip column 1

        # Binding post y-positions: evenly distributed across the board height
        n = len(TERMINAL_NAMES)
        v_margin = int(self.total_height * 0.18)
        spacing = (self.total_height - 2 * v_margin) // (n - 1)
        self._term_y = {
            name: v_margin + i * spacing
            for i, name in enumerate(TERMINAL_NAMES)
        }

    def col_x(self, col: int) -> int:
        """x pixel of tie-strip column col (1-based)."""
        return self.board_left + (col - 1) * PITCH

    def rail_x(self, index: int) -> int:
        """x pixel of rail hole index (1-based), with group-of-5 and mid-board gaps.

        Groups of 5 holes have RAIL_GROUP_GAP between them so that the rails
        span the same visual width as the 63-column tie strip.  The mid-board
        split (at RAIL_SPLIT) uses RAIL_BREAK_PX instead of RAIL_GROUP_GAP.
        """
        n_gaps = (index - 1) // 5
        if index > RAIL_SPLIT:
            n_gaps -= 1   # split boundary is accounted for by RAIL_BREAK_PX below
        x = self.board_left + (index - 1) * PITCH + n_gaps * RAIL_GROUP_GAP
        if index > RAIL_SPLIT:
            x += RAIL_BREAK_PX
        return x

    def hole_xy(self, hole: Hole) -> Optional[Tuple[int, int]]:
        """Return (x, y) centre of a hole, or None if not renderable."""
        if isinstance(hole, TieHole):
            return self.col_x(hole.col), self._row_y[hole.row]
        if isinstance(hole, RailHole):
            return self.rail_x(hole.index), self._rail_y[hole.rail]
        if isinstance(hole, Terminal):
            return TERM_CX, self._term_y[hole.name]
        return None

    def total_width(self) -> int:
        return self.board_left + COLUMNS * PITCH + MARGIN

    def nearest_hole(self, px: int, py: int) -> Optional[Hole]:
        """Return the hole closest to canvas pixel (px, py), within snap radius."""
        best: Optional[Hole] = None
        best_d = PITCH  # snap radius = one hole pitch

        # Tie strip holes
        for col in range(1, COLUMNS + 1):
            cx = self.col_x(col)
            if abs(cx - px) > best_d:
                continue
            for row in ALL_ROWS:
                ry = self._row_y[row]
                d = math.hypot(cx - px, ry - py)
                if d < best_d:
                    best_d = d
                    best = TieHole(col, row)

        # Rail holes
        for rail, ry in self._rail_y.items():
            if abs(ry - py) > best_d:
                continue
            for idx in range(1, RAIL_LEN + 1):
                rx = self.rail_x(idx)
                d = math.hypot(rx - px, ry - py)
                if d < best_d:
                    best_d = d
                    best = RailHole(rail, idx)

        # Terminals
        for t_name in TERMINAL_NAMES:
            t = Terminal(t_name)
            xy = self.hole_xy(t)
            if xy:
                d = math.hypot(xy[0] - px, xy[1] - py)
                if d < best_d:
                    best_d = d
                    best = t

        return best


# ---------------------------------------------------------------------------
# Ghost: preview of a component being dragged onto the canvas
# ---------------------------------------------------------------------------

@dataclass
class DragGhost:
    comp_def: ComponentDef
    ref: str
    anchor: Optional[TieHole] = None   # snapped hole for pin 1


# ---------------------------------------------------------------------------
# BreadboardCanvas
# ---------------------------------------------------------------------------

class BreadboardCanvas(wx.Panel):

    def __init__(self, parent, board: Breadboard, netlist: Optional[Netlist] = None):
        super().__init__(parent, style=wx.WANTS_CHARS)
        self.board = board
        self.netlist = netlist
        self.layout = CanvasLayout()

        self.mode = MODE_SELECT
        self._wire_start: Optional[Hole] = None
        self._wire_color_idx = 0

        self._ghost: Optional[DragGhost] = None      # component pending placement
        self._ghost_pos: Tuple[int, int] = (0, 0)    # current mouse pos

        self._selected_ref: Optional[str] = None     # selected placed component
        self._selected_wire: Optional[Wire] = None   # selected wire
        self._drag_comp: Optional[str] = None        # ref being repositioned on board
        self._drag_offset: Tuple[int, int] = (0, 0)  # mouse offset from pin-1 hole

        self._highlighted_holes: Set[Hole] = set()   # from validation
        self._highlight_kind: Optional[IssueKind] = None
        # (x, y, IssueKind) for each validation issue with locatable holes
        self._validation_icons: List[Tuple[int, int, IssueKind]] = []

        # Called with ref after a component is successfully placed
        self.on_placed: Optional[callable] = None

        # Draw offset for centering the board in the canvas (updated on each paint)
        self._draw_offset: Tuple[int, int] = (0, 0)

        self.SetMinSize((self.layout.total_width(), self.layout.total_height))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)
        self.Bind(wx.EVT_LEFT_UP, self._on_left_up)
        self.Bind(wx.EVT_MOTION, self._on_motion)
        self.Bind(wx.EVT_RIGHT_DOWN, self._on_right_down)
        self.Bind(wx.EVT_KEY_DOWN, self._on_key_down)

    # ------------------------------------------------------------------
    # Public API (called from window / tray)
    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._wire_start = None
        self._selected_wire = None
        self._selected_ref = None
        self.Refresh()

    def begin_place(self, comp_def: ComponentDef, ref: str) -> None:
        """Called from the tray when a component card is clicked."""
        self._ghost = DragGhost(comp_def=comp_def, ref=ref)
        self.SetFocus()   # so key events (Escape) reach the canvas
        self.Refresh()

    def _commit_place(self, px: int, py: int) -> bool:
        """Place the current ghost at canvas position (px, py). Returns True on success."""
        if self._ghost is None:
            return False
        anchor = self.layout.nearest_hole(px, py)
        if not isinstance(anchor, TieHole):
            return False
        comp_def = self._ghost.comp_def
        ref = self._ghost.ref
        try:
            pin_holes = comp_def.place(anchor)
        except (AssertionError, IndexError, KeyError):
            return False
        placed = PlacedComponent(ref=ref, type_id=comp_def.type_id, pin_holes=pin_holes)
        self.board.place(placed)
        self._ghost = None
        if self.on_placed:
            self.on_placed(ref)
        self.Refresh()
        return True

    def set_highlighted(self, holes: Set[Hole], kind: Optional[IssueKind] = None) -> None:
        self._highlighted_holes = holes
        self._highlight_kind = kind
        self.Refresh()

    def clear_highlights(self) -> None:
        self._highlighted_holes = set()
        self._highlight_kind = None
        self._validation_icons.clear()
        self.Refresh()

    def set_validation_result(self, result) -> None:
        """Store validation issues; position icons at the relevant component."""
        all_holes: Set[Hole] = set()
        self._validation_icons.clear()
        hole_set = set()
        for issue in result.issues:
            if not issue.holes:
                continue
            hole_set = set(issue.holes)
            icon_xy = None
            # Prefer a placed-component pin hole so the icon sits on the board
            for placed in self.board.placements.values():
                for hole in placed.pin_holes.values():
                    if hole in hole_set:
                        xy = self.layout.hole_xy(hole)
                        if xy:
                            icon_xy = xy
                            break
                if icon_xy:
                    break
            if icon_xy is None:
                # Fallback: centroid of all renderable holes
                xys = [self.layout.hole_xy(h) for h in issue.holes
                       if self.layout.hole_xy(h) is not None]
                if xys:
                    icon_xy = (sum(x for x, y in xys) // len(xys),
                               sum(y for x, y in xys) // len(xys))
            if icon_xy:
                # Offset upward so the badge doesn't cover the hole dot
                self._validation_icons.append((icon_xy[0], icon_xy[1] - 14, issue.kind))
            all_holes.update(issue.holes)
        self._highlighted_holes = all_holes
        self._highlight_kind = result.issues[0].kind if result.issues else None
        self.Refresh()

    def next_wire_color(self) -> str:
        c = WIRE_COLORS[self._wire_color_idx % len(WIRE_COLORS)]
        self._wire_color_idx += 1
        return c

    def _board_pos(self, px: int, py: int) -> Tuple[int, int]:
        """Convert a window-pixel mouse position to board-pixel coordinates."""
        ox, oy = self._draw_offset
        return px - ox, py - oy

    # ------------------------------------------------------------------
    # Mouse event handlers
    # ------------------------------------------------------------------

    def _on_key_down(self, evt: wx.KeyEvent) -> None:
        key = evt.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self._ghost = None
            self._wire_start = None
            self._drag_comp = None
            self._selected_wire = None
            self.Refresh()
        elif key in (wx.WXK_DELETE, wx.WXK_BACK):
            if self._selected_wire is not None:
                self.board.remove_wire(self._selected_wire)
                self._selected_wire = None
                self.Refresh()
            elif self._selected_ref is not None:
                self.board.remove(self._selected_ref)
                if self.on_placed:
                    self.on_placed(self._selected_ref)
                self._selected_ref = None
                self.Refresh()
        else:
            evt.Skip()

    def _on_left_down(self, evt: wx.MouseEvent) -> None:
        px, py = self._board_pos(*evt.GetPosition())

        # Placement mode: ghost is active — click to place, anywhere to cancel wire
        if self._ghost is not None:
            self._commit_place(px, py)
            return

        if self.mode == MODE_WIRE:
            hole = self.layout.nearest_hole(px, py)
            if hole is not None:
                if self._wire_start is None:
                    self._wire_start = hole
                else:
                    if hole != self._wire_start:
                        self.board.add_wire(self._wire_start, hole,
                                            color=self.next_wire_color())
                    self._wire_start = None
                    self.Refresh()
            return

        if self.mode == MODE_DELETE:
            self._try_delete(px, py)
            return

        if self.mode == MODE_SELECT:
            ref = self._comp_at(px, py)
            if ref:
                self._selected_ref = ref
                self._selected_wire = None
                self._drag_comp = ref
                p = self.board.get_placement(ref)
                if p:
                    pin1_hole = p.pin_holes.get(1)
                    if pin1_hole:
                        xy = self.layout.hole_xy(pin1_hole)
                        if xy:
                            self._drag_offset = (px - xy[0], py - xy[1])
                self.Refresh()
                self.CaptureMouse()
            else:
                wire = self._wire_at(px, py)
                self._selected_wire = wire
                self._selected_ref = None
                self.Refresh()

    def _on_left_up(self, evt: wx.MouseEvent) -> None:
        if self.HasCapture():
            self.ReleaseMouse()
        if self._drag_comp:
            px, py = self._board_pos(*evt.GetPosition())
            px -= self._drag_offset[0]
            py -= self._drag_offset[1]
            new_anchor = self.layout.nearest_hole(px, py)
            if isinstance(new_anchor, TieHole):
                p = self.board.get_placement(self._drag_comp)
                if p:
                    comp_def = ALL_DEFS.get(p.type_id)
                    if comp_def:
                        try:
                            new_pins = comp_def.place(new_anchor)
                            p.pin_holes = new_pins
                        except (AssertionError, IndexError, KeyError):
                            pass
            self._drag_comp = None
            self.Refresh()

    def _on_motion(self, evt: wx.MouseEvent) -> None:
        px, py = self._board_pos(*evt.GetPosition())
        self._ghost_pos = (px, py)
        if self._ghost:
            anchor = self.layout.nearest_hole(px, py)
            self._ghost.anchor = anchor if isinstance(anchor, TieHole) else None
        self.Refresh()

    def _on_right_down(self, evt: wx.MouseEvent) -> None:
        px, py = self._board_pos(*evt.GetPosition())
        # Right-click on a terminal opens the net assignment dialog
        term = self._terminal_at(px, py)
        if term is not None:
            self._assign_terminal(term)
            return
        # Otherwise cancel the current operation
        self._wire_start = None
        self._ghost = None
        self._drag_comp = None
        self.Refresh()

    def _terminal_at(self, px: int, py: int) -> Optional[Terminal]:
        """Return the Terminal whose circle contains pixel (px, py), or None."""
        for name in TERMINAL_NAMES:
            t = Terminal(name)
            xy = self.layout.hole_xy(t)
            if xy and math.hypot(xy[0] - px, xy[1] - py) <= TERM_R + 4:
                return t
        return None

    def _assign_terminal(self, terminal: Terminal) -> None:
        """Show a net-selection dialog and update the terminal assignment."""
        if self.netlist is None:
            wx.MessageBox('Load a netlist first.', 'Terminal Assignment',
                          wx.OK | wx.ICON_INFORMATION)
            return
        net_names = sorted(net.name for net in self.netlist.nets if net.name)
        choices = ['(unassigned)'] + net_names
        current = self.board.get_terminal_net(terminal.name)
        with wx.SingleChoiceDialog(
            self,
            f'Assign binding post "{terminal.name}" to a schematic net.\n'
            f'Students will wire this post to the breadboard.',
            f'Assign {terminal.name}',
            choices,
        ) as dlg:
            if current in net_names:
                dlg.SetSelection(net_names.index(current) + 1)
            if dlg.ShowModal() == wx.ID_OK:
                sel = dlg.GetSelection()
                self.board.assign_terminal(
                    terminal.name,
                    net_names[sel - 1] if sel > 0 else '',
                )
                self.Refresh()

    # ------------------------------------------------------------------
    # Hit testing helpers
    # ------------------------------------------------------------------

    def _comp_at(self, px: int, py: int) -> Optional[str]:
        """Return ref of the component whose body contains pixel (px, py)."""
        for ref, p in self.board.placements.items():
            for hole in p.pin_holes.values():
                xy = self.layout.hole_xy(hole)
                if xy and math.hypot(xy[0] - px, xy[1] - py) < PITCH * 1.5:
                    return ref
        return None

    def _wire_at(self, px: int, py: int) -> Optional[Wire]:
        """Return the wire closest to pixel (px, py), within click tolerance."""
        TOLERANCE = 6
        best_wire = None
        best_d = float('inf')
        for w in self.board.wires:
            xy1 = self.layout.hole_xy(w.h1)
            xy2 = self.layout.hole_xy(w.h2)
            if xy1 is None or xy2 is None:
                continue
            d = _point_to_segment_dist(px, py, xy1[0], xy1[1], xy2[0], xy2[1])
            if d < TOLERANCE and d < best_d:
                best_d = d
                best_wire = w
        return best_wire

    def _try_delete(self, px: int, py: int) -> None:
        ref = self._comp_at(px, py)
        if ref:
            self.board.remove(ref)
            if self._selected_ref == ref:
                self._selected_ref = None
            self.Refresh()
            return
        w = self._wire_at(px, py)
        if w:
            self.board.remove_wire(w)
            self.Refresh()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _on_paint(self, _evt) -> None:
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush('#f0f0f0'))
        dc.Clear()
        # Centre the board in whatever space is available
        cw, ch = self.GetClientSize()
        ox = max(0, (cw - self.layout.total_width()) // 2)
        oy = max(0, (ch - self.layout.total_height) // 2)
        self._draw_offset = (ox, oy)
        dc.SetDeviceOrigin(ox, oy)
        self._draw_board(dc)

    def _draw_board(self, dc: wx.DC) -> None:
        lay = self.layout

        # Board body
        board_rect = wx.Rect(
            lay.board_left - MARGIN // 2,
            MARGIN // 2,
            COLUMNS * PITCH + MARGIN,
            lay.total_height - MARGIN,
        )
        dc.SetBrush(wx.Brush('#e8e0c8'))
        dc.SetPen(wx.Pen('#b0a090', 1))
        dc.DrawRoundedRectangle(board_rect, 8)

        self._draw_rails(dc)
        self._draw_center_gap(dc)
        self._draw_holes(dc)
        self._draw_wires(dc)
        self._draw_components(dc)
        self._draw_terminals(dc)

        if self._ghost:
            self._draw_ghost(dc)

        if self._wire_start:
            self._draw_wire_start_indicator(dc)

        self._draw_column_labels(dc)
        self._draw_validation_icons(dc)

    def _draw_rails(self, dc: wx.DC) -> None:
        lay = self.layout
        rail_colors = {
            'top_plus': '#cc2222', 'top_minus': '#2244cc',
            'bot_plus': '#cc2222', 'bot_minus': '#2244cc',
        }
        strip_h = RAIL_H - 4   # coloured stripe height

        for rail, ry in lay._rail_y.items():
            color = rail_colors[rail]

            # Draw one stripe per group of 5 holes — the gaps between groups
            # are naturally visible as breaks in the stripe.
            for group in range(10):
                first = group * 5 + 1
                last  = group * 5 + 5
                x_left  = lay.rail_x(first) - PITCH // 2
                x_right = lay.rail_x(last)  + PITCH // 2
                stripe = wx.Rect(x_left, ry - strip_h // 2, x_right - x_left, strip_h)
                dc.SetBrush(wx.Brush(color))
                dc.SetPen(wx.Pen(color, 0))
                dc.DrawRoundedRectangle(stripe, 3)

            # Holes
            for idx in range(1, RAIL_LEN + 1):
                rx = lay.rail_x(idx)
                self._draw_hole_dot(dc, rx, ry, RailHole(rail, idx))

            # + / − symbol at both ends
            symbol = '+' if 'plus' in rail else '−'
            dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD))
            dc.SetTextForeground('#ffffff')
            dc.DrawText(symbol, lay.rail_x(1) - PITCH + 1, ry - 7)
            dc.DrawText(symbol, lay.rail_x(RAIL_LEN) + 4, ry - 7)

    def _draw_center_gap(self, dc: wx.DC) -> None:
        lay = self.layout
        gap_y_top = lay._row_y['e'] + PITCH // 2
        gap_y_bot = lay._row_y['f'] - PITCH // 2
        gap_rect = wx.Rect(
            lay.board_left - MARGIN // 4,
            gap_y_top,
            COLUMNS * PITCH + MARGIN // 2,
            gap_y_bot - gap_y_top,
        )
        dc.SetBrush(wx.Brush('#c0b898'))
        dc.SetPen(wx.Pen('#a09080', 1))
        dc.DrawRectangle(gap_rect)

    def _draw_holes(self, dc: wx.DC) -> None:
        lay = self.layout
        for col in range(1, COLUMNS + 1):
            cx = lay.col_x(col)
            for row in ALL_ROWS:
                ry = lay._row_y[row]
                h = TieHole(col, row)
                self._draw_hole_dot(dc, cx, ry, h)

    def _draw_hole_dot(self, dc: wx.DC, cx: int, cy: int, hole: Hole) -> None:
        if hole in self._highlighted_holes:
            color = '#ff4444' if self._highlight_kind == IssueKind.SHORT else '#ffaa00'
            dc.SetBrush(wx.Brush(color))
            dc.SetPen(wx.Pen(color, 1))
            dc.DrawCircle(cx, cy, HOLE_R + 2)
        elif self.board.is_hole_occupied(hole):
            dc.SetBrush(wx.Brush('#888888'))
            dc.SetPen(wx.Pen('#555555', 1))
            dc.DrawCircle(cx, cy, HOLE_R)
        else:
            dc.SetBrush(wx.Brush('#444444'))
            dc.SetPen(wx.Pen('#222222', 1))
            dc.DrawCircle(cx, cy, HOLE_R)

    def _draw_wires(self, dc: wx.DC) -> None:
        lay = self.layout
        for wire in self.board.wires:
            xy1 = lay.hole_xy(wire.h1)
            xy2 = lay.hole_xy(wire.h2)
            if xy1 is None or xy2 is None:
                continue
            selected = (wire is self._selected_wire)
            width = 5 if selected else 3
            color = '#ffffff' if selected else wire.color
            # Selection halo
            if selected:
                dc.SetPen(wx.Pen(wx.Colour(wire.color), 7))
                dc.DrawLine(xy1[0], xy1[1], xy2[0], xy2[1])
            dc.SetPen(wx.Pen(wx.Colour(color), width))
            dc.DrawLine(xy1[0], xy1[1], xy2[0], xy2[1])
            # End dots
            dc.SetBrush(wx.Brush(color))
            dc.SetPen(wx.Pen(color, 1))
            dc.DrawCircle(xy1[0], xy1[1], 4)
            dc.DrawCircle(xy2[0], xy2[1], 4)

    def _draw_components(self, dc: wx.DC) -> None:
        for ref, placed in self.board.placements.items():
            comp_def = ALL_DEFS.get(placed.type_id)
            if comp_def is None:
                continue
            selected = (ref == self._selected_ref)
            self._draw_placed_component(dc, ref, placed, comp_def, selected)

    def _draw_placed_component(self, dc: wx.DC, ref: str,
                                placed: PlacedComponent, comp_def: ComponentDef,
                                selected: bool) -> None:
        lay = self.layout
        holes = [lay.hole_xy(h) for h in placed.pin_holes.values() if lay.hole_xy(h)]
        if not holes:
            return

        xs = [xy[0] for xy in holes]
        ys = [xy[1] for xy in holes]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

        body_color = comp_def.color
        border_color = '#224422' if selected else '#333333'

        dc.SetBrush(wx.Brush(body_color))
        dc.SetPen(wx.Pen(border_color, 2 if selected else 1))

        if comp_def.is_dip:
            # IC body as rectangle spanning the gap
            body_rect = wx.Rect(x_min - 4, y_min - 2, x_max - x_min + 8, y_max - y_min + 4)
            dc.DrawRoundedRectangle(body_rect, 3)
            # Notch on left side
            dc.SetBrush(wx.Brush('#ffffff'))
            dc.SetPen(wx.Pen(border_color, 1))
            dc.DrawCircle(x_min, (y_min + y_max) // 2, 4)
        elif comp_def.pin_count == 2:
            # Axial body: pill shape between the two pin holes
            p1 = lay.hole_xy(placed.pin_holes[1])
            p2 = lay.hole_xy(placed.pin_holes[2])
            if p1 and p2:
                # Lead lines
                dc.SetPen(wx.Pen('#888888', 1))
                mid1_x = p1[0] + (p2[0] - p1[0]) // 4
                mid2_x = p1[0] + 3 * (p2[0] - p1[0]) // 4
                dc.DrawLine(p1[0], p1[1], mid1_x, p1[1])
                dc.DrawLine(mid2_x, p1[1], p2[0], p2[1])
                # Body
                dc.SetBrush(wx.Brush(body_color))
                dc.SetPen(wx.Pen(border_color, 1))
                body_w = (mid2_x - mid1_x)
                body_rect = wx.Rect(mid1_x, p1[1] - 5, body_w, 10)
                dc.DrawRoundedRectangle(body_rect, 4)
        else:
            # 3-pin components (TO-92, POT): simple rectangle
            body_rect = wx.Rect(x_min - 3, y_min - 5, x_max - x_min + 6, 10)
            dc.DrawRoundedRectangle(body_rect, 3)

        # Reference label
        dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground('#ffffff' if comp_def.is_dip else '#222222')
        label_x = (x_min + x_max) // 2
        label_y = (y_min + y_max) // 2 - 5
        dc.DrawText(ref, label_x - dc.GetTextExtent(ref).Width // 2, label_y)

    def _draw_terminals(self, dc: wx.DC) -> None:
        lay = self.layout
        for name in TERMINAL_NAMES:
            t = Terminal(name)
            xy = lay.hole_xy(t)
            if xy is None:
                continue
            cx, cy = xy
            body_color, highlight_color = TERM_COLORS[name]
            assigned = self.board.get_terminal_net(name)

            # Drop shadow
            dc.SetBrush(wx.Brush('#888888'))
            dc.SetPen(wx.Pen('#888888', 0))
            dc.DrawCircle(cx + 2, cy + 2, TERM_R)

            # Outer body (hex-nut-like ring effect via two filled circles)
            dc.SetBrush(wx.Brush(body_color))
            dc.SetPen(wx.Pen('#111111', 2))
            dc.DrawCircle(cx, cy, TERM_R)

            # Threaded-shaft ring (lighter, inner circle)
            dc.SetBrush(wx.Brush(highlight_color))
            dc.SetPen(wx.Pen('#888888', 1))
            dc.DrawCircle(cx, cy, TERM_R - 6)

            # Center hole
            dc.SetBrush(wx.Brush('#111111'))
            dc.SetPen(wx.Pen('#000000', 1))
            dc.DrawCircle(cx, cy, 5)

            # Bright wire-entry dot
            dc.SetBrush(wx.Brush('#e0e0e0'))
            dc.SetPen(wx.Pen('#aaaaaa', 0))
            dc.DrawCircle(cx, cy, 3)

            # Glow ring if assigned
            if assigned:
                dc.SetBrush(wx.TRANSPARENT_BRUSH)
                dc.SetPen(wx.Pen('#ffffff', 1, wx.PENSTYLE_DOT))
                dc.DrawCircle(cx, cy, TERM_R + 3)

            # Name label (below the circle)
            dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD))
            dc.SetTextForeground('#222222')
            tw = dc.GetTextExtent(name).Width
            dc.DrawText(name, cx - tw // 2, cy + TERM_R + 3)

            # Net assignment (small, below name)
            net_label = assigned if assigned else 'right-click'
            dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_NORMAL))
            dc.SetTextForeground('#446644' if assigned else '#999999')
            # Truncate if too wide
            max_w = TERM_R * 2 + 8
            while dc.GetTextExtent(net_label).Width > max_w and len(net_label) > 4:
                net_label = net_label[:-2] + '…'
            nlw = dc.GetTextExtent(net_label).Width
            dc.DrawText(net_label, cx - nlw // 2, cy + TERM_R + 15)

    def _draw_ghost(self, dc: wx.DC) -> None:
        """Draw a semi-transparent component preview at the drag position."""
        ghost = self._ghost
        if ghost is None or ghost.anchor is None:
            return
        lay = self.layout
        comp_def = ghost.comp_def
        try:
            pin_holes = comp_def.place(ghost.anchor)
        except (AssertionError, IndexError, KeyError):
            return

        dc.SetBrush(wx.Brush(wx.Colour(comp_def.color + '88')))
        dc.SetPen(wx.Pen('#88888888', 1, wx.PENSTYLE_DOT))
        holes_xy = [lay.hole_xy(h) for h in pin_holes.values()]
        holes_xy = [xy for xy in holes_xy if xy is not None]
        if holes_xy:
            xs = [xy[0] for xy in holes_xy]
            ys = [xy[1] for xy in holes_xy]
            body_rect = wx.Rect(min(xs) - 4, min(ys) - 6,
                                max(xs) - min(xs) + 8, max(ys) - min(ys) + 12)
            dc.DrawRoundedRectangle(body_rect, 4)

    def _draw_wire_start_indicator(self, dc: wx.DC) -> None:
        xy = self.layout.hole_xy(self._wire_start)
        if xy:
            dc.SetBrush(wx.Brush(wx.Colour(255, 200, 0, 180)))
            dc.SetPen(wx.Pen('#ffcc00', 2))
            dc.DrawCircle(xy[0], xy[1], HOLE_R + 5)
            # Line to current mouse
            mx, my = self._ghost_pos
            dc.SetPen(wx.Pen('#ffcc00', 2, wx.PENSTYLE_DOT))
            dc.DrawLine(xy[0], xy[1], mx, my)

    def _draw_validation_icons(self, dc: wx.DC) -> None:
        """Draw ⚡ / ? icons at the centroid of each validation issue's holes."""
        if not self._validation_icons:
            return

        ICON_R = 11   # background circle radius
        for cx, cy, kind in self._validation_icons:
            if kind == IssueKind.SHORT:
                bg_color  = '#cc2222'
                symbol    = '⚡'
            elif kind == IssueKind.OPEN_NET:
                bg_color  = '#cc8800'
                symbol    = '?'
            else:
                continue   # UNPLACED has no hole location

            # White halo so the icon is readable over any background
            dc.SetBrush(wx.Brush('#ffffff'))
            dc.SetPen(wx.Pen('#ffffff', 3))
            dc.DrawCircle(cx, cy, ICON_R + 2)

            # Filled badge
            dc.SetBrush(wx.Brush(bg_color))
            dc.SetPen(wx.Pen('#ffffff', 1))
            dc.DrawCircle(cx, cy, ICON_R)

            # Symbol
            dc.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                               wx.FONTWEIGHT_BOLD))
            dc.SetTextForeground('#ffffff')
            tw, th = dc.GetTextExtent(symbol)
            dc.DrawText(symbol, cx - tw // 2, cy - th // 2)

    def _draw_column_labels(self, dc: wx.DC) -> None:
        lay = self.layout
        dc.SetFont(wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground('#808080')
        label_y = lay._row_y['j'] + PITCH + 2
        for col in range(1, COLUMNS + 1, 5):
            x = lay.col_x(col)
            label = str(col)
            dc.DrawText(label, x - dc.GetTextExtent(label).Width // 2, label_y)


# ---------------------------------------------------------------------------
# Geometry helper
# ---------------------------------------------------------------------------

def _point_to_segment_dist(px, py, x1, y1, x2, y2) -> float:
    """Perpendicular distance from point (px,py) to line segment (x1,y1)-(x2,y2)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
