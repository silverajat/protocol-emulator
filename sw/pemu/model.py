"""Cycle-accurate golden model of the protocol emulator core (docs/ISA.md).

Core.step(ui_in, uio_in, bus) advances one clock and returns the read data seen during
that clock. Outputs (uo_gp, uio_out, uio_oe, irq) are properties of the state after the step.
The RTL is checked against this model cycle by cycle (test/test_diff.py).
"""

M32 = 0xFFFFFFFF
N_ENG = 3
ID = 0x50454D55
CFG1_MASK = 0x3FFFFFFF


OPS = ['jmp', 'wait', 'in', 'out', 'pushpull', 'mov', 'set', 'ctl']
# execution counters (coverage for the random tests)
STATS = dict.fromkeys(OPS + ['paced', 'stuff_out', 'stuff_in', 'autopull', 'autopush', 'crc_tap',
                            'serial', 'clocked'], 0)


def bit(v, i):
    return (v >> i) & 1


def lowmask(n):
    return M32 if n >= 32 else (1 << n) - 1


def window(base, cnt):
    return 0 if cnt == 0 else (((1 << cnt) - 1) << base) & 0xFFFF


def bitrev32(v):
    return int(f"{v & M32:032b}"[::-1], 2)


class Fifo:
    def __init__(self, depth=4):
        self.depth = depth
        self.q = []

    @property
    def empty(self):
        return not self.q

    @property
    def full(self):
        return len(self.q) >= self.depth

    @property
    def head(self):
        return self.q[0] if self.q else 0


class Engine:
    def __init__(self):
        self.cfg = [0x0FC00000, 0, 0, 0]  # WRAP_TOP = 63
        self.poly = 0
        self.crcw = 0
        self.tx = Fifo()
        self.rx = Fifo()
        self.nexec = 0          # instructions executed (test statistics, not hardware state)
        self.reset()

    def reset(self):
        self.pc = (self.cfg[2] >> 21) & 63      # START
        self.x = self.y = self.osr = self.isr = 0
        self.osr_cnt, self.isr_cnt = 32, 0
        self.dly = self.div = self.bt_cnt = 0
        self.pend_start = self.pend_samp = 0
        self.crc = 0
        self.tap_out = self.tap_in = self.stuff_out_en = self.stuff_in_en = 0
        self.last_bit = self.last_out = 0
        self.run = 0
        self.sbit = self.sph = 0
        self.tx.q.clear()
        self.rx.q.clear()

    # config fields
    def f(self, reg, lo, width):
        return (self.cfg[reg] >> lo) & ((1 << width) - 1)

    def step(self, en, pins_in, time, events, imem, rs_prev):
        """Evaluate one clock: returns (requests to the core, new register values)."""
        f = self.f
        clkdiv, wrap_bot, wrap_top, clk_pin = f(0, 0, 16), f(0, 16, 6), f(0, 22, 6), f(0, 28, 4)
        out_base, out_cnt, set_base, set_cnt = f(1, 0, 4), f(1, 4, 4), f(1, 8, 4), f(1, 12, 4)
        in_base, jmp_pin, monitor = f(1, 16, 4), f(1, 20, 4), f(1, 24, 1)
        out_shr, in_shr, autopull, autopush = f(1, 25, 1), f(1, 26, 1), f(1, 27, 1), f(1, 28, 1)
        stretch = f(1, 29, 1)
        pull_th = f(2, 0, 5) or 32
        push_th = f(2, 5, 5) or 32
        stuff_n, stuff_ones = f(2, 10, 4), f(2, 14, 1)
        rs_pin, rs_edge = f(2, 15, 4), f(2, 19, 2)
        ser_cfg, clk_en, cpol, cpha, duplex = f(2, 27, 1), f(2, 28, 1), f(2, 29, 1), f(2, 30, 1), f(2, 31, 1)
        bt_period, bt_point = f(3, 0, 16), f(3, 16, 16)
        wm1 = self.crcw
        crc_mask = M32 >> (31 - wm1)

        req = dict(pv_mask=0, pv_val=0, pd_mask=0, pd_val=0, ev_set=0, ev_clr=0,
                   tx_pop=False, rx_push=None, bt_set=None)
        new = {}

        instr = imem[self.pc]
        op, dl, pace, a = instr >> 13, (instr >> 10) & 7, (instr >> 9) & 1, instr & 0x1FF
        f86 = a >> 6
        nfld = (a & 31) or 32
        is_out, is_in = op == 3, op == 2

        # serial unit
        ser_on = bool(ser_cfg and (is_out or is_in) and f86 == 0)
        ser_clk = ser_on and bool(clk_en)
        push_align = op == 4 and not bit(a, 8) and bit(a, 5) and in_shr
        n = 1 if ser_on else (32 - self.isr_cnt) if push_align else nfld
        nsh = 32 - n
        clk_lead = (1 - cpol) if cpha else cpol
        clk_trail = cpol if cpha else 1 - cpol
        ph_out = (not ser_clk) or self.sph == 1
        ph_in = (not ser_clk) or self.sph == 3
        ph_pin = ser_clk and self.sph in (0, 2, 4)
        clk_lvl = clk_trail if self.sph == 2 else clk_lead if self.sph == 0 else cpol
        stretch_wait = ser_clk and self.sph == 3 and stretch and bit(pins_in, clk_pin) != clk_trail

        # bit timer (MONITOR: ignore resync edges while this engine drives a 0)
        rs_cur = bit(pins_in, rs_pin)
        rs_raw = ((rs_edge & 1) and rs_prev and not rs_cur) or ((rs_edge & 2) and not rs_prev and rs_cur)
        rs_hit = bool(rs_raw) and not (monitor and not self.last_out)
        bt_on = bt_period != 0
        bt_eff = 0 if rs_hit else self.bt_cnt
        bt_start = bt_on and bt_eff == 0
        bt_samp = bt_on and bt_eff == bt_point

        rot = ((pins_in >> in_base) | (pins_in << (16 - in_base))) & 0xFFFF
        crc_la = (self.crc << (31 - wm1)) & M32
        srcs = [rot, self.x, self.y, 0, time, crc_la, self.isr, self.osr]
        in_data = 0 if push_align else srcs[(a & 7) if op == 5 else f86]

        stuff_out_on = self.stuff_out_en and stuff_n != 0
        stuff_in_on = self.stuff_in_en and stuff_n != 0
        stuff_full = self.run == stuff_n
        stuff_bit = 0 if stuff_ones else 1 - self.last_bit

        out_act = is_out and ph_out
        out_pin1 = out_act and f86 == 0 and n == 1
        do_stuff_out = out_pin1 and stuff_out_on and stuff_full and not ser_clk
        osr_empty = self.osr_cnt >= pull_th
        need_pull = out_act and autopull and osr_empty and not do_stuff_out
        osr_src = self.tx.head if need_pull else self.osr
        ocnt_base = 0 if need_pull else self.osr_cnt
        if out_shr:
            out_data = osr_src & lowmask(n)
            osr_after = (osr_src >> n) if n < 32 else 0
        else:
            out_data = osr_src >> nsh
            osr_after = ((osr_src << n) & M32) if n < 32 else 0
        ocnt_after = min(32, ocnt_base + n)

        in_act = (is_in or (ser_clk and is_out and duplex)) and ph_in
        in_pin1 = in_act and f86 == 0 and n == 1
        do_stuff_in = in_pin1 and stuff_in_on and stuff_full and not ser_clk
        if in_shr:
            isr_shifted = ((self.isr >> n) if n < 32 else 0) | ((in_data << nsh) & M32)
        else:
            isr_shifted = (((self.isr << n) & M32) if n < 32 else 0) | (in_data & lowmask(n))
        icnt_after = min(32, self.isr_cnt + n)
        autopush_hit = in_act and autopush and not do_stuff_in and icnt_after >= push_th

        def run_next(b):
            if stuff_ones:
                return min(15, self.run + 1) if b else 0
            return min(15, self.run + 1) if (b == self.last_bit and self.run != 0) else 1

        run_nx_o, run_nx_i = run_next(out_data & 1), run_next(rot & 1)
        trail_stuff = (stuff_out_on and run_nx_o == stuff_n) if is_out else (stuff_in_on and run_nx_i == stuff_n)
        stuffing_bit = do_stuff_out or do_stuff_in

        pp_pull, pp_block, pp_if = bit(a, 8), bit(a, 7), bit(a, 6)
        push_skip = pp_if and self.isr_cnt < push_th
        pull_skip = pp_if and not osr_empty

        mop = (a >> 3) & 3
        mov_val = (~in_data & M32) if mop == 1 else bitrev32(in_data) if mop == 2 else in_data

        wsrc = (a >> 6) & 3
        if wsrc == 0:
            wait_val = bit(pins_in, a & 15)
        elif wsrc == 1:
            wait_val = bit(pins_in, (in_base + (a & 15)) & 15)
        elif wsrc == 2:
            wait_val = bit(events, a & 3)
        else:
            wait_val = 0

        jpin = (bit(pins_in, jmp_pin) == self.last_out) if monitor else bit(pins_in, jmp_pin)
        jtake = [True, self.x == 0, self.x != 0, not self.tx.empty, self.y != 0, self.x != self.y,
                 bool(jpin), not osr_empty][f86]

        tick = en and self.div == 0
        active = tick and self.dly == 0
        if op == 1:
            op_stall = wait_val != bit(a, 8)
        elif op == 2:
            op_stall = (autopush_hit and self.rx.full) or stretch_wait
        elif op == 3:
            op_stall = (need_pull and self.tx.empty) or (autopush_hit and self.rx.full) or stretch_wait
        elif op == 4:
            op_stall = (not pull_skip and self.tx.empty and pp_block) if pp_pull else \
                       (not push_skip and self.rx.full and pp_block)
        else:
            op_stall = False
        pace_out = (op == 3 and f86 in (0, 4)) or (op == 6 and f86 in (0, 3)) or (op == 5 and f86 in (0, 3))
        if ser_clk:
            pace_ok = True
        elif pace_out:
            pace_ok = (self.pend_start and not rs_hit) or bt_start
        else:
            pace_ok = (self.pend_samp and not rs_hit) or bt_samp
        execute = active and not op_stall and not (pace and not pace_ok)

        ser_last = ((self.sbit + 1) & 63) == nfld
        if ser_clk:
            done = self.sph == 4 or (self.sph == 3 and ser_last and cpha)
        elif ser_on:
            done = (self.sbit == nfld) if stuffing_bit else (ser_last and not (nfld != 1 and trail_stuff))
        else:
            done = not stuffing_bit

        if not en:
            return req, new

        new['div'] = clkdiv if self.div == 0 else self.div - 1
        if not bt_on:
            new['bt_cnt'] = 0
        else:
            new['bt_cnt'] = 0 if bt_eff == bt_period - 1 else bt_eff + 1
        if execute and pace:
            new['pend_start'] = new['pend_samp'] = 0
        elif rs_hit:
            new['pend_start'], new['pend_samp'] = int(bt_start), int(bt_samp)
        else:
            new['pend_start'] = int(self.pend_start or bt_start)
            new['pend_samp'] = int(self.pend_samp or bt_samp)
        if tick and self.dly != 0:
            new['dly'] = self.dly - 1

        do_out = execute and out_act and not do_stuff_out
        do_in = execute and in_act and not do_stuff_in

        # CRC feed
        crc_bit = None
        if do_out and out_pin1 and self.tap_out:
            crc_bit = out_data & 1
        if execute and is_out and f86 == 7:
            crc_bit = out_data & 1
        if do_in and in_pin1 and self.tap_in:
            crc_bit = rot & 1
        if crc_bit is not None:
            fb = crc_bit ^ bit(self.crc, wm1)
            new['crc'] = ((self.crc << 1) & crc_mask) ^ ((self.poly & crc_mask) if fb else 0)

        if not execute:
            return req, new

        self.nexec += 1
        st = STATS
        st[OPS[op]] += 1
        if pace:
            st['paced'] += 1
        if do_stuff_out:
            st['stuff_out'] += 1
        if do_stuff_in:
            st['stuff_in'] += 1
        if do_out and need_pull:
            st['autopull'] += 1
        if do_in and autopush_hit:
            st['autopush'] += 1
        if crc_bit is not None:
            st['crc_tap'] += 1
        if ser_on:
            st['serial'] += 1
        if ser_clk:
            st['clocked'] += 1

        if done:
            new['pc'] = wrap_bot if self.pc == wrap_top else (self.pc + 1) & 63
        if done or not ser_clk:
            new['dly'] = dl
        if ser_clk:
            if done:
                new['sph'], new['sbit'] = 0, 0
            elif self.sph == 3:
                if ser_last:
                    new['sph'] = 4
                else:
                    new['sph'], new['sbit'] = 0, (self.sbit + 1) & 63
            else:
                new['sph'] = self.sph + 1
        elif ser_on:
            if done:
                new['sbit'] = 0
            elif not stuffing_bit:
                new['sbit'] = nfld if ser_last else (self.sbit + 1) & 63

        out_win = window(out_base, out_cnt)
        set_win = window(set_base, set_cnt)

        if do_stuff_out:
            new['last_bit'] = new['last_out'] = stuff_bit
            new['run'] = 0 if stuff_ones else 1
        if do_stuff_in:
            new['last_bit'] = rot & 1
            new['run'] = (rot & 1) if stuff_ones else 1

        if do_out:
            if need_pull:
                req['tx_pop'] = True
            new['osr'], new['osr_cnt'] = osr_after, ocnt_after
            if out_pin1:
                new['last_out'] = out_data & 1
            if out_pin1 and stuff_out_on:
                new['last_bit'], new['run'] = out_data & 1, run_nx_o
            if f86 == 1:
                new['x'] = out_data
            elif f86 == 2:
                new['y'] = out_data
            elif f86 == 5:
                new['pc'] = out_data & 63
            elif f86 == 6:
                new['isr'], new['isr_cnt'] = out_data, n
        if do_in:
            if autopush_hit:
                new['isr'], new['isr_cnt'] = 0, 0
                req['rx_push'] = isr_shifted
            else:
                new['isr'], new['isr_cnt'] = isr_shifted, icnt_after
            if in_pin1 and stuff_in_on:
                new['last_bit'], new['run'] = rot & 1, run_nx_i

        # pins and events
        if op in (2, 3):
            if ph_pin:
                req['pv_mask'] = 1 << clk_pin
                req['pv_val'] = clk_lvl << clk_pin
            elif out_act:
                if f86 == 0:
                    req['pv_mask'] = out_win
                    req['pv_val'] = (((stuff_bit if do_stuff_out else out_data) & 0xFFFF) << out_base) & 0xFFFF
                elif f86 == 4:
                    req['pd_mask'] = out_win
                    req['pd_val'] = ((out_data & 0xFFFF) << out_base) & 0xFFFF
        if op == 0:
            if f86 == 2:
                new['x'] = (self.x - 1) & M32
            if f86 == 4:
                new['y'] = (self.y - 1) & M32
            if jtake:
                new['pc'] = a & 63
        elif op == 1:
            if wsrc == 2 and bit(a, 8):
                req['ev_clr'] |= 1 << (a & 3)
        elif op == 4:
            if pp_pull:
                if not pull_skip:
                    if self.tx.empty:
                        new['osr'] = self.x
                    else:
                        new['osr'] = self.tx.head
                        req['tx_pop'] = True
                    new['osr_cnt'] = 0
            else:
                if not push_skip:
                    if not self.rx.full:
                        req['rx_push'] = isr_shifted if push_align else self.isr
                    new['isr'], new['isr_cnt'] = 0, 0
        elif op == 5:
            d = a >> 6
            if d == 0:
                req['pv_mask'] = out_win
                req['pv_val'] = ((mov_val & 0xFFFF) << out_base) & 0xFFFF
            elif d == 1:
                new['x'] = mov_val
            elif d == 2:
                new['y'] = mov_val
            elif d == 3:
                req['pd_mask'] = out_win
                req['pd_val'] = ((mov_val & 0xFFFF) << out_base) & 0xFFFF
            elif d == 5:
                new['pc'] = mov_val & 63
            elif d == 6:
                new['isr'], new['isr_cnt'] = mov_val, 0
            elif d == 7:
                new['osr'], new['osr_cnt'] = mov_val, 0
        elif op == 6:
            d5 = a & 31
            if f86 == 0:
                req['pv_mask'] = set_win
                req['pv_val'] = (d5 << set_base) & 0xFFFF
                new['last_out'] = d5 & 1
            elif f86 == 1:
                new['x'] = d5
            elif f86 == 2:
                new['y'] = d5
            elif f86 == 3:
                req['pd_mask'] = set_win
                req['pd_val'] = (d5 << set_base) & 0xFFFF
            elif f86 == 4:
                new['crc'] = crc_mask if d5 else 0
        elif op == 7:
            if f86 == 1:
                new['tap_out'], new['tap_in'] = a & 1, (a >> 1) & 1
            elif f86 == 2:
                new['stuff_out_en'], new['stuff_in_en'] = a & 1, (a >> 1) & 1
                new['tap_out'], new['tap_in'] = a & 1, (a >> 1) & 1
                new['run'], new['last_bit'], new['crc'] = 0, 0, 0
            elif f86 == 3:
                req['ev_set'] |= 1 << (a & 3)
            elif f86 == 4:
                req['ev_clr'] |= 1 << (a & 3)
            elif f86 == 5:                        # BAUD: CFG3 = X, bit timer restarts
                req['bt_set'] = self.x
                new['bt_cnt'] = 0
        return req, new


class Core:
    def __init__(self):
        self.eng = [Engine() for _ in range(N_ENG)]
        self.imem = [0] * 64
        self.ctrl_en = 0
        self.imem_ptr = 0
        self.pin_od = 0
        self.events = 0
        self.time = 0
        self.pin_val = 0x3FFF           # outputs idle high, open-drain pins released
        self.pin_dir = 0
        self.sync1 = self.sync2 = 0
        self.rs_prev = [0] * 4

    # ---- outputs (state after the last step) ----
    @property
    def uo_gp(self):
        return (self.pin_val >> 8) & 0x3F

    @property
    def uio_out(self):
        return self.pin_val & ~self.pin_od & 0xFF

    @property
    def uio_oe(self):
        od = self.pin_od & 0xFF
        return ((~self.pin_val & od) | (self.pin_dir & ~od)) & 0xFF

    @property
    def irq(self):
        return (self.events >> 3) & 1

    def status(self):
        s = 0
        for e, g in enumerate(self.eng):
            s |= (int(g.tx.full) << 3 | int(g.tx.empty) << 2 | int(g.rx.full) << 1 | int(g.rx.empty)) << (4 * e)
        return s

    def read(self, addr):
        if addr < 0x10:
            if addr == 0x0:
                return ID
            if addr == 0x1:
                return self.ctrl_en
            if addr == 0x2:
                return self.imem_ptr
            if addr == 0x4:
                return self.status()
            if addr == 0x5:
                return self.time
            if addr == 0x6:
                return self.pin_od
            if addr == 0x7:
                return self.events
            if addr == 0x8:
                return (self.pin_val << 16) | self.sync2
            if addr == 0x9:
                return sum(g.pc << (6 * e) for e, g in enumerate(self.eng))
            return 0
        if addr < 0x10 + 8 * N_ENG:
            e, k = (addr - 0x10) >> 3, (addr - 0x10) & 7
            return self.eng[e].rx.head if k == 7 else 0     # configuration is write-only
        return 0

    def step(self, ui_in=0, uio_in=0, we=False, re=False, addr=0, wdata=0, rst=False):
        """One clock. Returns the bus read value seen during this clock."""
        rdata = self.read(addr)
        raw = ((ui_in >> 3) & 0x1F) << 8 | (uio_in & 0xFF)
        pins_in = self.sync2
        if rst:
            old1, old2 = self.sync1, self.sync2
            imem = self.imem
            self.__init__()
            self.imem = imem                      # program memory is not reset
            self.rs_prev = [bit(old2, 0)] * 4     # resync pin is 0 after reset
            self.sync2, self.sync1 = old1, raw
            return rdata

        g_sel = addr < 0x10
        eng_sel = 0x10 <= addr < 0x10 + 8 * N_ENG
        esel, ereg = ((addr - 0x10) >> 3) & 3, (addr - 0x10) & 7
        restart = ((wdata >> 4) & ((1 << N_ENG) - 1)) if (we and g_sel and addr == 1) else 0

        results = []
        for e, g in enumerate(self.eng):
            en = bool(self.ctrl_en >> e & 1)
            results.append(g.step(en, pins_in, self.time, self.events, self.imem, self.rs_prev[e]))

        # pins: engine 3 wins
        pv, pd = self.pin_val, self.pin_dir
        ev_set = ev_clr = 0
        for req, _ in results:
            pv = (pv & ~req['pv_mask']) | (req['pv_val'] & req['pv_mask'])
            pd = (pd & ~req['pd_mask']) | (req['pd_val'] & req['pd_mask'])
            ev_set |= req['ev_set']
            ev_clr |= req['ev_clr']

        # engine state, FIFOs (host and engine sides)
        for e, (g, (req, new)) in enumerate(zip(self.eng, results)):
            rs_pin = (g.cfg[2] >> 15) & 15
            self.rs_prev[e] = bit(pins_in, rs_pin)
            tx_push = we and eng_sel and esel == e and ereg == 6
            rx_pop = re and eng_sel and esel == e and ereg == 7
            if req['bt_set'] is not None:
                g.cfg[3] = req['bt_set'] & M32   # a host write later in this clock wins
            if restart >> e & 1:
                g.reset()
                continue
            # TX FIFO: host pushes, engine pops
            do_pop = req['tx_pop'] and not g.tx.empty
            do_push = tx_push and (not g.tx.full or do_pop)
            if do_pop:
                g.tx.q.pop(0)
            if do_push:
                g.tx.q.append(wdata & M32)
            # RX FIFO: engine pushes, host pops
            do_pop = rx_pop and not g.rx.empty
            do_push = req['rx_push'] is not None and (not g.rx.full or do_pop)
            if do_pop:
                g.rx.q.pop(0)
            if do_push:
                g.rx.q.append(req['rx_push'] & M32)
            for k, v in new.items():
                setattr(g, k, v & M32 if isinstance(v, int) else v)

        self.pin_val = pv & 0x3FFF
        self.pin_dir = pd & 0xFF
        self.events = (self.events & ~ev_clr | ev_set) & 0xF
        self.time = (self.time + 1) & M32

        if we and g_sel:
            if addr == 0x1:
                self.ctrl_en = wdata & ((1 << N_ENG) - 1)
            elif addr == 0x2:
                self.imem_ptr = wdata & 63
            elif addr == 0x3:
                self.imem[self.imem_ptr] = wdata & 0xFFFF
                self.imem_ptr = (self.imem_ptr + 1) & 63
            elif addr == 0x6:
                self.pin_od = wdata & 0x3FFF
            elif addr == 0x7:
                self.events = wdata & 0xF
        if we and eng_sel:
            g = self.eng[esel]
            if ereg == 0:
                g.cfg[0] = wdata & M32
            elif ereg == 1:
                g.cfg[1] = wdata & CFG1_MASK
            elif ereg == 2:
                g.cfg[2] = wdata & M32
            elif ereg == 3:
                g.cfg[3] = wdata & M32
            elif ereg == 4:
                g.poly = wdata & M32
            elif ereg == 5:
                g.crcw = wdata & 31

        self.sync2, self.sync1 = self.sync1, raw
        return rdata
