"""
Circuit validator.

Compares the student's breadboard state against the schematic netlist and
reports two classes of error:

  OPEN_NET   — pins that belong to the same net are not electrically connected.
  SHORT      — pins from different nets are electrically connected.

Usage:
    results = validate(breadboard, netlist, placements_type_map)
    for r in results:
        print(r)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from .breadboard import Breadboard, Hole, TieHole, Terminal, TERMINAL_NAMES, UnionFind
from .components import guess_type_id, ALL_DEFS
from .netlist import Net, Netlist


class IssueKind(Enum):
    OPEN_NET = 'open_net'
    SHORT = 'short'
    UNPLACED = 'unplaced'


@dataclass
class ValidationIssue:
    kind: IssueKind
    net_name: str
    description: str
    # Holes involved (for canvas highlighting)
    holes: List[Hole] = field(default_factory=list)

    def __str__(self):
        return f"[{self.kind.name}] {self.net_name}: {self.description}"


@dataclass
class ValidationResult:
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.issues) == 0

    def __str__(self):
        if self.ok:
            return "Circuit OK — all nets match."
        return '\n'.join(str(i) for i in self.issues)


def _symmetric_pin_overrides(
    board: Breadboard, netlist: Netlist, uf: UnionFind
) -> Dict[Tuple[str, int], Hole]:
    """
    For non-polar 2-pin components (R, L, C) determine whether the student
    placed the component in reversed orientation and, if so, return a mapping
    that swaps pin 1 ↔ pin 2 so the circuit still validates.

    A swap is applied when either:
      (a) h2 connects to other placed pins on net1 but h1 does not, OR
      (b) h1 connects to placed pins on net2 but h2 does not —
          which catches the case where net1 is a single-endpoint net (e.g. a
          schematic signal label with no other placed component) but the
          component is clearly wired into net2's side.
    """
    override: Dict[Tuple[str, int], Hole] = {}

    # ref → {pin_num → net_name}
    comp_pin_nets: Dict[str, Dict[int, str]] = {}
    for net in netlist.nets:
        for pn in net.pins:
            comp_pin_nets.setdefault(pn.ref, {})[pn.pin] = net.name

    # net_name → placed holes (excluding terminals handled separately)
    def placed_holes_for_net(net_name: str, exclude_ref: str) -> List[Hole]:
        holes: List[Hole] = []
        for net in netlist.nets:
            if net.name == net_name:
                for pn in net.pins:
                    if pn.ref != exclude_ref:
                        h = board.hole_for_pin(pn.ref, pn.pin)
                        if h is not None:
                            holes.append(h)
                break
        return holes

    for ref, placed in board.placements.items():
        comp_def = ALL_DEFS.get(placed.type_id)
        if not (comp_def and comp_def.symmetric and comp_def.pin_count == 2):
            continue
        h1 = placed.pin_holes.get(1)
        h2 = placed.pin_holes.get(2)
        if h1 is None or h2 is None:
            continue

        pin_nets = comp_pin_nets.get(ref, {})
        net1 = pin_nets.get(1)
        net2 = pin_nets.get(2)
        if not net1:
            continue

        net1_other = placed_holes_for_net(net1, ref)
        net2_other = placed_holes_for_net(net2, ref) if net2 else []

        h1_on_net1 = bool(net1_other) and any(uf.connected(h1, h) for h in net1_other)
        h2_on_net1 = bool(net1_other) and any(uf.connected(h2, h) for h in net1_other)
        h1_on_net2 = bool(net2_other) and any(uf.connected(h1, h) for h in net2_other)
        h2_on_net2 = bool(net2_other) and any(uf.connected(h2, h) for h in net2_other)

        swap = (not h1_on_net1 and h2_on_net1) or (h1_on_net2 and not h2_on_net2)
        if swap:
            override[(ref, 1)] = h2
            override[(ref, 2)] = h1

    return override


def validate(board: Breadboard, netlist: Netlist) -> ValidationResult:
    """
    Validate the student's breadboard against the schematic netlist.

    Steps:
      1. Check all components are placed.
      2. Build the connectivity graph.
      3. For each schematic net, collect the holes of all its pins.
         If they are not all in the same connected component → OPEN_NET.
      4. Collect all (root, net_name) pairs; if two different nets share a
         root → SHORT.
    """
    result = ValidationResult()
    uf = board.build_connectivity()

    # Step 1 — unplaced physical components
    # Virtual components (simulation sources, power symbols) have no known
    # type_id and are handled via terminal assignments instead.
    for ref, comp in netlist.components.items():
        if board.get_placement(ref) is None:
            type_id = guess_type_id(ref, comp.value, comp.symbol, comp.lib)
            if type_id is not None:
                result.issues.append(ValidationIssue(
                    kind=IssueKind.UNPLACED,
                    net_name='',
                    description=f"{ref} ({comp.value}) is not placed on the breadboard.",
                ))

    # For symmetric (non-polar) components, determine optimal pin-hole mapping
    pin_override = _symmetric_pin_overrides(board, netlist, uf)

    # Step 2 — build a map: net_name → list of holes
    # comp_pin_counts tracks only placed-component pins (not terminals) so that
    # single-endpoint nets (e.g. a wire ending only in a schematic label like
    # "OUTPUT") are not flagged as OPEN_NET — they have only 1 component pin
    # and are deliberately unconnected to another component.
    comp_pin_counts: Dict[str, int] = {}
    net_holes: Dict[str, List[Hole]] = {}

    for net in netlist.nets:
        holes: List[Hole] = []
        for pin_node in net.pins:
            key = (pin_node.ref, pin_node.pin)
            hole = pin_override.get(key) if key in pin_override else board.hole_for_pin(pin_node.ref, pin_node.pin)
            if hole is not None:
                holes.append(hole)
        if holes:
            comp_pin_counts[net.name] = len(holes)
            net_holes[net.name] = holes

    # Add terminal holes — count them so power-supply nets are validated
    terminal_pin_counts: Dict[str, int] = {}
    for term_name in TERMINAL_NAMES:
        net_name = board.get_terminal_net(term_name)
        if net_name:
            net_holes.setdefault(net_name, []).append(Terminal(term_name))
            terminal_pin_counts[net_name] = terminal_pin_counts.get(net_name, 0) + 1

    # Step 3 — open nets
    # Require at least 2 total endpoints (placed component pins + assigned terminals);
    # single-endpoint label-only nets are intentionally exempt.
    for net_name, holes in net_holes.items():
        total = comp_pin_counts.get(net_name, 0) + terminal_pin_counts.get(net_name, 0)
        if total < 2:
            continue
        roots = {uf.find(h) for h in holes}
        if len(roots) > 1:
            result.issues.append(ValidationIssue(
                kind=IssueKind.OPEN_NET,
                net_name=net_name,
                description=(
                    f"Net '{net_name}' is not fully connected "
                    f"({len(roots)} disconnected groups)."
                ),
                holes=holes,
            ))

    # Step 4 — shorts: two nets sharing the same connected component
    root_to_nets: Dict[object, Set[str]] = {}
    for net_name, holes in net_holes.items():
        for h in holes:
            root = uf.find(h)
            root_to_nets.setdefault(root, set()).add(net_name)

    for root, net_names in root_to_nets.items():
        if len(net_names) > 1:
            names = ', '.join(sorted(net_names))
            # Collect all holes for these nets (for highlighting)
            shorted_holes = []
            for net_name in net_names:
                shorted_holes.extend(net_holes.get(net_name, []))
            result.issues.append(ValidationIssue(
                kind=IssueKind.SHORT,
                net_name=names,
                description=f"Nets {names} are shorted together.",
                holes=shorted_holes,
            ))

    return result
