"""
Component tray — shows unplaced components from the netlist.

Each component is shown as a small card with its reference, value, and a
colour swatch.  The student drags a card from the tray onto the canvas to
place it.  Once placed, the card is greyed out but stays visible so students
can see the full component list.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import wx

from .model import (
    ComponentDef, ALL_DEFS, Netlist, NetlistComponent,
    guess_type_id, Breadboard,
)

CARD_W = 110
CARD_H = 36
CARD_PAD = 4
SWATCH_W = 12


class ComponentTray(wx.ScrolledWindow):

    def __init__(self, parent, board: Breadboard, netlist: Optional[Netlist] = None):
        super().__init__(parent, style=wx.VSCROLL | wx.BORDER_SUNKEN)
        self.board = board
        self.netlist = netlist
        self._cards: List[_TrayCard] = []
        # Set by the window after both canvas and tray are created:
        #   tray.on_pick = lambda comp_def, ref: canvas.begin_place(comp_def, ref)
        self.on_pick = None

        self.SetScrollRate(0, CARD_H + CARD_PAD)
        self.SetBackgroundColour('#d8d8d8')

        if netlist:
            self._build_cards(netlist)

    def load_netlist(self, netlist: Netlist) -> None:
        self.netlist = netlist
        self._build_cards(netlist)

    def refresh_placed(self) -> None:
        """Re-check which components are placed and redraw all cards."""
        for card in self._cards:
            card.Refresh()
        self.Refresh()

    # ------------------------------------------------------------------

    def _build_cards(self, netlist: Netlist) -> None:
        for child in self.GetChildren():
            child.Destroy()
        self._cards.clear()

        for ref, comp in sorted(netlist.components.items()):
            type_id = guess_type_id(ref, comp.value, comp.symbol, comp.lib)
            if type_id is None:
                continue   # virtual/simulation component — only usable via binding posts
            comp_def = ALL_DEFS.get(type_id)
            card = _TrayCard(self, ref=ref, comp=comp, comp_def=comp_def,
                             board=self.board)
            self._cards.append(card)

        self._layout_cards()

    def _layout_cards(self) -> None:
        y = CARD_PAD
        for card in self._cards:
            card.SetPosition((CARD_PAD, y))
            card.SetSize((CARD_W, CARD_H))
            y += CARD_H + CARD_PAD
        self.SetVirtualSize(CARD_W + CARD_PAD * 2, y)


class _TrayCard(wx.Panel):

    def __init__(self, parent, ref: str, comp: NetlistComponent,
                 comp_def: Optional[ComponentDef], board: Breadboard):
        super().__init__(parent)
        self.ref = ref
        self.comp = comp
        self.comp_def = comp_def
        self.board = board

        self.SetSize((CARD_W, CARD_H))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_drag_start)

    @property
    def placed(self) -> bool:
        # Always read through the tray's board so that a board swap (clear)
        # is immediately reflected without needing to rebuild the cards.
        tray = self.GetParent()
        board = tray.board if hasattr(tray, 'board') else self.board
        return board.get_placement(self.ref) is not None

    @placed.setter
    def placed(self, v: bool) -> None:
        self.Refresh()

    def _on_paint(self, _evt) -> None:
        dc = wx.AutoBufferedPaintDC(self)
        placed = self.placed
        bg = '#b8b8b8' if placed else '#f8f8f8'
        dc.SetBackground(wx.Brush(bg))
        dc.Clear()

        # Swatch
        color = self.comp_def.color if self.comp_def else '#aaaaaa'
        dc.SetBrush(wx.Brush(color if not placed else '#888888'))
        dc.SetPen(wx.Pen('#666666', 1))
        dc.DrawRectangle(4, 4, SWATCH_W, CARD_H - 8)

        # Text
        text_x = SWATCH_W + 8
        fg = '#888888' if placed else '#222222'
        dc.SetTextForeground(fg)

        dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_BOLD))
        type_suffix = f' - {self.comp_def.type_id}' if self.comp_def else ''
        dc.DrawText(f'{self.ref}{type_suffix}', text_x, 4)

        dc.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_NORMAL))
        value_text = self.comp.value[:14]  # truncate long values
        dc.DrawText(value_text, text_x, 18)

        # Border
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        dc.SetPen(wx.Pen('#aaaaaa' if placed else '#888888', 1))
        dc.DrawRectangle(0, 0, CARD_W, CARD_H)

    def _on_drag_start(self, evt: wx.MouseEvent) -> None:
        if self.placed or self.comp_def is None:
            return
        tray = self.GetParent()
        if hasattr(tray, 'on_pick') and tray.on_pick is not None:
            tray.on_pick(self.comp_def, self.ref)
