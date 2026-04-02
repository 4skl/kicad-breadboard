"""
Component tray — shows unplaced components from the netlist.

Each component is shown as a small card with its reference, value, and a
colour swatch.  The student clicks a card to begin placing it on the canvas.
Once placed, the card is greyed out but stays visible.

Cards are native wx.Panel widgets with SetBackgroundColour + StaticText so
that all rendering is handled by the platform — no custom paint handlers.
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


class _TrayCard(wx.Panel):

    def __init__(self, parent, ref: str, comp: NetlistComponent,
                 comp_def: Optional[ComponentDef], board: Breadboard):
        super().__init__(parent, size=(CARD_W, CARD_H))
        self.ref      = ref
        self.comp     = comp
        self.comp_def = comp_def
        self.board    = board
        self._swatch_color = comp_def.color if comp_def else '#aaaaaa'

        # Swatch: a child panel whose background IS its colour
        self._swatch = wx.Panel(self, pos=(4, 4), size=(SWATCH_W, CARD_H - 8))

        type_suffix = f' - {comp_def.type_id}' if comp_def else ''
        self._ref_lbl = wx.StaticText(self, label=f'{ref}{type_suffix}',
                                      pos=(SWATCH_W + 8, 3))
        self._ref_lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))

        self._val_lbl = wx.StaticText(self, label=comp.value[:14],
                                      pos=(SWATCH_W + 8, 18))
        self._val_lbl.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        self._apply_colors()

        for widget in (self, self._swatch, self._ref_lbl, self._val_lbl):
            widget.Bind(wx.EVT_LEFT_DOWN, self._on_click)

    def _apply_colors(self) -> None:
        placed = self.board.get_placement(self.ref) is not None
        bg  = '#b8b8b8' if placed else '#f8f8f8'
        fg  = '#888888' if placed else '#222222'
        sw  = '#888888' if placed else self._swatch_color

        self.SetBackgroundColour(bg)
        self._ref_lbl.SetBackgroundColour(bg)
        self._ref_lbl.SetForegroundColour(fg)
        self._val_lbl.SetBackgroundColour(bg)
        self._val_lbl.SetForegroundColour(fg)
        self._swatch.SetBackgroundColour(sw)

        self.Refresh()
        self._swatch.Refresh()
        self._ref_lbl.Refresh()
        self._val_lbl.Refresh()

    def update(self, board: Breadboard) -> None:
        self.board = board
        self._apply_colors()

    def _on_click(self, _evt) -> None:
        if self.board.get_placement(self.ref) is not None or self.comp_def is None:
            return
        tray = self.GetParent()
        if hasattr(tray, 'on_pick') and tray.on_pick is not None:
            tray.on_pick(self.comp_def, self.ref)


class ComponentTray(wx.ScrolledWindow):

    def __init__(self, parent, board: Breadboard, netlist: Optional[Netlist] = None):
        super().__init__(parent, style=wx.VSCROLL | wx.BORDER_SUNKEN)
        self.board   = board
        self.netlist = netlist
        self._cards: List[_TrayCard] = []
        self.on_pick = None

        self.SetScrollRate(0, CARD_H + CARD_PAD)
        self.SetBackgroundColour('#d8d8d8')

        if netlist:
            self._build_cards(netlist)

    def load_netlist(self, netlist: Netlist) -> None:
        self.netlist = netlist
        self._build_cards(netlist)

    def refresh_placed(self) -> None:
        for card in self._cards:
            card.update(self.board)

    def _build_cards(self, netlist: Netlist) -> None:
        for child in self.GetChildren():
            child.Destroy()
        self._cards.clear()

        y = CARD_PAD
        for ref, comp in sorted(netlist.components.items()):
            type_id  = guess_type_id(ref, comp.value, comp.symbol, comp.lib)
            if type_id is None:
                continue
            comp_def = ALL_DEFS.get(type_id)
            card = _TrayCard(self, ref=ref, comp=comp, comp_def=comp_def,
                             board=self.board)
            card.SetPosition((CARD_PAD, y))
            self._cards.append(card)
            y += CARD_H + CARD_PAD

        self.SetVirtualSize(CARD_W + CARD_PAD * 2, y if self._cards else CARD_PAD)
        self.Scroll(0, 0)
