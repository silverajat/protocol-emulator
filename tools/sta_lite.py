#!/usr/bin/env python3
"""Pre-layout static timing estimate for a Yosys-mapped netlist (JSON) against a Liberty file.

The real sign-off is OpenROAD/OpenSTA in the GDS flow, which takes hours per run. This gives a
named critical path in about a minute, so RTL timing changes can be compared before a GDS run:

  * cell delays and output slews from the Liberty NLDM tables (bilinear interpolation on input
    slew and load), worst of rise/fall, slew propagated along the path;
  * load = input-pin capacitance of the fanout + a per-fanout wire estimate (--wire-ff);
    nets with more than --max-fanout loads are assumed buffered (as the flow's resizer does):
    the driver sees --max-fanout loads and each extra level of an 8-way buffer tree adds one
    buffer delay;
  * launch at flip-flop CLK->Q (ideal clock), capture at period - setup;
  * primary inputs arrive at --input-delay (Tiny Tapeout's flow uses 20% of the period).

No placement, so long wires and congestion are not modelled: expect post-route paths to be
longer. Use it to rank RTL variants and to find which logic is on the path.

  python3 tools/sta_lite.py netlist.json lib.lib --period 30 [--paths 5]
"""
import argparse
import bisect
import json
import re
import sys
from collections import defaultdict


# ---------------- Liberty ----------------
def lib_tokens(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = text.replace("\\\n", " ")
    return re.findall(r'"[^"]*"|[A-Za-z0-9_.\-+!\[\]:]+|[{}();,]', text)


def lib_parse(text):
    """Returns nested groups: {'type', 'name', 'attrs': {k: v}, 'complex': {k: [args]},
    'groups': [...]}."""
    toks = lib_tokens(text)
    pos = 0

    def group(gtype, gname):
        nonlocal pos
        g = {"type": gtype, "name": gname, "attrs": {}, "complex": defaultdict(list), "groups": []}
        while pos < len(toks):
            t = toks[pos]
            if t == "}":
                pos += 1
                return g
            name = t
            pos += 1
            if toks[pos] == ":":
                pos += 1
                val = toks[pos]
                pos += 1
                while toks[pos] != ";":
                    pos += 1
                pos += 1
                g["attrs"][name] = val.strip('"')
            elif toks[pos] == "(":
                pos += 1
                args = []
                while toks[pos] != ")":
                    if toks[pos] != ",":
                        args.append(toks[pos].strip('"'))
                    pos += 1
                pos += 1
                if toks[pos] == "{":
                    pos += 1
                    g["groups"].append(group(name, args[0] if args else ""))
                else:
                    if toks[pos] == ";":
                        pos += 1
                    g["complex"][name].append(args)
            else:
                pos += 1
        return g

    while toks[pos] != "{":
        pos += 1
    pos += 1
    return group("library", "")


class Table:
    def __init__(self, g):
        def nums(s):
            return [float(x) for x in s.replace(" ", "").split(",") if x]
        i1 = g["complex"].get("index_1")
        i2 = g["complex"].get("index_2")
        vals = g["complex"]["values"][0]
        self.x = nums(i1[0][0]) if i1 else [0.0]
        self.y = nums(i2[0][0]) if i2 else [0.0]
        rows = [nums(v) for v in vals]
        if len(rows) == 1 and len(self.x) > 1 and len(rows[0]) == len(self.x) * len(self.y):
            r = rows[0]
            rows = [r[i * len(self.y):(i + 1) * len(self.y)] for i in range(len(self.x))]
        self.v = rows

    @staticmethod
    def _seg(axis, q):
        if len(axis) == 1:
            return 0, 0, 0.0
        i = min(max(bisect.bisect_right(axis, q) - 1, 0), len(axis) - 2)
        t = (q - axis[i]) / (axis[i + 1] - axis[i])
        return i, i + 1, t

    def __call__(self, a, b=0.0):
        i0, i1, ti = self._seg(self.x, a)
        j0, j1, tj = self._seg(self.y, b)
        v = self.v
        if len(self.y) == 1:
            return v[i0][0] + (v[i1][0] - v[i0][0]) * ti
        top = v[i0][j0] + (v[i0][j1] - v[i0][j0]) * tj
        bot = v[i1][j0] + (v[i1][j1] - v[i1][j0]) * tj
        return top + (bot - top) * ti


class Cell:
    def __init__(self, g):
        self.name = g["name"]
        self.pins = {}            # name -> (direction, cap)
        self.arcs = defaultdict(list)   # out pin -> [(related, type, delay tables, slew tables)]
        self.setup = {}           # data pin -> table (constraint)
        self.ff_clock = None
        for sub in g["groups"]:
            if sub["type"] in ("ff", "latch"):
                self.ff_clock = re.sub(r"[!()\s]", "", sub["attrs"].get("clocked_on", sub["attrs"].get("enable", "")))
            if sub["type"] == "pin":
                d = sub["attrs"].get("direction", "")
                cap = float(sub["attrs"].get("capacitance", "0") or 0)
                self.pins[sub["name"]] = (d, cap)
                for t in sub["groups"]:
                    if t["type"] != "timing":
                        continue
                    rel = t["attrs"].get("related_pin", "")
                    ttype = t["attrs"].get("timing_type", "combinational")
                    tabs = {x["type"]: Table(x) for x in t["groups"] if "values" in x["complex"]}
                    if ttype.startswith("setup"):
                        for k in ("rise_constraint", "fall_constraint"):
                            if k in tabs:
                                self.setup.setdefault(sub["name"], []).append(tabs[k])
                    elif ttype in ("combinational", "rising_edge", "combinational_rise",
                                   "combinational_fall", "falling_edge"):
                        dl = [tabs[k] for k in ("cell_rise", "cell_fall") if k in tabs]
                        sl = [tabs[k] for k in ("rise_transition", "fall_transition") if k in tabs]
                        if dl:
                            self.arcs[sub["name"]].append((rel, ttype, dl, sl))

    @property
    def is_seq(self):
        return self.ff_clock is not None


def load_lib(path):
    lib = lib_parse(open(path).read())
    return {g["name"]: Cell(g) for g in lib["groups"] if g["type"] == "cell"}


# ---------------- netlist ----------------
def best_name(names):
    pub = [n for n in names if not n.startswith("$")]
    pool = pub or names
    return min(pool, key=lambda n: (len(n), n)) if pool else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("netlist")
    ap.add_argument("liberty")
    ap.add_argument("--period", type=float, default=30.0, help="ns")
    ap.add_argument("--top")
    ap.add_argument("--wire-ff", type=float, default=4.0, help="wire capacitance per fanout, fF")
    ap.add_argument("--input-delay", type=float, default=None, help="ns (default 20%% of period)")
    ap.add_argument("--clk-slew", type=float, default=0.1)
    ap.add_argument("--paths", type=int, default=3)
    ap.add_argument("--max-fanout", type=int, default=8)
    ap.add_argument("--buffer", default="sg13g2_buf_4")
    ap.add_argument("--quiet-path", action="store_true", help="only list endpoints")
    a = ap.parse_args()
    in_delay = 0.2 * a.period if a.input_delay is None else a.input_delay

    cells = load_lib(a.liberty)
    nl = json.load(open(a.netlist))
    top = a.top or next(n for n, m in nl["modules"].items() if m.get("attributes", {}).get("top"))
    mod = nl["modules"][top]

    netname = defaultdict(list)
    for n, info in mod["netnames"].items():
        for i, b in enumerate(info["bits"]):
            if isinstance(b, int):
                netname[b].append(n if len(info["bits"]) == 1 else f"{n}[{i}]")
    name_of = lambda b: best_name(netname[b])

    driver = {}               # net -> (inst, pin)
    loads = defaultdict(list)  # net -> [(inst, pin)]
    inst = mod["cells"]
    for iname, c in inst.items():
        lc = cells.get(c["type"])
        if lc is None:
            continue
        for pin, bits in c["connections"].items():
            for b in bits:
                if not isinstance(b, int):
                    continue
                d = lc.pins.get(pin, ("input", 0))[0]
                if d == "output":
                    driver[b] = (iname, pin)
                else:
                    loads[b].append((iname, pin))
    pis, pos_ = set(), set()
    for p, info in mod["ports"].items():
        for b in info["bits"]:
            if isinstance(b, int):
                (pis if info["direction"] == "input" else pos_).add(b)

    import math
    buf = cells[a.buffer]
    buf_in = next(c for d, c in buf.pins.values() if d == "input")
    buf_arc = next(iter(buf.arcs.values()))[0]
    tree_load = a.max_fanout * (buf_in + a.wire_ff * 1e-3)
    buf_delay = max(t(a.clk_slew, tree_load) for t in buf_arc[2])

    def net_load(b):
        cap = 0.0
        for iname, pin in loads[b]:
            cap += cells[inst[iname]["type"]].pins.get(pin, ("", 0))[1]
        n = len(loads[b]) + (1 if b in pos_ else 0)
        if n > a.max_fanout:
            return cap / n * a.max_fanout + a.max_fanout * a.wire_ff * 1e-3
        return cap + n * a.wire_ff * 1e-3

    def tree_delay(b):
        n = len(loads[b]) + (1 if b in pos_ else 0)
        if n <= a.max_fanout:
            return 0.0
        return math.ceil(math.log(n / a.max_fanout) / math.log(8)) * buf_delay

    arr, slew, prev = {}, {}, {}     # net -> ns, ns, (net_in, inst) predecessor
    # sources
    for b in pis:
        arr[b], slew[b], prev[b] = in_delay, a.clk_slew, None
    for iname, c in inst.items():
        lc = cells.get(c["type"])
        if lc is None or not lc.is_seq:
            continue
        for opin, arcs in lc.arcs.items():
            for b in c["connections"].get(opin, []):
                if not isinstance(b, int):
                    continue
                L = net_load(b)
                d = max(max(t(a.clk_slew, L) for t in dl) for rel, tt, dl, sl in arcs if tt.endswith("edge"))
                s = max(max(t(a.clk_slew, L) for t in sl) for rel, tt, dl, sl in arcs if tt.endswith("edge"))
                arr[b], slew[b], prev[b] = d + tree_delay(b), s, ("clk", iname)

    # combinational cells in topological order (Kahn on cell inputs)
    comb = [i for i, c in inst.items() if c["type"] in cells and not cells[c["type"]].is_seq]
    need = {}
    users = defaultdict(list)
    for i in comb:
        c = inst[i]
        lc = cells[c["type"]]
        ins = [b for p, bits in c["connections"].items() if lc.pins.get(p, ("input",))[0] != "output"
               for b in bits if isinstance(b, int)]
        need[i] = sum(1 for b in ins if b not in arr)
        for b in ins:
            if b not in arr:
                users[b].append(i)
    ready = [i for i in comb if need[i] == 0]
    done = 0
    while ready:
        i = ready.pop()
        done += 1
        c = inst[i]
        lc = cells[c["type"]]
        for opin, arcs in lc.arcs.items():
            for ob in c["connections"].get(opin, []):
                if not isinstance(ob, int):
                    continue
                L = net_load(ob)
                best = None
                for rel, tt, dl, sl in arcs:
                    for ib in c["connections"].get(rel, []):
                        if not isinstance(ib, int) or ib not in arr:
                            continue
                        si = slew[ib]
                        d = max(t(si, L) for t in dl)
                        s = max(t(si, L) for t in sl) if sl else si
                        t_ = arr[ib] + d + tree_delay(ob)
                        if best is None or t_ > best[0]:
                            best = (t_, s, ib)
                if best is None:
                    continue
                if ob not in arr or best[0] > arr[ob]:
                    arr[ob], slew[ob], prev[ob] = best[0], best[1], (best[2], i)
                for u in users.pop(ob, []):
                    need[u] -= 1
                    if need[u] == 0:
                        ready.append(u)
    if done != len(comb):
        print(f"warning: {len(comb) - done} cells not reached (loops or undriven inputs)", file=sys.stderr)

    # endpoints
    ends = []
    for iname, c in inst.items():
        lc = cells.get(c["type"])
        if lc is None or not lc.is_seq:
            continue
        for pin, tabs in lc.setup.items():
            for b in c["connections"].get(pin, []):
                if isinstance(b, int) and b in arr:
                    su = max(t(slew[b], a.clk_slew) for t in tabs)
                    ends.append((a.period - su - arr[b], b, f"{iname}/{pin}", su))
    for b in pos_:
        if b in arr:
            ends.append((a.period - in_delay - arr[b], b, f"output {name_of(b)}", 0.0))
    ends.sort()

    # flip-flop count, depth stats
    nff = sum(1 for c in inst.values() if c["type"] in cells and cells[c["type"]].is_seq)
    print(f"{top}: {len(inst)} cells ({nff} flip-flops), period {a.period} ns, wire {a.wire_ff} fF/fanout")
    wns = ends[0][0] if ends else 0
    print(f"worst slack {wns:.3f} ns  ->  estimated fmax {1000 / (a.period - wns):.1f} MHz (pre-layout)")
    seen = set()
    shown = 0
    for sl, b, ep, su in ends:
        if shown >= a.paths:
            break
        # one path per distinct start->end group (skip bits of the same bus ending the same way)
        path = []
        n = b
        while n is not None and prev.get(n):
            p = prev[n]
            if p[0] == "clk":
                path.append((n, p[1], "clk"))
                break
            path.append((n, p[1], p[0]))
            n = p[0]
        start = name_of(path[-1][0]) if path else name_of(b)
        key = (re.sub(r"\[\d+\]", "", start), re.sub(r"\[\d+\]", "", name_of(b)))
        if key in seen:
            continue
        seen.add(key)
        shown += 1
        print(f"\nslack {sl:7.3f} ns   arrival {arr[b]:.3f}   endpoint {ep} ({name_of(b)})   "
              f"setup {su:.3f}   cells {len(path)}")
        if a.quiet_path:
            continue
        for n, i, src in reversed(path):
            nm = name_of(n)
            print(f"   {arr[n]:7.3f}  {inst[i]['type']:<18} {'' if nm.startswith('$') else nm}"
                  f"  (fanout {len(loads[n])})")


if __name__ == "__main__":
    main()
