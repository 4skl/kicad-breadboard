"""
SPICE netlist generation and ngspice invocation for the breadboard plugin.

Public API:
    simulate(board, netlist, terminal_voltages) -> SimResult
    simulate_transient(board, netlist, terminal_voltages, plot_nets) -> SimResult
    find_vsin_sources(netlist) -> List[VsinSource]
"""
from __future__ import annotations

import math
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
class TransientTrace:
    net_name: str
    times:    List[float]
    values:   List[float]


@dataclass
class VsinSource:
    ref:     str
    freq:    float   # Hz
    vampl:   float   # V peak amplitude
    voff:    float   # V DC offset
    net_pos: str     # net at + terminal
    net_neg: str     # net at - terminal


@dataclass
class SimResult:
    node_voltages:     Dict[str, float]              # spice_node_name → voltage in V
    net_voltages:      Dict[str, float]              # original net_name → voltage in V
    branch_currents:   Dict[str, float]              # component ref → current in A
    error:             Optional[str]                 # None if simulation succeeded
    spice_netlist:     str                           # the generated netlist (for debugging)
    spice_output:      str = ''                      # raw ngspice stdout
    warnings:          List[str] = field(default_factory=list)
    transient_traces:  Dict[str, TransientTrace] = field(default_factory=dict)


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


_SPICE_SFXS = [
    ('MEG', 1e6), ('meg', 1e6), ('T', 1e12), ('G', 1e9),
    ('K', 1e3), ('k', 1e3), ('U', 1e-6), ('u', 1e-6),
    ('N', 1e-9), ('n', 1e-9), ('P', 1e-12), ('p', 1e-12),
    ('F', 1e-15), ('f', 1e-15), ('M', 1e-3),  # M=milli in SPICE
]


def _parse_spice_float(s: str) -> float:
    """Convert a SPICE value string (e.g. '1k', '100Meg', '5u') to a Python float."""
    s = s.strip()
    if not s:
        return 0.0
    su = s.upper()
    for sfx, mult in _SPICE_SFXS:
        if su.endswith(sfx.upper()):
            return float(s[:-len(sfx)]) * mult
    return float(s)


def _parse_sim_params(raw: str) -> Dict[str, str]:
    """Parse a KiCad Sim.Params string like 'dc=0 ampl=1 f=1k ac=1' into a dict."""
    result: Dict[str, str] = {}
    for token in raw.split():
        if '=' in token:
            k, _, v = token.partition('=')
            result[k.strip().lower()] = v.strip()
    return result


def find_vsin_sources(netlist: 'Netlist') -> List[VsinSource]:
    """Return all VSIN sources found in the netlist with their AC parameters."""
    sources: List[VsinSource] = []
    for ref, comp in netlist.components.items():
        if comp.symbol.upper() != 'VSIN':
            continue
        nets = netlist.nets_for_ref(ref)
        net_pos = nets.get(1)
        net_neg = nets.get(2)
        if net_pos is None or net_neg is None:
            continue
        try:
            # KiCad stores parameters in Sim.Params as "dc=0 ampl=1 f=1k ac=1"
            sim_params = _parse_sim_params(comp.properties.get('Sim.Params', '') or '')
            freq  = _parse_spice_float(sim_params.get('f',    comp.properties.get('FREQ',  '0') or '0'))
            vampl = _parse_spice_float(sim_params.get('ampl', comp.properties.get('VAMPL', '0') or '0'))
            voff  = _parse_spice_float(sim_params.get('dc',   comp.properties.get('VOFF',  '0') or '0'))
        except (ValueError, OverflowError):
            continue
        sources.append(VsinSource(
            ref=ref, freq=freq, vampl=vampl, voff=voff,
            net_pos=net_pos.name, net_neg=net_neg.name,
        ))
    return sources


def _half_spice_val(sv: str) -> str:
    """Return a SPICE value string equal to half of sv (for POT wiper at 50%)."""
    sv = sv.strip()
    sv_up = sv.upper()
    for sfx, m in _SPICE_SFXS:
        if sv_up.endswith(sfx.upper()):
            try:
                n = float(sv[:-len(sfx)]) * m / 2
                if n == 0:
                    return '1'
                e = math.floor(math.log10(abs(n)))
                if e >= 9:   return f'{n/1e9:.4g}G'
                if e >= 6:   return f'{n/1e6:.4g}Meg'
                if e >= 3:   return f'{n/1e3:.4g}k'
                if e >= 0:   return f'{n:.4g}'
                if e >= -3:  return f'{n*1e3:.4g}m'
                if e >= -6:  return f'{n*1e6:.4g}u'
                if e >= -9:  return f'{n*1e9:.4g}n'
                return f'{n*1e12:.4g}p'
            except (ValueError, OverflowError):
                break
    try:
        return f'{float(sv) / 2:.4g}'
    except ValueError:
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

# Model names defined in _MODELS.
_BUILTIN_MODELS = frozenset({
    'Dgen', 'Dled', 'Dzen', 'QNPN', 'QPNP',
    'JFET_N', 'JFET_P', 'NMOS', 'PMOS',
})

# KiCad properties that carry an external SPICE model name.
_MODEL_PROPS = ('Spice_Model', 'Sim.Model', 'Sim.Internal_Model')


def _external_model_conflicts(board: 'Breadboard', netlist: 'Netlist') -> List[str]:
    """
    Return one error string per placed component that references a SPICE model
    we cannot use (i.e. not one of our built-in generics).
    """
    conflicts: List[str] = []
    for ref, placed in board.placements.items():
        nl_comp = netlist.components.get(ref)
        if not nl_comp:
            continue
        for prop in _MODEL_PROPS:
            model_name = nl_comp.properties.get(prop, '').strip()
            if model_name and model_name not in _BUILTIN_MODELS:
                conflicts.append(
                    f'  • {ref} ({placed.type_id}): references model "{model_name}"'
                )
                break
    return conflicts


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

    if tid == 'POT':
        # Model as two equal resistors at 50% wiper position
        # pin1=terminal1, pin2=wiper, pin3=terminal3
        half = _half_spice_val(_spice_val(value))
        return (f'R{ref}_A  {p(1)}  {p(2)}  {half}\n'
                f'R{ref}_B  {p(2)}  {p(3)}  {half}')

    if tid == 'OPAMP_SPICE':
        # Ideal op-amp: VCVS, gain=1e5.  pin1=IN+, pin2=IN-, pin3=V+, pin4=V-, pin5=OUT
        return f'E{ref}  {p(5)}  0  {p(1)}  {p(2)}  1e5'

    return None


# ---------------------------------------------------------------------------
# SPICE netlist builder
# ---------------------------------------------------------------------------

def _build_component_lines(
    board: Breadboard, netlist: Netlist,
) -> Tuple[List[str], int, Dict[str, str], List[str]]:
    """
    Build the component element lines shared by both DC and transient netlists.

    Returns (lines, component_count, node_map, warnings).
    node_map is pre-populated with GND and terminal nets.
    """
    warnings: List[str] = []
    gnd_net = board.terminal_nets.get('GND', '')

    node_map: Dict[str, str] = {}
    if gnd_net:
        node_map[gnd_net] = '0'
    for net_name in board.terminal_nets.values():
        if net_name and net_name not in node_map:
            node_map[net_name] = _sanitize_node(net_name)

    lines: List[str] = []
    component_count = 0

    for ref, placed in board.placements.items():
        nets_dict = netlist.nets_for_ref(ref)
        if not nets_dict:
            warnings.append(f'{ref} ({placed.type_id}): not in netlist — skipped')
            lines.append(f'* skipped: {ref} ({placed.type_id}) — no nets')
            continue

        floating = [pin for pin, net in nets_dict.items()
                    if net.name.startswith('unconnected-')]
        if floating:
            warnings.append(
                f'{ref} ({placed.type_id}): pin(s) {floating} unconnected — skipped')
            lines.append(f'* skipped: {ref} ({placed.type_id}) — floating pin(s)')
            continue

        pin_nodes: Dict[int, str] = {}
        for pin_num, net in nets_dict.items():
            pin_nodes[pin_num] = _node_for_net(net.name, node_map)

        nl_comp = netlist.components.get(ref)
        value = nl_comp.value if nl_comp else '1'

        element = _element_line(ref, placed.type_id, pin_nodes, value)
        if element is None:
            warnings.append(f'{ref} ({placed.type_id}): no SPICE model — skipped')
            lines.append(f'* skipped: {ref} ({placed.type_id}) — no model')
        else:
            lines.append(element)
            component_count += 1

    return lines, component_count, node_map, warnings


_EXTERNAL_MODEL_MSG = (
    'One or more components reference external SPICE models that this simulator '
    'cannot load. Using generic fallbacks would give incorrect results.\n\n'
    '{conflicts}\n\n'
    'In KiCad Eeschema, open each component\'s properties → Simulation Model '
    'and choose a built-in model type (Passive, Diode, BJT, etc.) without an '
    'external model file. Then re-export the netlist and return to the breadboard.'
)


def _build_netlist(board: Breadboard, netlist: Netlist,
                   terminal_voltages: Dict[str, float]) -> Tuple[str, Optional[str], List[str]]:
    """
    Build a SPICE DC (.op) netlist.  Returns (spice_text, error_or_None, warnings).
    """
    gnd_net = board.terminal_nets.get('GND', '')
    if not gnd_net:
        return '', 'GND terminal not assigned', []

    conflicts = _external_model_conflicts(board, netlist)
    if conflicts:
        return '', _EXTERNAL_MODEL_MSG.format(conflicts='\n'.join(conflicts)), []

    comp_lines, component_count, node_map, warnings = _build_component_lines(board, netlist)

    if component_count == 0:
        return '', 'No simulatable components on board', warnings

    lines: List[str] = ['* Breadboard SPICE netlist', ''] + comp_lines + ['']

    vsin_sources = find_vsin_sources(netlist)
    net_to_vsin: Dict[str, VsinSource] = {src.net_pos: src for src in vsin_sources}

    for term in ('V1', 'V2'):
        net_name = board.terminal_nets.get(term, '')
        if not net_name:
            continue
        spice_node = _node_for_net(net_name, node_map)
        src = net_to_vsin.get(net_name)
        if src:
            # VSIN source: reference its actual negative terminal and use dc as operating point
            neg_node = _node_for_net(src.net_neg, node_map)
            lines.append(f'V_bb_{term}  {spice_node}  {neg_node}  DC {src.voff:.6g}')
        else:
            voltage = terminal_voltages.get(term)
            if voltage is not None:
                lines.append(f'V_bb_{term}  {spice_node}  0  DC {voltage}')

    lines += ['', _MODELS, '.op', '.end', '']
    return '\n'.join(lines), None, warnings


def _build_transient_netlist(
    board: Breadboard,
    netlist: Netlist,
    terminal_voltages: Dict[str, float],
    vsin_sources: List[VsinSource],
    plot_nets: Optional[List[str]] = None,
) -> Tuple[str, Optional[str], List[str], Dict[str, str]]:
    """
    Build a SPICE transient (.tran) netlist.

    Returns (spice_text, error_or_None, warnings, node_map).
    node_map is needed by the caller to map SPICE node names back to net names.
    """
    gnd_net = board.terminal_nets.get('GND', '')
    if not gnd_net:
        return '', 'GND terminal not assigned', [], {}

    conflicts = _external_model_conflicts(board, netlist)
    if conflicts:
        return '', _EXTERNAL_MODEL_MSG.format(conflicts='\n'.join(conflicts)), [], {}

    comp_lines, component_count, node_map, warnings = _build_component_lines(board, netlist)

    if component_count == 0:
        return '', 'No simulatable components on board', warnings, {}

    # Build a map: net_pos → VsinSource for quick lookup
    net_to_vsin: Dict[str, VsinSource] = {src.net_pos: src for src in vsin_sources}

    # Determine simulation timing from VSIN frequencies
    freqs = [src.freq for src in vsin_sources if src.freq > 0]
    if freqs:
        min_freq = min(freqs)
        period = 1.0 / min_freq
        tstop = 5.0 * period
        tstep = tstop / 500.0
    else:
        tstep = 1e-5
        tstop = 5e-3

    lines: List[str] = ['* Breadboard SPICE transient netlist', ''] + comp_lines + ['']

    for term in ('V1', 'V2'):
        net_name = board.terminal_nets.get(term, '')
        if not net_name:
            continue
        spice_node = _node_for_net(net_name, node_map)
        src = net_to_vsin.get(net_name)
        if src:
            neg_node = _node_for_net(src.net_neg, node_map)
            lines.append(
                f'V_bb_{term}  {spice_node}  {neg_node}'
                f'  SIN({src.voff:.6g} {src.vampl:.6g} {src.freq:.6g})'
            )
        else:
            voltage = terminal_voltages.get(term)
            if voltage is not None:
                lines.append(f'V_bb_{term}  {spice_node}  0  DC {voltage}')

    lines.append('')
    lines.append(_MODELS)

    # Determine which nodes to print
    if plot_nets:
        print_nodes = [_node_for_net(n, node_map) for n in plot_nets
                       if n and _node_for_net(n, node_map) != '0']
    else:
        # Print everything that isn't GND
        print_nodes = [v for k, v in node_map.items() if v != '0']

    # Remove duplicates while preserving order
    seen: set = set()
    unique_nodes: List[str] = []
    for n in print_nodes:
        if n not in seen:
            seen.add(n)
            unique_nodes.append(n)

    tstep_s = f'{tstep:.3g}'
    tstop_s = f'{tstop:.3g}'
    lines.append(f'.tran {tstep_s} {tstop_s}')
    if unique_nodes:
        node_list = '  '.join(f'v({n})' for n in unique_nodes)
        lines.append(f'.print tran  {node_list}')
    lines += ['.end', '']

    return '\n'.join(lines), None, warnings, node_map


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


_NGSPICE_ERROR_RE = re.compile(
    r'^\s*(error|fatal)\b',
    re.IGNORECASE,
)
_NGSPICE_WARN_RE = re.compile(
    r'^\s*warning\b',
    re.IGNORECASE,
)
# Lines that are routine ngspice banner / progress noise — not real warnings
_NGSPICE_NOISE_RE = re.compile(
    r'(ngspice.*release|note:|Doing analysis|No\.\ of\ Data|reference value'
    r'|cpu\ time|Total\ analysis|tran:\ step|Transient\ analysis)',
    re.IGNORECASE,
)


def _scan_ngspice_output(output: str) -> Tuple[Optional[str], List[str]]:
    """
    Scan ngspice output for error and warning lines.
    Returns (error_or_None, warnings_list).
    Errors take priority: if any error line is found the first is returned as error.
    """
    errors: List[str] = []
    warnings: List[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or _NGSPICE_NOISE_RE.search(stripped):
            continue
        if _NGSPICE_ERROR_RE.match(stripped):
            errors.append(stripped)
        elif _NGSPICE_WARN_RE.match(stripped):
            warnings.append(stripped)
    if errors:
        return '\n'.join(errors), warnings
    return None, warnings


def _run_ngspice(spice_text: str) -> Tuple[str, Optional[str], List[str]]:
    """
    Write spice_text to a temporary file, run ngspice -b.
    Returns (output, error_or_None, ngspice_warnings).
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
            err_lines = (result.stderr or result.stdout).strip().splitlines()
            snippet = '\n'.join(err_lines[:10]) if err_lines else 'ngspice failed'
            return combined, f'ngspice error (rc={result.returncode}):\n{snippet}', []

        # Even on rc=0, ngspice may emit Error/Warning lines
        scan_err, scan_warns = _scan_ngspice_output(combined)
        return combined, scan_err, scan_warns

    except FileNotFoundError:
        return '', 'ngspice not found on PATH', []

    except subprocess.TimeoutExpired:
        return '', 'ngspice timed out after 30 s', []

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
# Transient output parser
# ---------------------------------------------------------------------------

def _parse_transient_output(
    output: str,
    node_map: Dict[str, str],
) -> Dict[str, TransientTrace]:
    """
    Parse ngspice .print tran tabular output.

    Returns {net_name: TransientTrace}.
    """
    # Invert node_map: spice_node (lowercase) → net_name
    spice_to_net: Dict[str, str] = {}
    for net_name, node in node_map.items():
        spice_to_net[node.lower()] = net_name

    lines = output.splitlines()

    # Find the header line: contains "Index" and "time"
    header_idx = -1
    col_names: List[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        lo = stripped.lower()
        if lo.startswith('index') and 'time' in lo:
            col_names = stripped.split()
            header_idx = i
            break

    if header_idx < 0 or len(col_names) < 3:
        return {}

    # col_names[0] = 'Index', col_names[1] = 'time', col_names[2..] = 'v(node)'
    # Map column index → net_name
    col_nets: List[Optional[str]] = [None, None]  # Index, time slots
    for col in col_names[2:]:
        lo = col.lower()
        # Strip v(...) wrapper if present
        if lo.startswith('v(') and lo.endswith(')'):
            node = lo[2:-1]
        else:
            node = lo
        net = spice_to_net.get(node, node)
        col_nets.append(net)

    # Collect time and value lists
    times_list: List[float] = []
    col_data: List[List[float]] = [[] for _ in col_names]

    for line in lines[header_idx + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith('-') or stripped.lower().startswith('index'):
            continue
        parts = stripped.split()
        if len(parts) < len(col_names):
            continue
        try:
            t = float(parts[1])
            times_list.append(t)
            for j in range(2, len(col_names)):
                col_data[j].append(float(parts[j]))
        except (ValueError, IndexError):
            # Partial row or end of table — stop
            if times_list:
                break

    traces: Dict[str, TransientTrace] = {}
    for j in range(2, len(col_names)):
        net_name = col_nets[j]
        if net_name is None:
            continue
        traces[net_name] = TransientTrace(
            net_name=net_name,
            times=times_list[:],
            values=col_data[j][:],
        )

    return traces


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def simulate(board: Breadboard, netlist: Netlist,
             terminal_voltages: Dict[str, float]) -> SimResult:
    """
    Run a DC .op simulation of the current board state.

    terminal_voltages: {terminal_name: voltage_V} e.g. {'V1': 5.0, 'V2': -5.0}
    GND terminal is always 0 V regardless of what is passed in terminal_voltages.

    Returns a SimResult; .error is None on success.
    """
    spice_text, build_err, build_warnings = _build_netlist(board, netlist, terminal_voltages)
    if build_err:
        return SimResult(
            node_voltages={},
            net_voltages={},
            branch_currents={},
            error=build_err,
            spice_netlist=spice_text,
            warnings=build_warnings,
        )

    output, run_err, run_warns = _run_ngspice(spice_text)
    all_warnings = build_warnings + run_warns
    if run_err:
        return SimResult(
            node_voltages={},
            net_voltages={},
            branch_currents={},
            error=run_err,
            spice_netlist=spice_text,
            spice_output=output or '',
            warnings=all_warnings,
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

    # Invert: spice_node → net_name (lowercase keys to match ngspice output)
    spice_to_net: Dict[str, str] = {}
    for net_name, spice_node in node_map.items():
        key = spice_node.lower()
        if key not in spice_to_net:
            spice_to_net[key] = net_name

    net_voltages: Dict[str, float] = {}
    for spice_node, voltage in node_voltages.items():
        # spice_node is already lowercase (from _parse_output)
        net_name = spice_to_net.get(spice_node, spice_node)
        net_voltages[net_name] = voltage

    # ngspice never outputs node 0 (GND = 0 V by definition) — add it explicitly
    # so that components connected to GND get their pin voltage resolved.
    gnd_net = board.terminal_nets.get('GND', '')
    if gnd_net and gnd_net not in net_voltages:
        net_voltages[gnd_net] = 0.0

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
        spice_output=output,
        warnings=all_warnings,
    )


def simulate_transient(
    board: Breadboard,
    netlist: Netlist,
    terminal_voltages: Dict[str, float],
    plot_nets: Optional[List[str]] = None,
) -> SimResult:
    """
    Run a transient (.tran) simulation using VSIN source parameters from the netlist.

    plot_nets: net names to include in .print tran (defaults to all non-GND nodes).
    Returns a SimResult with .transient_traces populated on success.
    """
    vsin_sources = find_vsin_sources(netlist)
    if not vsin_sources:
        return SimResult(
            node_voltages={}, net_voltages={}, branch_currents={},
            error='No VSIN sources found in netlist — cannot run transient analysis.',
            spice_netlist='',
        )

    spice_text, build_err, build_warnings, node_map = _build_transient_netlist(
        board, netlist, terminal_voltages, vsin_sources, plot_nets
    )
    if build_err:
        return SimResult(
            node_voltages={}, net_voltages={}, branch_currents={},
            error=build_err,
            spice_netlist=spice_text,
            warnings=build_warnings,
        )

    output, run_err, run_warns = _run_ngspice(spice_text)
    all_warnings = build_warnings + run_warns
    if run_err:
        return SimResult(
            node_voltages={}, net_voltages={}, branch_currents={},
            error=run_err,
            spice_netlist=spice_text,
            spice_output=output or '',
            warnings=all_warnings,
        )

    traces = _parse_transient_output(output, node_map)
    if not traces:
        return SimResult(
            node_voltages={}, net_voltages={}, branch_currents={},
            error='Transient simulation produced no output — check the console.',
            spice_netlist=spice_text,
            spice_output=output or '',
            warnings=all_warnings,
        )

    return SimResult(
        node_voltages={}, net_voltages={}, branch_currents={},
        error=None,
        spice_netlist=spice_text,
        spice_output=output,
        warnings=all_warnings,
        transient_traces=traces,
    )
