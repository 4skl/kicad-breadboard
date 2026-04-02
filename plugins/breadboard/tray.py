"""
Component tray — shows unplaced components from the netlist.

Each component is shown as a small card with its reference, value, and a
colour swatch.  The student clicks a card to begin placing it on the canvas.
Once placed, the card is greyed out but stays visible.

All cards are drawn directly in the ScrolledWindow's own paint handler
(no child wx.Panel widgets) for reliable cross-platform rendering.
"""
from __future__ import annotations

from typing import List, Optional

import wx

from .model import (
    ComponentDef, ALL_DEFS, Netlist, NetlistComponent,
    guess_type_id, Breadboard,
)

CARD_W   = 110
CARD_H   = 36
CARD_PAD = 4
SWATCH_W = 12


class _Card:
    """Pure data — no wx widget."""
    __slots__ = ('ref', 'comp', 'comp_def', 'y')

    def __init__(self, ref: str, comp: NetlistComponent,
                 comp_def: Optional[ComponentDef], y: int):
        self.ref      = ref
        self.comp     = comp
        self.comp_def = comp_def
        self.y        = y   # top-left y in virtual coordinates


class ComponentTray(wx.ScrolledWindow):

    def __init__(self, parent, board: Breadboard, netlist: Optional[Netlist] = None):
        super().__init__(parent, style=wx.VSCROLL | wx.BORDER_SUNKEN)
        self.board   = board
        self.netlist = netlist
        self._cards: List[_Card] = []
        # Set by the window after both canvas and tray are created:
        #   tray.on_pick = lambda comp_def, ref: canvas.begin_place(comp_def, ref)
        self.on_pick = None

        self.SetScrollRate(0, CARD_H + CARD_PAD)
        self.SetBackgroundColour('#d8d8d8')
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        self.Bind(wx.EVT_PAINT,       self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN,   self._on_left_down)

        if netlist:
            self._build_cards(netlist)

    def load_netlist(self, netlist: Netlist) -> None:
        self.netlist = netlist
        self._build_cards(netlist)

    def refresh_placed(self) -> None:
        """Re-check which components are placed and redraw."""
        self.Refresh()

    # ------------------------------------------------------------------

    def _build_cards(self, netlist: Netlist) -> None:
        self._cards.clear()
        y = CARD_PAD
        for ref, comp in sorted(netlist.components.items()):
            type_id  = guess_type_id(ref, comp.value, comp.symbol, comp.lib)
            if type_id is None:
                continue
            comp_def = ALL_DEFS.get(type_id)
            self._cards.append(_Card(ref=ref, comp=comp, comp_def=comp_def, y=y))
            y += CARD_H + CARD_PAD

        total_h = y if self._cards else CARD_PAD
        self.SetVirtualSize(CARD_W + CARD_PAD * 2, total_h)
        self.Scroll(0, 0)
        self.Refresh()

    def _card_at(self, virt_y: int) -> Optional[_Card]:
        """Return the card whose bounding box contains virtual y-coordinate virt_y."""
        for card in self._cards:
            if card.y <= virt_y < card.y + CARD_H:
                return card
        return None

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def _on_paint(self, _evt) -> None:
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()
        dc.SetBackgroundMode(wx.TRANSPARENT)

        # Compute scroll offset in pixels (no PrepareDC — same approach as canvas)
        _, y_unit  = self.GetScrollPixelsPerUnit()
        _, y_start = self.GetViewStart()
        scroll_y   = y_start * y_unit
        client_h   = self.GetClientSize().height

        font_bold   = wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        font_normal = wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)

        for card in self._cards:
            placed = self.board.get_placement(card.ref) is not None
            x = CARD_PAD
            y = card.y - scroll_y   # virtual → screen coordinates
            if y + CARD_H < 0 or y > client_h:
                continue            # outside visible area
            bg     = '#b8b8b8' if placed else '#f8f8f8'

            # Background
            dc.SetBrush(wx.Brush(bg))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawRectangle(x, y, CARD_W, CARD_H)

            # Swatch
            color = card.comp_def.color if card.comp_def else '#aaaaaa'
            dc.SetBrush(wx.Brush(color if not placed else '#888888'))
            dc.SetPen(wx.Pen('#666666', 1))
            dc.DrawRectangle(x + 4, y + 4, SWATCH_W, CARD_H - 8)

            # Text
            fg = '#888888' if placed else '#222222'
            dc.SetTextForeground(fg)

            type_suffix = f' - {card.comp_def.type_id}' if card.comp_def else ''
            dc.SetFont(font_bold)
            dc.DrawText(f'{card.ref}{type_suffix}', x + SWATCH_W + 8, y + 4)

            dc.SetFont(font_normal)
            dc.DrawText(card.comp.value[:14], x + SWATCH_W + 8, y + 18)

            # Border
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.SetPen(wx.Pen('#aaaaaa' if placed else '#888888', 1))
            dc.DrawRectangle(x, y, CARD_W, CARD_H)

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def _on_left_down(self, evt: wx.MouseEvent) -> None:
        # Convert click position to virtual (unscrolled) coordinates
        xu, yu = self.CalcUnscrolledPosition(evt.GetX(), evt.GetY())
        card = self._card_at(yu)
        if card is None:
            return
        placed = self.board.get_placement(card.ref) is not None
        if placed or card.comp_def is None:
            return
        if self.on_pick is not None:
            self.on_pick(card.comp_def, card.ref)
