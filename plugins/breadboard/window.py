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
    parse_netlist, find_netlist, find_schematic,
    validate, IssueKind,
    ALL_DEFS, guess_type_id,
)

# Toolbar button IDs
ID_SELECT = wx.NewIdRef()
ID_WIRE   = wx.NewIdRef()
ID_DELETE = wx.NewIdRef()
ID_UPDATE   = wx.NewIdRef()
ID_EXPORT   = wx.NewIdRef()
ID_VALIDATE = wx.NewIdRef()
ID_CLEAR_WARNINGS = wx.NewIdRef()
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
        self._project_path: Optional[str] = project_path

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

        # --- Binding-post assignment section ---
        term_label = wx.StaticText(tray_panel, label='Binding posts')
        term_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                   wx.FONTWEIGHT_BOLD))
        tray_sizer.Add(term_label, 0, wx.LEFT | wx.TOP | wx.RIGHT, 6)

        _TERM_COLORS = {'GND': '#3a3a3a', 'V1': '#bb2020', 'V2': '#1a7a30'}
        self._term_choices: dict = {}
        term_grid = wx.FlexGridSizer(rows=3, cols=2, vgap=4, hgap=6)
        term_grid.AddGrowableCol(1)
        for name in ('GND', 'V1', 'V2'):
            lbl = wx.StaticText(tray_panel, label=name)
            lbl.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                wx.FONTWEIGHT_BOLD))
            lbl.SetForegroundColour(_TERM_COLORS[name])
            ch = wx.Choice(tray_panel, choices=['(unassigned)'])
            ch.SetSelection(0)
            self._term_choices[name] = ch
            term_grid.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
            term_grid.Add(ch, 1, wx.EXPAND)
        tray_sizer.Add(term_grid, 0, wx.EXPAND | wx.ALL, 6)
        tray_sizer.Add(wx.StaticLine(tray_panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)

        # --- Component tray ---
        label = wx.StaticText(tray_panel, label='Components')
        label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_BOLD))
        self.tray = ComponentTray(tray_panel, self.board, self.netlist)
        tray_sizer.Add(label, 0, wx.ALL, 6)
        tray_sizer.Add(self.tray, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)

        hotkey_label = wx.StaticText(tray_panel, label=(
            'Hotkeys\n'
            'W  Wire\n'
            'D  Delete\n'
            'R  Rotate DIP / TO-92 180°\n'
            'Esc  Select / cancel\n'
            'Del  Delete selected\n'
            'R-click  Rotate component\n'
            '\n'
            'View\n'
            'Scroll  Zoom in / out\n'
            'Middle drag  Pan\n'
            'Ctrl+Home  Fit view\n'
            '+  /  \u2212  Zoom in / out\n'
        ))
        hotkey_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                                     wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        hotkey_label.SetForegroundColour('#000000')
        tray_sizer.Add(hotkey_label, 0, wx.ALL, 6)

        credit_label = wx.StaticText(tray_panel,
                                     label='Made with \u2665 at Cosys-lab\ncosys.uantwerpen.be')
        credit_label.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                     wx.FONTSTYLE_ITALIC, wx.FONTWEIGHT_NORMAL))
        credit_label.SetForegroundColour('#444444')
        tray_sizer.Add(credit_label, 0, wx.ALL | wx.ALIGN_CENTRE_HORIZONTAL, 8)

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
        tb.AddTool(ID_UPDATE, 'Update from schematic', wx.NullBitmap,
                   shortHelp='Re-export netlist from .kicad_sch and reload (requires kicad-cli)')
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
        tb.AddTool(ID_EXPORT,   'Export image', wx.NullBitmap,
                   shortHelp='Save the breadboard as a PNG image')
        tb.AddSeparator()
        tb.AddTool(ID_VALIDATE, 'Validate',   wx.NullBitmap,
                   shortHelp='Check if your circuit matches the schematic')
        tb.AddTool(ID_CLEAR_WARNINGS, 'Clear warnings', wx.NullBitmap,
                   shortHelp='Dismiss validation warning/short markers')
        tb.AddTool(ID_CLEAR,  'Clear board',  wx.NullBitmap,
                   shortHelp='Remove all placed components and wires')
        tb.Realize()
        self.toolbar = tb

    def _bind_events(self) -> None:
        self.Bind(wx.EVT_TOOL, self._on_open,     id=ID_OPEN)
        self.Bind(wx.EVT_TOOL, self._on_update,   id=ID_UPDATE)
        self.Bind(wx.EVT_TOOL, self._on_export,   id=ID_EXPORT)
        self.Bind(wx.EVT_TOOL, self._on_select,   id=ID_SELECT)
        self.Bind(wx.EVT_TOOL, self._on_wire,     id=ID_WIRE)
        self.Bind(wx.EVT_TOOL, self._on_delete,   id=ID_DELETE)
        self.Bind(wx.EVT_TOOL, self._on_validate,       id=ID_VALIDATE)
        self.Bind(wx.EVT_TOOL, self._on_clear_warnings, id=ID_CLEAR_WARNINGS)
        self.Bind(wx.EVT_TOOL, self._on_clear,          id=ID_CLEAR)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        for name, ch in self._term_choices.items():
            ch.Bind(wx.EVT_CHOICE, lambda evt, n=name: self._on_term_choice(n, evt))

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
                path = dlg.GetPath()
                self._project_path = str(Path(path).parent)
                self._load_netlist(path)

    def _on_update(self, _evt) -> None:
        """Re-export the netlist from the .kicad_sch via kicad-cli and reload."""
        import subprocess

        if not self._project_path:
            wx.MessageBox(
                'No project loaded yet.\n'
                'Use "Open netlist" first, or launch the plugin from KiCad.',
                'Update from schematic', wx.OK | wx.ICON_INFORMATION)
            return

        sch = find_schematic(self._project_path)
        if not sch:
            wx.MessageBox(
                f'No .kicad_sch file found in:\n{self._project_path}',
                'Update from schematic', wx.OK | wx.ICON_ERROR)
            return

        net_path = sch.with_suffix('.net')
        self.SetStatusText('Exporting netlist from schematic…', 0)
        self.Update()   # flush the status bar before the subprocess blocks

        try:
            result = subprocess.run(
                ['kicad-cli', 'sch', 'export', 'netlist',
                 '--format', 'kicadfmt', '-o', str(net_path), str(sch)],
                capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            wx.MessageBox(
                'kicad-cli not found on PATH.\n'
                'Make sure KiCad 9 is installed and kicad-cli is accessible.',
                'Update from schematic', wx.OK | wx.ICON_ERROR)
            return
        except subprocess.TimeoutExpired:
            wx.MessageBox('kicad-cli timed out.', 'Update from schematic',
                          wx.OK | wx.ICON_ERROR)
            return

        if result.returncode != 0:
            wx.MessageBox(
                f'kicad-cli failed (exit {result.returncode}):\n{result.stderr}',
                'Update from schematic', wx.OK | wx.ICON_ERROR)
            return

        # Reload the freshly-written netlist, keeping existing placements
        self._load_netlist(str(net_path))

        # Remove placements for refs that no longer exist in the new netlist
        if self.netlist:
            removed = []
            for ref in list(self.board.placements):
                if ref not in self.netlist.components:
                    self.board.remove(ref)
                    removed.append(ref)
            if removed:
                self.tray.refresh_placed()
                self.canvas.Refresh()
                self.SetStatusText(
                    f'Netlist updated. Removed orphaned placement(s): '
                    f'{", ".join(removed)}.', 0)

    def _on_export(self, _evt) -> None:
        default = 'breadboard.png'
        if self._project_path:
            from pathlib import Path as _Path
            default = str(_Path(self._project_path) / 'breadboard.png')
        with wx.FileDialog(
            self,
            message='Save breadboard image',
            defaultFile=default,
            wildcard='PNG image (*.png)|*.png',
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()

        bmp = self.canvas.render_to_bitmap()
        if not bmp.SaveFile(path, wx.BITMAP_TYPE_PNG):
            wx.MessageBox(f'Failed to save image to:\n{path}',
                          'Export image', wx.OK | wx.ICON_ERROR)
            return
        self.SetStatusText(f'Image saved to {path}', 0)

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

    def _on_clear_warnings(self, _evt) -> None:
        self.canvas.clear_highlights()
        self.SetStatusText('Validation markers cleared.', 0)

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
        self._project_path = project_path
        net_path = find_netlist(project_path)
        if net_path:
            self._load_netlist(str(net_path))
        else:
            self.SetStatusText(
                f'No netlist found for "{project_path}". '
                'Use "Update from schematic" or export one manually.', 0
            )

    def _on_term_choice(self, term_name: str, evt) -> None:
        ch = self._term_choices[term_name]
        sel = ch.GetSelection()
        # item 0 is "(unassigned)"; items 1..n are net names
        net = ch.GetString(sel) if sel > 0 else ''
        self.board.assign_terminal(term_name, net)
        self.canvas.Refresh()

    def _refresh_terminal_choices(self) -> None:
        """Repopulate the binding-post dropdowns from the loaded netlist."""
        if self.netlist is None:
            return
        net_names = sorted(net.name for net in self.netlist.nets if net.name)
        choices = ['(unassigned)'] + net_names
        for name, ch in self._term_choices.items():
            ch.SetItems(choices)
            current = self.board.get_terminal_net(name)
            if current in net_names:
                ch.SetSelection(net_names.index(current) + 1)
            else:
                ch.SetSelection(0)

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

        self._refresh_terminal_choices()

        n = len(self.netlist.components)
        self.SetStatusText(
            f'Loaded {n} component(s) from {Path(path).name}.  '
            'Click a component in the tray to place it.  '
            'Right-click a binding post to assign it to a net.', 0
        )
