# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A KiCad 9 Action Plugin that lets students build their schematic on a virtual 830-point breadboard.  The student draws a schematic in Eeschema, exports a netlist, then places components from a tray onto the breadboard, draws jumper wires, and validates the result against the netlist.

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
├── tray.py              wx.ScrolledWindow: component cards
└── model/
    ├── breadboard.py    Core data model: TieHole/RailHole/Terminal, UnionFind,
    │                    Breadboard, PlacedComponent (incl. flipped), Wire
    ├── components.py    ComponentDef for every supported part; PinOffset resolution;
    │                    guess_type_id() heuristic from KiCad symbol/ref/value
    ├── netlist.py       KiCad S-expression netlist parser (.net files from Eeschema)
    └── validator.py     validate() → ValidationResult with OPEN_NET / SHORT / UNPLACED
```

### Data flow

1. `netlist.py` parses the `.net` file → `Netlist` (components + nets).
2. `components.py` maps each component to a `ComponentDef` via `guess_type_id()`.
3. The student clicks a card in `ComponentTray` → `canvas.begin_place()` → ghost preview → click hole to place.
   - `ComponentDef.place(anchor, flipped)` → `{pin_num: TieHole}`.
   - `Breadboard.place(PlacedComponent)` stores the result.
4. The student draws wires; `Breadboard.add_wire(h1, h2)` stores them.
5. `validate()` calls `Breadboard.build_connectivity()` (union-find over tie strips + rails + wires) and checks every schematic net for connectivity and shorts.

### Breadboard model

- **Tie strips**: 63 columns × rows a–e (top bank) and f–j (bottom bank).  All holes in the same (column, bank) are pre-connected.
- **Power rails**: `top_plus`, `top_minus`, `bot_plus`, `bot_minus` — 50 holes each, split at hole 25.  `RAIL_GROUP_GAP=22px` between groups of 5; `RAIL_BREAK_PX=58px` at the mid-board split.
- **Terminals**: `GND`, `V1`, `V2` — binding posts on the left.  Right-click to assign to a schematic net.  The validator treats an assigned terminal as a hole on that net.
- Connectivity is a union-find rebuilt from scratch on each validation call.

### Supported components

| type_id      | Part                     | Package    | Pins |
|---|---|---|---|
| R            | Resistor (colour bands)  | axial      | 2    |
| C / C_POL    | Capacitor / electrolytic | radial     | 2    |
| L            | Inductor                 | axial      | 2    |
| D            | Diode (1N4001 style)     | axial      | 2 (A-K) |
| D_Zener      | Zener diode              | axial      | 2 (A-K) |
| LED          | LED 5 mm                 | round      | 2 (A-K) |
| POT          | Potentiometer            | 3-pin      | 3    |
| NPN / PNP    | BJT transistor           | TO-92      | 3 (C-B-E) |
| JFET_N/P     | JFET                     | TO-92      | 3 (S-G-D) |
| BS170        | MOSFET                   | TO-92      | 3 (S-G-D) |
| TL081        | Single op-amp            | 8-DIP      | 8    |
| RC4558       | Dual op-amp              | 8-DIP      | 8    |
| TL084        | Quad op-amp              | 14-DIP     | 14   |

Virtual/simulation components (no `guess_type_id` match) are hidden from the tray and only accessible via binding post assignment.

### DIP IC convention

DIP ICs straddle the center gap.  **Pins 1–N/2 go into row `f` (lower bank); pins N/2+1–N go into row `e` (upper bank).**  Pin 1 is therefore at the lower-left of the IC body — matching the physical lab convention.

`PlacedComponent.flipped = True` rotates the IC 180°: `PinOffset.resolve()` negates `col_delta` AND inverts `cross_gap`, moving top-side pins to bottom and vice versa.  The anchor shifts by `footprint_cols - 1` so the body stays in the same visual position.

DIP drawing includes grey leg tabs at each pin and a white pin-1 dot on the body side where pin 1 lives.

### Interaction modes

| Mode   | Hotkey | Left-click behaviour |
|---|---|---|
| Select | Esc    | Click to select; drag to reposition |
| Wire   | W      | First click = start hole; second click = end hole |
| Delete | D      | Click component or wire to remove |

Additional keys: `R` rotates a DIP 180° (during placement or when selected); `Del` deletes the selected item.  Right-click on a placed DIP also rotates it.  Hotkeys bound via `EVT_CHAR_HOOK` on the frame; `_set_mode()` keeps the toolbar radio buttons in sync.

### Canvas layout

- Board is centred with `dc.SetDeviceOrigin(ox, oy)`; mouse coords converted via `_board_pos()`.
- `_parse_ohms()` + `_resistor_bands()` in `canvas.py` decode the KiCad value string (e.g. `4k7`, `10k`, `470R`) into 4-band colour tuples drawn on the resistor pill body.

### Validation

- **UNPLACED**: placeable component has no placement.
- **OPEN_NET**: net holes span more than one connected component.
- **SHORT**: two different nets share a connected component root.

Icons (⚡ SHORT, ? OPEN_NET) are placed at the first relevant placed-component pin, offset upward.
