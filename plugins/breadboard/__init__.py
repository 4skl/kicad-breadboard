"""
KiCad Action Plugin: Breadboard Builder.

Installation
------------
Symlink or copy the `plugins/breadboard/` directory into KiCad's scripting
plugins folder:

  ~/.local/share/kicad/9.0/scripting/plugins/breadboard/

Then in KiCad: Tools > External Plugins > Refresh Plugins, then
Tools > External Plugins > Breadboard Builder.
"""
import os

try:
    import pcbnew

    class BreadboardPlugin(pcbnew.ActionPlugin):
        def defaults(self):
            self.name = 'Breadboard Builder'
            self.category = 'Educational'
            self.description = (
                'Place schematic components on a virtual breadboard and '
                'check correctness against the netlist.'
            )
            self.show_toolbar_button = True
            icon = os.path.join(os.path.dirname(__file__), 'resources', 'icon.png')
            if os.path.isfile(icon):
                self.icon_file_name = icon

        def Run(self):
            import wx
            from .window import BreadboardWindow

            board = pcbnew.GetBoard()
            project_path = os.path.dirname(board.GetFileName()) if board else None

            app = wx.GetApp()
            if app is None:
                app = wx.App()

            win = BreadboardWindow(parent=None, project_path=project_path)
            win.Show()

    BreadboardPlugin().register()

except ImportError:
    # Running outside KiCad (e.g. standalone / testing)
    pass
