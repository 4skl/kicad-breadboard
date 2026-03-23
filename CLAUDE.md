# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A KiCad 9 Action Plugin that lets students build their schematic on a virtual 830-point breadboard.  The student draws a schematic in Eeschema, exports a netlist, then drags components from a tray onto the breadboard, draws jumper wires, and validates the result against the netlist.

Target audience: introductory analog electronics course at the University of Antwerp.

## Running / developing

**Standalone (without KiCad), for UI development:**
```bash
cd /home/robin/kicad/kicad-breadboard
python -m plugins.breadboard.standalone path/to/circuit.net
```
Requires `wxPython`: `pip install wxPython`.

**Inside KiCad 9:**
Symlink the plugin directory into KiCad's scripting plugins folder, then refresh:
```bash
ln -s /home/robin/kicad/kicad-breadboard/plugins/breadboard \
      ~/.local/share/kicad/9.0/scripting/plugins/breadboard
```
Then in KiCad: Tools → External Plugins → Refresh Plugins → Breadboard Builder.

**Netlist export from Eeschema:**
File → Export → Netlist → KiCad format → save as `<project>.net`.

## Architecture

```
plugins/breadboard/
├── __init__.py          KiCad ActionPlugin registration (pcbnew.ActionPlugin)
├── standalone.py        wx.App entry point for development outside KiCad
├── window.py            Main wx.Frame: toolbar, splitter, status bar
├── canvas.py            wx.Panel: renders breadboard, handles mouse interaction
├── tray.py              wx.ScrolledWindow: component cards, drag source
└── model/
    ├── breadboard.py    Core data model: TieHole/RailHole/Terminal, UnionFind,
    │                    Breadboard, PlacedComponent, Wire
    ├── components.py    ComponentDef for every supported part; PinOffset resolution;
    │                    guess_type_id() heuristic from KiCad symbol/ref/value
    ├── netlist.py       KiCad S-expression netlist parser (.net files from Eeschema)
    └── validator.py     validate() → ValidationResult with OPEN_NET / SHORT / UNPLACED
```

### Data flow

1. `netlist.py` parses the `.net` file → `Netlist` (components + nets).
2. `components.py` maps each component to a `ComponentDef` via `guess_type_id()`.
3. The student drags cards from `ComponentTray` onto `BreadboardCanvas`.
   - Drop calls `ComponentDef.place(anchor)` → `{pin_num: TieHole}`.
   - `Breadboard.place(PlacedComponent)` stores the result.
4. The student draws wires; `Breadboard.add_wire(h1, h2)` stores them.
5. `validate()` calls `Breadboard.build_connectivity()` (union-find over tie strips +
   rails + wires) and checks every schematic net for connectivity and shorts.

### Breadboard model

- **Tie strips**: 63 columns × rows a–e (top bank) and f–j (bottom bank).
  All holes in the same (column, bank) are pre-connected.
- **Power rails**: `top_plus`, `top_minus`, `bot_plus`, `bot_minus` — 50 holes each.
- **Terminals**: `GND`, `V1`, `V2` — lab power supply connection points on the left side.
  Students connect them to rails with wires.
- Connectivity is a union-find rebuilt from scratch on each validation call.

### Supported components

| type_id      | Part                    | Package   | Pins |
|---|---|---|---|
| R            | Resistor                | axial     | 2    |
| C / C_POL    | Capacitor / electrolytic| radial    | 2    |
| L            | Inductor                | axial     | 2    |
| POT          | Potentiometer           | 3-pin     | 3    |
| NPN / PNP    | BJT transistor          | TO-92     | 3 (C-B-E) |
| JFET_N/P     | JFET                    | TO-92     | 3 (S-G-D) |
| BS170        | MOSFET                  | TO-92     | 3 (S-G-D) |
| TL081        | Single op-amp           | 8-DIP     | 8    |
| RC4558       | Dual op-amp             | 8-DIP     | 8    |
| TL084        | Quad op-amp             | 14-DIP    | 14   |

DIP ICs always straddle the center gap: top-side pins in row `e`, bottom-side in row `f`.

### Interaction modes

| Mode   | Hotkey | Left-click behaviour                                    |
|---|---|---|
| Select | Esc    | Click to select a placed component; drag to reposition  |
| Wire   | W      | First click = wire start hole; second click = end hole  |
| Delete | D      | Click on a placed component or wire to remove it        |

Hotkeys are bound on the frame via `EVT_CHAR_HOOK`; `_set_mode()` in `window.py` keeps the toolbar radio buttons in sync.

Selected wire or component can be deleted with the keyboard Delete key.

### Validation logic (`model/validator.py`)

- **UNPLACED**: component in netlist has no placement on the board (only for components where `guess_type_id` returns non-None; virtual/simulation components are skipped).
- **OPEN_NET**: pins in the same schematic net are in different connected components.
- **SHORT**: pins from different schematic nets share the same connected component.

Validation icons (⚡ for SHORT, ? for OPEN_NET) are drawn at the pin of the first relevant placed component, offset upward so they don't obscure the hole dot.

### Canvas layout

The breadboard is centred in the window using `dc.SetDeviceOrigin(ox, oy)`.  Mouse coordinates are converted via `_board_pos()` before any hit-testing.

Power rails use `RAIL_GROUP_GAP = 27 px` between groups of 5 holes so that 50 rail holes span the same visual width as the 63-column tie strip.  The mid-board break uses `RAIL_BREAK_PX = 22 px` and is not double-counted as a group gap.

### Virtual components and binding posts

Components for which `guess_type_id()` returns `None` (e.g., simulation voltage sources) are excluded from the component tray.  They can still be connected to the circuit by right-clicking a binding post (`GND`, `V1`, `V2`) and assigning it to a schematic net.  The validator treats a terminal as a hole on its assigned net.
