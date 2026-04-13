"""
Component tray — shows unplaced components from the netlist.

Each component is shown as a small card with its reference, value, and a
colour swatch.  The student clicks a card to begin placing it on the canvas.
Once placed, the card is greyed out but stays visible.

Cards are drawn entirely in the ScrolledWindow's EVT_PAINT handler using
wx.AutoBufferedPaintDC.  No native child wx.Panel widgets are used, which
avoids a GTK/Linux issue where native sub-windows overflow their parent's
clip region and bleed into adjacent panels.

Scroll offset is computed manually (GetScrollPixelsPerUnit + GetViewStart)
rather than via PrepareDC, which is the reliable cross-platform pattern.
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

import wx

from .model import (
    ComponentDef, ALL_DEFS, Netlist, NetlistComponent,
    guess_type_id, Breadboard, TO92_PINOUT_VARIANTS,
)

CARD_W      = 110
CARD_H      = 36
TO92_CARD_H = 48   # taller to accommodate the pinout row
CARD_PAD    = 4
SWATCH_W    = 12

# Cycle-button dimensions (TO-92 cards only)
_BTN_W = 16
_BTN_H = 12
_BTN_RIGHT_PAD = 4   # gap between button right edge and card right edge


class _Card:
    """Pure data — no wx widget."""
    __slots__ = ('ref', 'comp', 'comp_def', 'y', 'height', 'pinout_idx')

    def __init__(self, ref: str, comp: NetlistComponent,
                 comp_def: Optional[ComponentDef], y: int, height: int):
        self.ref        = ref
        self.comp       = comp
        self.comp_def   = comp_def
        self.y          = y       # top-left y in virtual (unscrolled) coordinates
        self.height     = height
        self.pinout_idx = 0       # index into TO92_PINOUT_VARIANTS[type_id]


class ComponentTray(wx.ScrolledWindow):

    def __init__(self, parent, board: Breadboard, netlist: Optional[Netlist] = None):
        super().__init__(parent, style=wx.VSCROLL | wx.BORDER_SUNKEN)
        self.board   = board
        self.netlist = netlist
        self._cards: List[_Card] = []
        self.on_pick = None

        self.SetScrollRate(0, CARD_H + CARD_PAD)
        self.SetBackgroundColour('#d8d8d8')
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        self.Bind(wx.EVT_PAINT,     self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)

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
            h = TO92_CARD_H if type_id in TO92_PINOUT_VARIANTS else CARD_H
            self._cards.append(_Card(ref=ref, comp=comp, comp_def=comp_def, y=y, height=h))
            y += h + CARD_PAD

        total_h = y if self._cards else CARD_PAD
        self.SetVirtualSize(CARD_W + CARD_PAD * 2, total_h)
        self.Scroll(0, 0)
        self.Refresh()

    def _card_at(self, virt_y: int) -> Optional[_Card]:
        """Return the card whose bounding box contains virtual y-coordinate virt_y."""
        for card in self._cards:
            if card.y <= virt_y < card.y + card.height:
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

        font_pinout = wx.Font(6, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        font_btn    = wx.Font(7, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)

        for card in self._cards:
            placed = self.board.get_placement(card.ref) is not None
            x = CARD_PAD
            y = card.y - scroll_y   # virtual → screen coordinates
            if y + card.height < 0 or y > client_h:
                continue            # outside visible area
            bg = '#b8b8b8' if placed else '#f8f8f8'

            # Background
            dc.SetBrush(wx.Brush(bg))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawRectangle(x, y, CARD_W, card.height)

            # Colour swatch
            color = card.comp_def.color if card.comp_def else '#aaaaaa'
            dc.SetBrush(wx.Brush(color if not placed else '#888888'))
            dc.SetPen(wx.Pen('#666666', 1))
            dc.DrawRectangle(x + 4, y + 4, SWATCH_W, card.height - 8)

            # Text
            fg = '#888888' if placed else '#222222'
            dc.SetTextForeground(fg)

            type_suffix = f' - {card.comp_def.type_id}' if card.comp_def else ''
            dc.SetFont(font_bold)
            dc.DrawText(f'{card.ref}{type_suffix}', x + SWATCH_W + 8, y + 4)

            dc.SetFont(font_normal)
            dc.DrawText(card.comp.value[:14], x + SWATCH_W + 8, y + 18)

            # Pinout row (TO-92 only)
            if card.height > CARD_H and card.comp_def:
                variants = TO92_PINOUT_VARIANTS.get(card.comp_def.type_id, [])
                if variants:
                    pinout_label = variants[card.pinout_idx][0]
                    dc.SetFont(font_pinout)
                    dc.SetTextForeground(fg)
                    dc.DrawText(pinout_label, x + SWATCH_W + 8, y + 32)

                    # Cycle button (only when not placed and multiple variants exist)
                    if len(variants) > 1 and not placed:
                        btn_x = x + CARD_W - _BTN_W - _BTN_RIGHT_PAD
                        btn_y = y + 30
                        dc.SetBrush(wx.Brush('#d8d8d8'))
                        dc.SetPen(wx.Pen('#888888', 1))
                        dc.DrawRoundedRectangle(btn_x, btn_y, _BTN_W, _BTN_H, 2)
                        dc.SetFont(font_btn)
                        dc.SetTextForeground('#333333')
                        tw, th = dc.GetTextExtent('>')
                        dc.DrawText('>', btn_x + (_BTN_W - tw) // 2,
                                    btn_y + (_BTN_H - th) // 2)

            # Border
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.SetPen(wx.Pen('#aaaaaa' if placed else '#888888', 1))
            dc.DrawRectangle(x, y, CARD_W, card.height)

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def _on_left_down(self, evt: wx.MouseEvent) -> None:
        click_x = evt.GetX()
        _, virt_y = self.CalcUnscrolledPosition(evt.GetX(), evt.GetY())
        card = self._card_at(virt_y)
        if card is None:
            return
        placed = self.board.get_placement(card.ref) is not None

        # Check if click landed on the cycle-pinout button (TO-92 only)
        if (not placed and card.comp_def and
                card.comp_def.type_id in TO92_PINOUT_VARIANTS):
            variants = TO92_PINOUT_VARIANTS[card.comp_def.type_id]
            if len(variants) > 1:
                btn_x = CARD_PAD + CARD_W - _BTN_W - _BTN_RIGHT_PAD
                if btn_x <= click_x < btn_x + _BTN_W:
                    card.pinout_idx = (card.pinout_idx + 1) % len(variants)
                    self.Refresh()
                    return

        if placed or card.comp_def is None:
            return
        if self.on_pick is not None:
            comp_def = card.comp_def
            if (card.comp_def.type_id in TO92_PINOUT_VARIANTS
                    and card.pinout_idx > 0):
                _, variant_offsets = TO92_PINOUT_VARIANTS[
                    card.comp_def.type_id][card.pinout_idx]
                comp_def = dataclasses.replace(card.comp_def,
                                               pin_offsets=variant_offsets)
            self.on_pick(comp_def, card.ref)
