"""
Main breadboard window (wx.Frame).

Layout:
  ┌──────────────────────────────────────────────────────────┐
  │  Toolbar: [Select] [Wire] [Delete] | [Validate] [Clear]  │
  ├──────────────────────────────┬───────────────────────────┤
  │                              │  Component tray           │
  │    BreadboardCanvas          │  (scrollable list of      │
  │                              │   netlist components)     │
  ├──────────────────────────────┴───────────────────────────┤
  │  Status bar                                              │
  └──────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import wx

from .canvas import BreadboardCanvas, MODE_SELECT, MODE_WIRE, MODE_DELETE
from .tray import ComponentTray
from .model import (
    Breadboard, Netlist,
    parse_netlist, find_netlist,
    validate, IssueKind,
    ALL_DEFS, guess_type_id,
)

# Toolbar button IDs
ID_SELECT = wx.NewIdRef()
ID_WIRE   = wx.NewIdRef()
ID_DELETE = wx.NewIdRef()
ID_VALIDATE = wx.NewIdRef()
ID_CLEAR  = wx.NewIdRef()
ID_OPEN   = wx.NewIdRef()


class BreadboardWindow(wx.Frame):

    def __init__(self, parent=None, project_path: Optional[str] = None):
        super().__init__(
            parent,
            title='Breadboard Builder',
            size=(1300, 600),
            style=wx.DEFAULT_FRAME_STYLE,
        )

        self.board = Breadboard()
        self.netlist: Optional[Netlist] = None

        self._build_ui()
        self._bind_events()

        if project_path:
            self._auto_load_netlist(project_path)

        self.Centre()
        self.Show()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_toolbar()

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)

        self.canvas = BreadboardCanvas(splitter, self.board, self.netlist)

        tray_panel = wx.Panel(splitter)
        tray_sizer = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(tray_panel, label='Components')
        label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_BOLD))
        self.tray = ComponentTray(tray_panel, self.board, self.netlist)
        tray_sizer.Add(label, 0, wx.ALL, 6)
        tray_sizer.Add(self.tray, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)

        hotkey_label = wx.StaticText(tray_panel, label=(
            'W  Wire\n'
            'D  Delete\n'
            'F  Flip DIP\n'
            'Esc  Select\n'
            'Del  Delete selected\n'
            'R-click  Flip / Assign net'
        ))
        hotkey_label.SetFont(wx.Font(7, wx.FONTFAMILY_DEFAULT,
                                     wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        hotkey_label.SetForegroundColour('#777777')
        tray_sizer.Add(hotkey_label, 0, wx.ALL, 6)

        tray_panel.SetSizer(tray_sizer)

        splitter.SplitVertically(self.canvas, tray_panel, sashPosition=-140)
        splitter.SetMinimumPaneSize(200)

        # Connect tray → canvas placement flow
        self.tray.on_pick = lambda comp_def, ref: self.canvas.begin_place(comp_def, ref)
        self.canvas.on_placed = lambda ref: self.tray.refresh_placed()

        self.SetStatusBar(wx.StatusBar(self))
        self.GetStatusBar().SetFieldsCount(2)
        self.GetStatusBar().SetStatusWidths([-3, -1])
        self.SetStatusText('Load a netlist, then click a component in the tray to place it.', 0)
        self.SetStatusText('Mode: Select / Move  [W] Wire  [D] Delete', 1)

    def _build_toolbar(self) -> None:
        tb = self.CreateToolBar(wx.TB_HORIZONTAL | wx.TB_TEXT | wx.TB_NOICONS)

        tb.AddTool(ID_OPEN,   'Open netlist', wx.NullBitmap,
                   shortHelp='Load KiCad netlist (.net)')
        tb.AddSeparator()
        tb.AddTool(ID_SELECT, 'Select / Move', wx.NullBitmap,
                   shortHelp='Select and move placed components',
                   kind=wx.ITEM_RADIO)
        tb.AddTool(ID_WIRE,   'Draw Wire',    wx.NullBitmap,
                   shortHelp='Draw a jumper wire between two holes',
                   kind=wx.ITEM_RADIO)
        tb.AddTool(ID_DELETE, 'Delete',       wx.NullBitmap,
                   shortHelp='Delete a component or wire',
                   kind=wx.ITEM_RADIO)
        tb.AddSeparator()
        tb.AddTool(ID_VALIDATE, 'Validate',   wx.NullBitmap,
                   shortHelp='Check if your circuit matches the schematic')
        tb.AddTool(ID_CLEAR,  'Clear board',  wx.NullBitmap,
                   shortHelp='Remove all placed components and wires')
        tb.Realize()
        self.toolbar = tb

    def _bind_events(self) -> None:
        self.Bind(wx.EVT_TOOL, self._on_open,     id=ID_OPEN)
        self.Bind(wx.EVT_TOOL, self._on_select,   id=ID_SELECT)
        self.Bind(wx.EVT_TOOL, self._on_wire,     id=ID_WIRE)
        self.Bind(wx.EVT_TOOL, self._on_delete,   id=ID_DELETE)
        self.Bind(wx.EVT_TOOL, self._on_validate, id=ID_VALIDATE)
        self.Bind(wx.EVT_TOOL, self._on_clear,    id=ID_CLEAR)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    # ------------------------------------------------------------------
    # Toolbar handlers
    # ------------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        """Switch canvas mode and keep toolbar radio state in sync."""
        self.canvas.set_mode(mode)
        if mode == MODE_SELECT:
            self.toolbar.ToggleTool(ID_SELECT, True)
            self.SetStatusText('Mode: Select / Move  [W] Wire  [D] Delete', 1)
        elif mode == MODE_WIRE:
            self.toolbar.ToggleTool(ID_WIRE, True)
            self.SetStatusText('Mode: Draw Wire — click start, click end  [Esc] cancel', 1)
        elif mode == MODE_DELETE:
            self.toolbar.ToggleTool(ID_DELETE, True)
            self.SetStatusText('Mode: Delete — click component or wire  [Esc] cancel', 1)

    def _on_select(self, _evt) -> None:
        self._set_mode(MODE_SELECT)

    def _on_wire(self, _evt) -> None:
        self._set_mode(MODE_WIRE)

    def _on_delete(self, _evt) -> None:
        self._set_mode(MODE_DELETE)

    def _on_char_hook(self, evt: wx.KeyEvent) -> None:
        key = evt.GetKeyCode()
        if key in (ord('W'), ord('w')):
            self._set_mode(MODE_WIRE)
        elif key in (ord('D'), ord('d')):
            self._set_mode(MODE_DELETE)
        elif key == wx.WXK_ESCAPE:
            self._set_mode(MODE_SELECT)
        else:
            evt.Skip()

    def _on_open(self, _evt) -> None:
        with wx.FileDialog(
            self,
            message='Open KiCad netlist',
            wildcard='KiCad netlist (*.net)|*.net|All files (*)|*',
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self._load_netlist(dlg.GetPath())

    def _on_validate(self, _evt) -> None:
        if self.netlist is None:
            self.SetStatusText('No netlist loaded.', 0)
            return

        result = validate(self.board, self.netlist)

        if result.ok:
            self.canvas.clear_highlights()
            self.SetStatusText('Circuit OK — all nets match the schematic.', 0)
            wx.MessageBox('Circuit is correct!', 'Validation', wx.OK | wx.ICON_INFORMATION)
        else:
            self.canvas.set_validation_result(result)
            lines = [str(i) for i in result.issues]
            summary = f"{len(result.issues)} issue(s) found."
            self.SetStatusText(summary, 0)
            wx.MessageBox('\n'.join(lines), 'Validation issues',
                          wx.OK | wx.ICON_WARNING)

    def _on_clear(self, _evt) -> None:
        if wx.MessageBox(
            'Clear all placed components and wires?', 'Confirm',
            wx.YES_NO | wx.ICON_QUESTION
        ) == wx.YES:
            self.board = Breadboard()
            # Re-apply GND assignment
            if self.netlist and self.netlist.net_by_name('0'):
                self.board.assign_terminal('GND', '0')
            self.canvas.board = self.board
            self.tray.board = self.board
            self.tray.refresh_placed()
            self.canvas.clear_highlights()
            self.canvas.Refresh()
            self.SetStatusText('Board cleared.', 0)

    # ------------------------------------------------------------------
    # Netlist loading
    # ------------------------------------------------------------------

    def _auto_load_netlist(self, project_path: str) -> None:
        net_path = find_netlist(project_path)
        if net_path:
            self._load_netlist(str(net_path))
        else:
            self.SetStatusText(
                f'No netlist found for "{project_path}". '
                'Export one from Eeschema: File → Export → Netlist.', 0
            )

    def _load_netlist(self, path: str) -> None:
        try:
            self.netlist = parse_netlist(path)
        except Exception as exc:
            wx.MessageBox(f'Failed to load netlist:\n{exc}',
                          'Error', wx.OK | wx.ICON_ERROR)
            return

        self.canvas.netlist = self.netlist
        self.tray.load_netlist(self.netlist)

        # Auto-assign GND terminal to the simulation ground net ("0")
        if self.netlist.net_by_name('0'):
            self.board.assign_terminal('GND', '0')

        n = len(self.netlist.components)
        self.SetStatusText(
            f'Loaded {n} component(s) from {Path(path).name}.  '
            'Click a component in the tray to place it.  '
            'Right-click a binding post to assign it to a net.', 0
        )
