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
import wx.lib.stattext

from .canvas import BreadboardCanvas, MODE_SELECT, MODE_WIRE, MODE_DELETE
from .tray import ComponentTray
from .model import (
    Breadboard, Netlist,
    parse_netlist, find_netlist, find_schematic,
    validate, IssueKind,
    ALL_DEFS, guess_type_id,
    save_session, load_session,
    PROBE_NAMES, PROBE_META,
)

# Toolbar button IDs
ID_SELECT = wx.NewIdRef()
ID_WIRE   = wx.NewIdRef()
ID_DELETE = wx.NewIdRef()
ID_UPDATE      = wx.NewIdRef()
ID_EXPORT      = wx.NewIdRef()
ID_VALIDATE    = wx.NewIdRef()
ID_CLEAR_WARNINGS = wx.NewIdRef()
ID_CLEAR       = wx.NewIdRef()
ID_OPEN        = wx.NewIdRef()
ID_NET_LABELS  = wx.NewIdRef()
ID_SAVE        = wx.NewIdRef()
ID_LOAD        = wx.NewIdRef()


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
        self._netlist_path: Optional[str] = None   # last successfully loaded .net file
        self._refreshing_choices: bool = False     # suppress EVT_CHOICE during SetItems

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
        self._build_menu()
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

        # --- Instruments section ---
        instr_label = wx.StaticText(tray_panel, label='Instruments')
        instr_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                    wx.FONTWEIGHT_BOLD))
        tray_sizer.Add(instr_label, 0, wx.LEFT | wx.TOP | wx.RIGHT, 6)

        self._probe_choices: dict = {}
        self._probe_place_btns: dict = {}

        _INSTRUMENT_GROUPS = [
            ('Function generator', ('FG+', 'FG_GND')),
            ('Oscilloscope',       ('CH1', 'CH2', 'SCOPE_GND')),
        ]
        for group_label, probe_list in _INSTRUMENT_GROUPS:
            sub = wx.StaticText(tray_panel, label=group_label)
            sub.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_ITALIC,
                                wx.FONTWEIGHT_NORMAL))
            sub.SetForegroundColour('#555555')
            tray_sizer.Add(sub, 0, wx.LEFT | wx.TOP, 8)

            grid = wx.FlexGridSizer(rows=len(probe_list), cols=3, vgap=3, hgap=4)
            grid.AddGrowableCol(2)
            for name in probe_list:
                meta = PROBE_META[name]
                lbl = wx.StaticText(tray_panel, label=meta['label'])
                lbl.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                    wx.FONTWEIGHT_BOLD))
                lbl.SetForegroundColour(meta['color'])
                btn = wx.Button(tray_panel, label='Place', size=(54, -1))
                ch = wx.Choice(tray_panel, choices=['(unassigned)'])
                ch.SetSelection(0)
                self._probe_place_btns[name] = btn
                self._probe_choices[name] = ch
                grid.Add(lbl, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(btn, 0, wx.ALIGN_CENTRE_VERTICAL)
                grid.Add(ch,  1, wx.EXPAND | wx.ALIGN_CENTRE_VERTICAL)
                btn.Bind(wx.EVT_BUTTON, lambda e, n=name: self._on_probe_place_btn(n))
                ch.Bind(wx.EVT_CHOICE,  lambda e, n=name: self._on_probe_choice(n, e))
            tray_sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        tray_sizer.Add(wx.StaticLine(tray_panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)

        # --- Component tray ---
        label = wx.StaticText(tray_panel, label='Components')
        label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_BOLD))
        self.tray = ComponentTray(tray_panel, self.board, self.netlist)
        tray_sizer.Add(label, 0, wx.ALL, 6)
        tray_sizer.Add(self.tray, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)

        hk_font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        hk_bold = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        info_font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_ITALIC, wx.FONTWEIGHT_NORMAL)

        # Left grid: Edit + File
        left_grid = wx.GridBagSizer(hgap=4, vgap=1)

        def hk_head(text, grid, row):
            lbl = wx.StaticText(tray_panel, label=text)
            lbl.SetFont(hk_bold)
            grid.Add(lbl, pos=(row, 0), span=(1, 2), flag=wx.TOP, border=4)
            return row + 1

        def hk_row(key, desc, grid, row):
            k = wx.StaticText(tray_panel, label=key)
            k.SetFont(hk_font)
            d = wx.StaticText(tray_panel, label=desc)
            d.SetFont(hk_font)
            d.SetForegroundColour('#444444')
            grid.Add(k, pos=(row, 0), flag=wx.ALIGN_RIGHT)
            grid.Add(d, pos=(row, 1))
            return row + 1

        r = 0
        r = hk_head('Edit', left_grid, r)
        r = hk_row('W', 'Wire', left_grid, r)
        r = hk_row('D', 'Delete', left_grid, r)
        r = hk_row('R', 'Rotate', left_grid, r)
        r = hk_row('Esc', 'Select', left_grid, r)
        r = hk_row('Del', 'Delete sel.', left_grid, r)
        r = hk_row('R-click', 'Rotate', left_grid, r)
        r = hk_head('File', left_grid, r)
        r = hk_row('Ctrl+O', 'Open', left_grid, r)
        r = hk_row('Ctrl+S', 'Save', left_grid, r)
        r = hk_row('Ctrl+L', 'Load', left_grid, r)

        # Right sizer: View grid + info text directly below it
        right_grid = wx.GridBagSizer(hgap=4, vgap=1)
        r = 0
        r = hk_head('View', right_grid, r)
        r = hk_row('Scroll', 'Zoom', right_grid, r)
        r = hk_row('Sh+Scroll', 'Pan V', right_grid, r)
        r = hk_row('Ctrl+Scroll', 'Pan H', right_grid, r)
        r = hk_row('Mid drag', 'Pan', right_grid, r)
        r = hk_row('Ctrl+Home', 'Fit', right_grid, r)
        r = hk_row('+/\u2212', 'Zoom', right_grid, r)

        info_lbl = wx.lib.stattext.GenStaticText(tray_panel,
                                 label='\nRelease: baking...\n\nMade with \u2665 by\nRobin Kerstens\nUniversity of Antwerp,\nBelgium.')
        info_lbl.SetFont(info_font)
        info_lbl.SetForegroundColour('#666666')

        right_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer.Add(right_grid, 0)
        right_sizer.Add(info_lbl, 0, wx.TOP, 6)

        hotkey_sizer = wx.BoxSizer(wx.HORIZONTAL)
        hotkey_sizer.Add(left_grid, 0, wx.RIGHT | wx.ALIGN_BOTTOM, 12)
        hotkey_sizer.Add(right_sizer, 0, wx.ALIGN_BOTTOM)

        tray_sizer.Add(hotkey_sizer, 0, wx.ALL, 6)

        tray_panel.SetSizer(tray_sizer)

        splitter.SplitVertically(self.canvas, tray_panel, sashPosition=-260)
        splitter.SetMinimumPaneSize(200)
        splitter.SetSashGravity(1.0)

        # Connect tray → canvas placement flow
        self.tray.on_pick = lambda comp_def, ref: self.canvas.begin_place(comp_def, ref)
        self.canvas.on_placed = lambda ref: self.tray.refresh_placed()
        self.canvas.on_probe_placed = lambda name: self._refresh_probe_buttons()

        self.SetStatusBar(wx.StatusBar(self))
        self.GetStatusBar().SetFieldsCount(2)
        self.GetStatusBar().SetStatusWidths([-3, -1])
        self.SetStatusText('Load a netlist, then click a component in the tray to place it.', 0)
        self.SetStatusText('Mode: Select / Move  [W] Wire  [D] Delete', 1)

    def _build_menu(self) -> None:
        menu_bar = wx.MenuBar()
        file_menu = wx.Menu()
        file_menu.Append(ID_OPEN,   'Open netlist…\tCtrl+O',
                         'Load a KiCad .net file')
        file_menu.Append(ID_UPDATE, 'Update from schematic',
                         'Re-export and reload netlist via kicad-cli')
        file_menu.AppendSeparator()
        file_menu.Append(ID_SAVE,   'Save session…\tCtrl+S',
                         'Save current placements and wires to a .kicad_bbrd file')
        file_menu.Append(ID_LOAD,   'Load session…\tCtrl+L',
                         'Restore placements and wires from a .kicad_bbrd file')
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, 'Quit\tAlt+F4')
        menu_bar.Append(file_menu, '&File')
        self.SetMenuBar(menu_bar)

    def _build_toolbar(self) -> None:
        tb = self.CreateToolBar(wx.TB_HORIZONTAL | wx.TB_TEXT | wx.TB_NOICONS)

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
        tb.AddTool(ID_NET_LABELS, 'Signal labels', wx.NullBitmap,
                   shortHelp='Show / hide net signal labels on the breadboard',
                   kind=wx.ITEM_CHECK)
        tb.ToggleTool(ID_NET_LABELS, True)
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
        self.Bind(wx.EVT_MENU, self._on_open,     id=ID_OPEN)
        self.Bind(wx.EVT_MENU, self._on_update,   id=ID_UPDATE)
        self.Bind(wx.EVT_MENU, self._on_save,     id=ID_SAVE)
        self.Bind(wx.EVT_MENU, self._on_load,     id=ID_LOAD)
        self.Bind(wx.EVT_MENU, lambda _: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_TOOL, self._on_open,     id=ID_OPEN)
        self.Bind(wx.EVT_TOOL, self._on_update,   id=ID_UPDATE)
        self.Bind(wx.EVT_TOOL, self._on_export,   id=ID_EXPORT)
        self.Bind(wx.EVT_TOOL, self._on_select,   id=ID_SELECT)
        self.Bind(wx.EVT_TOOL, self._on_wire,     id=ID_WIRE)
        self.Bind(wx.EVT_TOOL, self._on_delete,   id=ID_DELETE)
        self.Bind(wx.EVT_TOOL, self._on_net_labels,     id=ID_NET_LABELS)
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
                self._netlist_path = path
                self._load_netlist(path)

    def _export_netlist(self, silent: bool = False) -> Optional[Path]:
        """
        Run kicad-cli to export the netlist from the project schematic.
        Returns the .net path on success, or None on failure.
        If silent=True, errors are written to the status bar instead of a dialog.
        """
        import subprocess

        sch = find_schematic(self._project_path)
        if not sch:
            msg = f'No .kicad_sch file found in:\n{self._project_path}'
            if silent:
                self.SetStatusText(msg, 0)
            else:
                wx.MessageBox(msg, 'Update from schematic', wx.OK | wx.ICON_ERROR, self)
            return None

        net_path = sch.with_suffix('.net')
        self.SetStatusText('Exporting netlist from schematic…', 0)
        self.Update()

        try:
            result = subprocess.run(
                ['kicad-cli', 'sch', 'export', 'netlist',
                 '--format', 'kicadsexpr', '-o', str(net_path), str(sch)],
                capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            msg = ('kicad-cli not found on PATH.\n'
                   'Make sure KiCad is installed and kicad-cli is accessible.')
            if silent:
                self.SetStatusText(msg.replace('\n', ' '), 0)
            else:
                wx.MessageBox(msg, 'Update from schematic', wx.OK | wx.ICON_ERROR, self)
            return None
        except subprocess.TimeoutExpired:
            msg = 'kicad-cli timed out.'
            if silent:
                self.SetStatusText(msg, 0)
            else:
                wx.MessageBox(msg, 'Update from schematic', wx.OK | wx.ICON_ERROR, self)
            return None

        if result.returncode != 0:
            msg = f'kicad-cli failed (exit {result.returncode}):\n{result.stderr}'
            if silent:
                self.SetStatusText(msg.replace('\n', ' '), 0)
            else:
                wx.MessageBox(msg, 'Update from schematic', wx.OK | wx.ICON_ERROR, self)
            return None

        return net_path

    def _on_update(self, _evt) -> None:
        """Re-export the netlist from the .kicad_sch via kicad-cli and reload."""
        if not self._project_path:
            wx.MessageBox(
                'No project loaded yet.\n'
                'Use "Open netlist" first, or launch the plugin from KiCad.',
                'Update from schematic', wx.OK | wx.ICON_INFORMATION, self)
            return

        net_path = self._export_netlist(silent=False)
        if net_path is None:
            return

        # Reload the freshly-written netlist, keeping existing placements
        self._load_netlist(str(net_path))

        # Remove placements for refs that no longer exist, or whose type changed
        if self.netlist:
            removed = []
            type_changed = []
            for ref in list(self.board.placements):
                comp = self.netlist.components.get(ref)
                if comp is None:
                    self.board.remove(ref)
                    removed.append(ref)
                else:
                    new_type = guess_type_id(ref, comp.value, comp.symbol, comp.lib)
                    old_type = self.board.get_placement(ref).type_id
                    if new_type != old_type:
                        self.board.remove(ref)
                        type_changed.append(ref)
            msgs = []
            if removed:
                msgs.append(f'removed orphaned: {", ".join(removed)}')
            if type_changed:
                msgs.append(f'type changed — re-place: {", ".join(type_changed)}')
            if msgs:
                self.tray.refresh_placed()
                self.canvas.Refresh()
                self.SetStatusText(f'Netlist updated. {"; ".join(msgs).capitalize()}.', 0)

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
                          'Export image', wx.OK | wx.ICON_ERROR, self)
            return
        self.SetStatusText(f'Image saved to {path}', 0)

    def _on_save(self, _evt) -> None:
        default = 'breadboard.kicad_bbrd'
        if self._project_path:
            from pathlib import Path as _Path
            default = str(_Path(self._project_path) / 'breadboard.kicad_bbrd')
        with wx.FileDialog(
            self,
            message='Save session',
            defaultFile=default,
            wildcard='Breadboard session (*.kicad_bbrd)|*.kicad_bbrd|All files (*)|*',
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            save_session(self.board, self._netlist_path, path)
            self.SetStatusText(f'Session saved to {path}', 0)
        except Exception as exc:
            wx.MessageBox(f'Failed to save session:\n{exc}', 'Save session',
                          wx.OK | wx.ICON_ERROR, self)

    def _on_load(self, _evt) -> None:
        default_dir = self._project_path or ''
        with wx.FileDialog(
            self,
            message='Load session',
            defaultDir=default_dir,
            wildcard='Breadboard session (*.kicad_bbrd)|*.kicad_bbrd|All files (*)|*',
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            result = load_session(path)
        except Exception as exc:
            wx.MessageBox(f'Failed to load session:\n{exc}', 'Load session',
                          wx.OK | wx.ICON_ERROR, self)
            return

        # Restore board state
        self.board = result['board']
        self.canvas.board = self.board
        self.tray.board = self.board
        self.canvas.clear_highlights()

        # Reload the netlist from the saved path (if present and netlist not yet loaded)
        saved_netlist = result.get('netlist_path')
        if saved_netlist and self.netlist is None:
            try:
                self._load_netlist(saved_netlist)
            except Exception:
                pass  # carry on without netlist; user can open manually
        elif self.netlist:
            self.tray.refresh_placed()

        # Always resync terminal and probe dropdowns from the restored board state
        self._refresh_terminal_choices()
        self._refresh_probe_choices()
        self._refresh_probe_buttons()

        self.canvas.Refresh()
        self.SetStatusText(f'Session loaded from {path}', 0)

    def _on_net_labels(self, _evt) -> None:
        self.canvas.show_net_labels = self.toolbar.GetToolState(ID_NET_LABELS)
        self.canvas.Refresh()

    def _on_validate(self, _evt) -> None:
        if self.netlist is None:
            self.SetStatusText('No netlist loaded.', 0)
            return

        result = validate(self.board, self.netlist)

        if result.ok:
            self.canvas.clear_highlights()
            self.SetStatusText('Circuit OK — all nets match the schematic.', 0)
            wx.MessageBox('Circuit is correct!', 'Validation', wx.OK | wx.ICON_INFORMATION, self)
        else:
            self.canvas.set_validation_result(result)
            lines = [str(i) for i in result.issues]
            summary = f"{len(result.issues)} issue(s) found."
            self.SetStatusText(summary, 0)
            wx.MessageBox('\n'.join(lines), 'Validation issues',
                          wx.OK | wx.ICON_WARNING, self)

    def _on_clear_warnings(self, _evt) -> None:
        self.canvas.clear_highlights()
        self.SetStatusText('Validation markers cleared.', 0)

    def _on_clear(self, _evt) -> None:
        if wx.MessageBox(
            'Clear all placed components and wires?', 'Confirm',
            wx.YES_NO | wx.ICON_QUESTION, self
        ) == wx.YES:
            self.board = Breadboard()
            # Re-apply GND assignments
            if self.netlist and self.netlist.net_by_name('0'):
                self.board.assign_terminal('GND', '0')
                self.board.assign_probe_net('FG_GND', '0')
                self.board.assign_probe_net('SCOPE_GND', '0')
            self.canvas.board = self.board
            self.tray.board = self.board
            self.tray.refresh_placed()
            self._refresh_terminal_choices()
            self._refresh_probe_choices()
            self._refresh_probe_buttons()
            self.canvas.clear_highlights()
            self.canvas.Refresh()
            self.SetStatusText('Board cleared.', 0)

    # ------------------------------------------------------------------
    # Netlist loading
    # ------------------------------------------------------------------

    def _auto_load_netlist(self, project_path: str) -> None:
        self._project_path = project_path
        net_path = find_netlist(project_path)
        if not net_path:
            net_path = self._export_netlist(silent=True)
        if net_path:
            self._load_netlist(str(net_path))

    def _on_term_choice(self, term_name: str, evt) -> None:
        if self._refreshing_choices:
            return
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
        self._refreshing_choices = True
        try:
            for name, ch in self._term_choices.items():
                ch.SetItems(choices)
                current = self.board.get_terminal_net(name)
                if current in net_names:
                    ch.SetSelection(net_names.index(current) + 1)
                else:
                    ch.SetSelection(0)
        finally:
            self._refreshing_choices = False

    # ------------------------------------------------------------------
    # Instrument probe handlers
    # ------------------------------------------------------------------

    def _on_probe_choice(self, probe_name: str, _evt) -> None:
        if self._refreshing_choices:
            return
        ch = self._probe_choices[probe_name]
        sel = ch.GetSelection()
        net = ch.GetString(sel) if sel > 0 else ''
        self.board.assign_probe_net(probe_name, net)
        self.canvas.Refresh()

    def _on_probe_place_btn(self, probe_name: str) -> None:
        if self.board.get_probe_hole(probe_name) is not None:
            # Already placed — remove it
            self.board.remove_probe(probe_name)
            self._refresh_probe_buttons()
            self.canvas.Refresh()
        else:
            # Start placement mode
            self.canvas.begin_probe_place(probe_name)
            self.SetStatusText(
                f'Click a hole to place {PROBE_META[probe_name]["label"]} probe. '
                'Esc to cancel.', 0)

    def _refresh_probe_buttons(self) -> None:
        for name, btn in self._probe_place_btns.items():
            placed = self.board.get_probe_hole(name) is not None
            btn.SetLabel('Remove' if placed else 'Place')

    def _refresh_probe_choices(self) -> None:
        """Repopulate probe net dropdowns from the loaded netlist."""
        if self.netlist is None:
            return
        net_names = sorted(net.name for net in self.netlist.nets if net.name)
        choices = ['(unassigned)'] + net_names
        self._refreshing_choices = True
        try:
            for name, ch in self._probe_choices.items():
                ch.SetItems(choices)
                current = self.board.get_probe_net(name)
                if current in net_names:
                    ch.SetSelection(net_names.index(current) + 1)
                else:
                    ch.SetSelection(0)
        finally:
            self._refreshing_choices = False

    def _load_netlist(self, path: str) -> None:
        try:
            self.netlist = parse_netlist(path)
        except Exception as exc:
            wx.MessageBox(f'Failed to load netlist:\n{exc}',
                          'Error', wx.OK | wx.ICON_ERROR, self)
            return
        self._netlist_path = path

        self.canvas.netlist = self.netlist
        self.tray.load_netlist(self.netlist)

        # Auto-assign GND terminal and instrument grounds to the simulation ground net ("0")
        if self.netlist.net_by_name('0'):
            self.board.assign_terminal('GND', '0')
            self.board.assign_probe_net('FG_GND', '0')
            self.board.assign_probe_net('SCOPE_GND', '0')

        self._refresh_terminal_choices()
        self._refresh_probe_choices()

        n = len(self.netlist.components)
        if n == 0:
            self.SetStatusText(
                f'No components found in {Path(path).name} — '
                'save your schematic in Eeschema first, then use "Update from schematic".', 0
            )
        else:
            self.SetStatusText(
                f'Loaded {n} component(s) from {Path(path).name}.  '
                'Click a component in the tray to place it.  '
                'Right-click a binding post to assign it to a net.', 0
            )
