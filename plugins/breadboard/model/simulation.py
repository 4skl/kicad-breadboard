"""
SPICE netlist generation and ngspice invocation for the breadboard plugin.

Public API:
    simulate(board, netlist, terminal_voltages) -> SimResult
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .breadboard import Breadboard
from .netlist import Netlist


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass
class SimResult:
    node_voltages:   Dict[str, float]   # spice_node_name → voltage in V
    net_voltages:    Dict[str, float]   # original net_name → voltage in V
    branch_currents: Dict[str, float]   # component ref → current in A
    error:           Optional[str]      # None if simulation succeeded
    spice_netlist:   str                # the generated netlist (for debugging)


# ---------------------------------------------------------------------------
# Net-name → SPICE node name sanitisation
# ---------------------------------------------------------------------------

def _sanitize_node(name: str) -> str:
    """Convert a schematic net name to a valid SPICE node identifier."""
    # Replace characters that SPICE doesn't like in node names
    node = re.sub(r'[/\-+\s(){}[\]<>:,;!@#$%^&*|\\?]', '_', name)
    # Prefix with n_ if the name starts with a digit or underscore
    if node and (node[0].isdigit() or node[0] == '_'):
        node = 'n_' + node
    # Collapse multiple underscores
    node = re.sub(r'_+', '_', node)
    # Strip trailing underscores
    node = node.rstrip('_')
    return node or 'n_unknown'


def _build_node_map(board: Breadboard) -> Dict[str, str]:
    """
    Build a mapping from schematic net name → SPICE node name.
    The GND net maps to '0'.
    """
    gnd_net = board.terminal_nets.get('GND', '')
    node_map: Dict[str, str] = {}

    if gnd_net:
        node_map[gnd_net] = '0'

    # Collect all other net names that are referenced by terminals
    for term, net_name in board.terminal_nets.items():
        if net_name and net_name not in node_map:
            node_map[net_name] = _sanitize_node(net_name)

    return node_map


def _node_for_net(net_name: str, node_map: Dict[str, str]) -> str:
    """Return (and cache) the SPICE node name for a schematic net name."""
    if net_name not in node_map:
        node_map[net_name] = _sanitize_node(net_name)
    return node_map[net_name]


# ---------------------------------------------------------------------------
# Value string → SPICE value
# ---------------------------------------------------------------------------

# Trailing unit suffixes to strip (case-insensitive, applied in order)
_UNIT_OHM_RE = re.compile(r'(?i)(ohms?|[ΩR])\s*$')
_UNIT_FH_RE  = re.compile(r'(?i)([FH])\s*$')

# What we recognise as a valid SPICE value  (number + optional SPICE suffix)
_VALID_VAL_RE = re.compile(
    r'^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?'
    r'([TtGgMmKkUuNnPpFf]|[Mm]eg|[Mm]egs?)?$'
)


def _spice_val(value: str) -> str:
    """
    Convert a KiCad component value string to a SPICE-compatible value.

    - Strips trailing unit letters (Ω, R, Ohm, F, H …)
    - Leaves SPICE suffixes intact (k, M, m, u, n, p, f, T, G …)
    - Returns "1" for non-numeric values (e.g. "BC547")
    """
    if not value:
        return '1'

    v = value.strip()

    # Strip trailing unit designators (ohm variants first, then F/H)
    v = _UNIT_OHM_RE.sub('', v).strip()
    v = _UNIT_FH_RE.sub('', v).strip()

    if not v:
        return '1'

    # If the value looks like a valid SPICE number+suffix, use it directly
    if _VALID_VAL_RE.match(v):
        return v

    # Try to handle things like "4k7" → "4.7k"
    m = re.match(r'^(\d+)([KkMmUuNnPpFf])(\d+)$', v)
    if m:
        return f'{m.group(1)}.{m.group(3)}{m.group(2)}'

    # Non-numeric (e.g. device model name "BC547") — dummy fallback
    return '1'


# ---------------------------------------------------------------------------
# SPICE element line generation
# ---------------------------------------------------------------------------

# SPICE models to append to every netlist
_MODELS = """\
.model Dgen D (IS=1e-12 N=1.0 RS=0.1 BV=100 CJO=0 TT=0)
.model Dled D (IS=1e-20 N=2.0 RS=5 BV=5)
.model Dzen D (IS=1e-12 N=1.0 RS=1 BV=5.1)
.model QNPN NPN (IS=1e-14 BF=200 NF=1 VAF=100 IKF=0.3)
.model QPNP PNP (IS=1e-14 BF=200 NF=1 VAF=100 IKF=0.3)
.model JFET_N NJF (VTO=-2 BETA=1e-3 LAMBDA=1e-4)
.model JFET_P PJF (VTO=2 BETA=1e-3 LAMBDA=1e-4)
.model NMOS NMOS (VTO=2 KP=2e-5 LAMBDA=0.01 GAMMA=0.37)
.model PMOS PMOS (VTO=-2 KP=2e-5 LAMBDA=0.01 GAMMA=0.37)
"""


def _element_line(ref: str, type_id: str,
                  pins: Dict[int, str],   # pin_num → spice_node
                  value: str) -> Optional[str]:
    """
    Return a SPICE element line for the given component, or None if unsupported.

    pins maps pin_number → SPICE node name.
    """
    tid = type_id

    def p(n: int) -> str:
        return pins.get(n, 'NC')

    if tid == 'R':
        return f'R{ref}  {p(1)}  {p(2)}  {_spice_val(value)}'

    if tid in ('C', 'C_POL'):
        # C_POL: pin1=+, pin2=-  (same numbering as C)
        return f'C{ref}  {p(1)}  {p(2)}  {_spice_val(value)}'

    if tid == 'L':
        return f'L{ref}  {p(1)}  {p(2)}  {_spice_val(value)}'

    if tid == 'D':
        # pin1=K (cathode), pin2=A (anode)
        return f'D{ref}  {p(2)}  {p(1)}  Dgen'

    if tid == 'D_Zener':
        return f'D{ref}  {p(2)}  {p(1)}  Dzen'

    if tid == 'LED':
        return f'D{ref}  {p(2)}  {p(1)}  Dled'

    if tid == 'NPN':
        # pin1=C, pin2=B, pin3=E
        return f'Q{ref}  {p(1)}  {p(2)}  {p(3)}  QNPN'

    if tid == 'PNP':
        return f'Q{ref}  {p(1)}  {p(2)}  {p(3)}  QPNP'

    if tid == 'JFET_N':
        # pin1=S, pin2=G, pin3=D  →  SPICE J: drain gate source model
        return f'J{ref}  {p(3)}  {p(2)}  {p(1)}  JFET_N'

    if tid == 'JFET_P':
        return f'J{ref}  {p(3)}  {p(2)}  {p(1)}  JFET_P'

    if tid in ('NMOS', 'BS170'):
        # pin1=G, pin2=S, pin3=D  →  SPICE M: drain gate source bulk model
        return f'M{ref}  {p(3)}  {p(1)}  {p(2)}  0  NMOS'

    if tid == 'PMOS':
        return f'M{ref}  {p(3)}  {p(1)}  {p(2)}  0  PMOS'

    return None


# ---------------------------------------------------------------------------
# SPICE netlist builder
# ---------------------------------------------------------------------------

def _build_netlist(board: Breadboard, netlist: Netlist,
                   terminal_voltages: Dict[str, float]) -> Tuple[str, Optional[str]]:
    """
    Build a SPICE netlist string from the board and netlist state.

    Returns (spice_text, error_or_None).
    """
    # ---- Validate GND assignment ----
    gnd_net = board.terminal_nets.get('GND', '')
    if not gnd_net:
        return '', 'GND terminal not assigned'

    # ---- Net name → SPICE node map (starts with GND→0 and terminal nets) ----
    node_map: Dict[str, str] = {gnd_net: '0'}
    for net_name in board.terminal_nets.values():
        if net_name and net_name not in node_map:
            node_map[net_name] = _sanitize_node(net_name)

    lines: list[str] = ['* Breadboard SPICE netlist', '']

    component_count = 0

    for ref, placed in board.placements.items():
        # Get the net for each pin of this component
        nets_dict = netlist.nets_for_ref(ref)   # {pin_num: Net}
        if not nets_dict:
            lines.append(f'* skipped: {ref} ({placed.type_id}) — no nets')
            continue

        # Map pin_num → SPICE node
        pin_nodes: Dict[int, str] = {}
        for pin_num, net in nets_dict.items():
            pin_nodes[pin_num] = _node_for_net(net.name, node_map)

        # Get component value from netlist
        nl_comp = netlist.components.get(ref)
        value = nl_comp.value if nl_comp else '1'

        element = _element_line(ref, placed.type_id, pin_nodes, value)
        if element is None:
            lines.append(f'* skipped: {ref} ({placed.type_id})')
        else:
            lines.append(element)
            component_count += 1

    if component_count == 0:
        return '', 'No simulatable components on board'

    lines.append('')

    # ---- Voltage sources for terminals V1, V2 ----
    for term in ('V1', 'V2'):
        net_name = board.terminal_nets.get(term, '')
        if not net_name:
            continue
        voltage = terminal_voltages.get(term)
        if voltage is None:
            continue
        spice_node = _node_for_net(net_name, node_map)
        lines.append(f'V_bb_{term}  {spice_node}  0  DC {voltage}')

    lines.append('')

    # ---- Models ----
    lines.append(_MODELS)

    # ---- Analysis ----
    lines.append('.op')
    lines.append('.end')
    lines.append('')

    return '\n'.join(lines), None


# ---------------------------------------------------------------------------
# ngspice invocation
# ---------------------------------------------------------------------------

def _find_ngspice() -> str:
    """Return the ngspice executable path, searching common install locations."""
    exe = shutil.which('ngspice')
    if exe:
        return exe
    candidates: List[str] = []
    if sys.platform == 'darwin':
        candidates = [
            '/opt/homebrew/bin/ngspice',
            '/usr/local/bin/ngspice',
        ]
    elif sys.platform == 'win32':
        candidates = [
            r'C:\Program Files\Spice64\bin\ngspice.exe',
            r'C:\Program Files (x86)\Spice64\bin\ngspice.exe',
            r'C:\ngspice\bin\ngspice.exe',
        ]
    else:  # Linux / other
        candidates = [
            '/usr/bin/ngspice',
            '/usr/local/bin/ngspice',
        ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return 'ngspice'   # fall through; subprocess will raise FileNotFoundError


def _run_ngspice(spice_text: str) -> Tuple[str, Optional[str]]:
    """
    Write spice_text to a temporary file, run ngspice -b, return (output, error_or_None).
    """
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.sp', delete=False,
                                         mode='w', encoding='utf-8') as f:
            tmp_path = f.name
            f.write(spice_text)

        result = subprocess.run(
            [_find_ngspice(), '-b', tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            # Extract a useful snippet from stderr
            err_lines = (result.stderr or result.stdout).strip().splitlines()
            snippet = '\n'.join(err_lines[:10]) if err_lines else 'ngspice failed'
            return combined, f'ngspice error (rc={result.returncode}):\n{snippet}'
        return combined, None

    except FileNotFoundError:
        return '', 'ngspice not found on PATH'

    except subprocess.TimeoutExpired:
        return '', 'ngspice timed out after 30 s'

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

# Line like:  "    mid                              3.333333e+00"
_VOLTAGE_LINE_RE = re.compile(
    r'^\s+(\S+)\s+([\d.eE+\-]+)\s*$'
)

# Line like:  "    v1#branch                        -1.66667e-03"
_CURRENT_LINE_RE = re.compile(
    r'^\s+(\S+)#branch\s+([\d.eE+\-]+)\s*$'
)


def _parse_output(output: str) -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    Parse ngspice .op output.

    Returns:
        node_voltages:   {spice_node → float}
        branch_currents: {device_name → float}
    """
    node_voltages:   Dict[str, float] = {}
    branch_currents: Dict[str, float] = {}

    in_voltage_section  = False
    in_current_section  = False

    for line in output.splitlines():
        stripped = line.strip()

        # Section headers
        if 'Node' in line and 'Voltage' in line:
            in_voltage_section  = True
            in_current_section  = False
            continue

        if 'Source' in line and 'Current' in line:
            in_current_section  = True
            in_voltage_section  = False
            continue

        # Separator / dashes lines — skip
        if re.match(r'^[-\s]+$', stripped) or stripped.startswith('----'):
            continue

        # Blank line ends the current section
        if not stripped:
            in_voltage_section = False
            in_current_section = False
            continue

        if in_voltage_section:
            m = _VOLTAGE_LINE_RE.match(line)
            if m:
                try:
                    node_voltages[m.group(1).lower()] = float(m.group(2))
                except ValueError:
                    pass

        elif in_current_section:
            m = _CURRENT_LINE_RE.match(line)
            if m:
                try:
                    branch_currents[m.group(1).lower()] = float(m.group(2))
                except ValueError:
                    pass

    return node_voltages, branch_currents


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def simulate(board: Breadboard, netlist: Netlist,
             terminal_voltages: Dict[str, float]) -> SimResult:
    """
    Run a DC .op simulation of the current board state.

    terminal_voltages: {terminal_name: voltage_V} e.g. {'V1': 5.0, 'V2': -5.0}
    GND terminal is always 0 V regardless of what is passed in terminal_voltages.

    Returns a SimResult; .error is None on success.
    """
    spice_text, build_err = _build_netlist(board, netlist, terminal_voltages)
    if build_err:
        return SimResult(
            node_voltages={},
            net_voltages={},
            branch_currents={},
            error=build_err,
            spice_netlist=spice_text,
        )

    output, run_err = _run_ngspice(spice_text)
    if run_err:
        return SimResult(
            node_voltages={},
            net_voltages={},
            branch_currents={},
            error=run_err,
            spice_netlist=spice_text,
        )

    node_voltages, branch_currents_raw = _parse_output(output)

    # ---- Build net_voltages by inverting the node_map ----
    # We need to rebuild node_map from the same inputs to invert it.
    gnd_net = board.terminal_nets.get('GND', '')
    node_map: Dict[str, str] = {gnd_net: '0'} if gnd_net else {}
    for net_name in board.terminal_nets.values():
        if net_name and net_name not in node_map:
            node_map[net_name] = _sanitize_node(net_name)
    # Walk all component nets too, so we can resolve every node name
    for ref in board.placements:
        for net in netlist.nets_for_ref(ref).values():
            if net.name not in node_map:
                node_map[net.name] = _sanitize_node(net.name)

    # Invert: spice_node → net_name  (keep first hit if there are duplicates)
    spice_to_net: Dict[str, str] = {}
    for net_name, spice_node in node_map.items():
        if spice_node not in spice_to_net:
            spice_to_net[spice_node] = net_name

    net_voltages: Dict[str, float] = {}
    for spice_node, voltage in node_voltages.items():
        net_name = spice_to_net.get(spice_node, spice_node)
        net_voltages[net_name] = voltage

    # ---- Normalise branch current keys to component refs ----
    # ngspice names them like "v_bb_v1#branch" or "r1#branch" → strip to ref
    branch_currents: Dict[str, float] = {}
    for raw_key, current in branch_currents_raw.items():
        # raw_key is already lowercase, e.g. "r1", "v_bb_v1"
        branch_currents[raw_key] = current

    return SimResult(
        node_voltages=node_voltages,
        net_voltages=net_voltages,
        branch_currents=branch_currents,
        error=None,
        spice_netlist=spice_text,
    )
