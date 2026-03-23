"""
Standalone launcher — run the breadboard window outside KiCad for development.

Usage:
    python -m plugins.breadboard.standalone [path/to/project.net]

Requires wxPython:
    pip install wxPython
"""
import sys
import wx
from .window import BreadboardWindow


def main():
    app = wx.App(False)
    path = sys.argv[1] if len(sys.argv) > 1 else None
    win = BreadboardWindow(project_path=path)
    app.MainLoop()


if __name__ == '__main__':
    main()
