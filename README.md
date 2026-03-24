# KiCad Breadboard Builder

A KiCad 9 Action Plugin for introductory analog electronics courses at the University of Antwerp. Students draw a schematic in Eeschema, then use this plugin to wire up the same circuit on a virtual 830-point breadboard — placing components, drawing jumper wires, and validating their work against the schematic.

## What it does

- Renders an 830-point breadboard (63 columns × rows a–j, four power rails, three binding posts: GND / V1 / V2)
- Parses a KiCad S-expression netlist and shows all placeable components in a side tray
- Two-step placement for 2-pin components: click pin 1, then click pin 2
- Single-click placement for DIP ICs and 3-pin components (BJT, POT)
- Draw jumper wires between any two holes (tie strip, rail, or binding post)
- Validate the board against the schematic: highlights open nets (?) and shorts (⚡)
- Export the board as a PNG image
- "Update from schematic" re-exports the netlist via `kicad-cli` without leaving the window

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

---

## Installation in KiCad 9

### Step 1 — Clone the repository

```bash
git clone https://cosysgit.uantwerpen.be/rkerstens/kicad-breadboard.git
```

### Step 2 — Link the plugin into KiCad's scripting folder

KiCad looks for plugins in `~/.local/share/kicad/9.0/scripting/plugins/` on Linux/macOS, or `%APPDATA%\kicad\9.0\scripting\plugins\` on Windows.

**Linux / macOS:**
```bash
ln -s /path/to/kicad-breadboard/plugins/breadboard \
      ~/.local/share/kicad/9.0/scripting/plugins/breadboard
```

**Windows** (run PowerShell as Administrator):
```powershell
New-Item -ItemType Junction `
  -Path  "$env:APPDATA\kicad\9.0\scripting\plugins\breadboard" `
  -Target "C:\path\to\kicad-breadboard\plugins\breadboard"
```

Or simply **copy** the `plugins/breadboard/` folder into the scripting plugins directory if you do not want a symlink.

### Step 3 — Refresh plugins in KiCad

1. Open KiCad and open any project in the **PCB Editor** (pcbnew).
2. In the menu: **Tools → External Plugins → Refresh Plugins**.
3. A breadboard icon appears in the right-hand toolbar (or under **Tools → External Plugins → Breadboard Builder**).

> The plugin registers as an Action Plugin and only appears inside the PCB Editor, not the schematic editor — this is a KiCad limitation for Python plugins.

### Step 4 — Open your project

Click the toolbar button (or menu entry). The plugin will automatically find the netlist (`.net`) in the same folder as the open PCB file.

If you have not exported a netlist yet, use **"Update from schematic"** in the toolbar — this calls `kicad-cli` to export one automatically. `kicad-cli` ships with KiCad 9 and is on the PATH when KiCad is installed normally.

---

## Standalone mode (development / no KiCad needed)

```bash
pip install wxPython
cd /path/to/kicad-breadboard
python -m plugins.breadboard.standalone path/to/circuit.net
```

---

## Toolbar buttons

| Button | Action |
|---|---|
| Open netlist | Load a `.net` file manually |
| Update from schematic | Re-export netlist from `.kicad_sch` via `kicad-cli` and reload |
| Export image | Save the current board view as a PNG |
| Validate | Check if the breadboard matches the schematic |
| Clear warnings | Dismiss `?` / `⚡` validation markers |
| Clear board | Remove all placed components and wires |

## Hotkeys

| Key | Action |
|---|---|
| W | Wire mode |
| D | Delete mode |
| R | Rotate DIP IC or TO-92 / POT 180° (during placement or when selected) |
| Esc | Back to Select / Move mode |
| Del | Delete selected component or wire |
| Right-click on DIP | Rotate 180° |
| Right-click on binding post | Assign to schematic net |

Example workflow:
- Draw a schematic.

![schematic](images/schematic.png)

- Go to the PCB editor (using the green button on the toolbar, or by using Tools -> Switch to PCB editor)
![icon](images/icon.png)

At the top, a new breadboard icon appeared (in the toolbar, next to the CLI input icon). Clicking this will take you to the Breadboard editor.

![breadboard](images/breadboard.png)


Here, you can select which component you want to place and click "Validate" to check if your build contains errors. If it does, it will indicate missing connections and short circuits on the relevant pins as illustrated below.

![shortcircuit](images/shortcircuit.png)

Thats it! Have fun!

Robin