# KiCad Breadboard Builder

A KiCad 9 Action Plugin for introductory analog electronics courses at the University of Antwerp. Students draw a schematic in Eeschema, export a netlist, then use this plugin to wire up the same circuit on a virtual 830-point breadboard — placing components, drawing jumper wires, and validating their work against the schematic.

## What it does

- Renders an 830-point breadboard (63 columns × rows a–j, four power rails, three binding posts: GND / V1 / V2)
- Parses a KiCad S-expression netlist and shows all placeable components in a side tray
- Click a component in the tray → ghost preview follows the mouse → click a hole to place it
- Draw jumper wires between any two holes (tie strip, rail, or binding post)
- Validate the board against the schematic: highlights open nets (?) and shorts (⚡)
- Simulation sources (V1, V2) are not placeable — assign them to binding posts via right-click

## Supported components

| Component | Package |
|---|---|
| Resistor (with colour bands) | Axial |
| Capacitor, electrolytic capacitor | Radial |
| Inductor | Axial |
| Diode, Zener diode | Axial (1N4001 style) |
| LED | 5 mm round |
| Potentiometer | 3-pin |
| NPN / PNP BJT | TO-92 |
| N / P-channel JFET | TO-92 |
| BS170 MOSFET | TO-92 |
| TL081 (single), RC4558 (dual), TL084 (quad) op-amp | DIP-8 / DIP-14 |

## Installation

**Inside KiCad 9** — symlink the plugin and refresh:

```bash
ln -s /path/to/kicad-breadboard/plugins/breadboard \
      ~/.local/share/kicad/9.0/scripting/plugins/breadboard
```

Then: Tools → External Plugins → Refresh Plugins → Breadboard Builder.

**Standalone** (for development, no KiCad needed):

```bash
pip install wxPython
cd /path/to/kicad-breadboard
python -m plugins.breadboard.standalone path/to/circuit.net
```

Netlist export from Eeschema: File → Export → Netlist → KiCad format → save as `<project>.net`.

## Hotkeys

| Key | Action |
|---|---|
| W | Wire mode |
| D | Delete mode |
| R | Rotate DIP IC 180° (during placement or when selected) |
| Esc | Back to Select / Move mode |
| Del | Delete selected component or wire |
| Right-click on DIP | Rotate 180° |
| Right-click on binding post | Assign to schematic net |
