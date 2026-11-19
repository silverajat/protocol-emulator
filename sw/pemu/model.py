"""Cycle-accurate golden model of the protocol emulator core (docs/ISA.md).

Core.step(ui_in, uio_in, bus) advances one clock and returns the read data seen during
that clock. Outputs (uo_gp, uio_out, uio_oe, irq) are properties of the state after the step.
The RTL is checked against this model cycle by cycle (test/test_diff.py).
"""

M32 = 0xFFFFFFFF
ID = 0x50454D55
CFG0_MASK, CFG1_MASK, CFG2_MASK = 0x0FFFFFFF, 0x1FFFFFFF, 0x07FFFFFF


OPS = ['jmp', 'wait', 'in', 'out', 'pushpull', 'mov', 'set', 'ctl']
# execution counters (coverage for the random tests)
STATS = dict.fromkeys(OPS + ['paced', 'stuff_out', 'stuff_in', 'autopull', 'autopush', 'crc_tap'], 0)


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
        self.reset()

    def reset(self):
        self.pc = (self.cfg[2] >> 21) & 63      # START
        self.x = self.y = self.osr = self.isr = 0
        self.osr_cnt, self.isr_cnt = 32, 0
        self.dly = self.div = self.bt_cnt = 0
        self.bt_pend = 0
        self.crc = 0
        self.tap_out = self.tap_in = self.stuff_en = 0
        self.last_bit = self.last_in = 0
        self.run = 0
        self.tx.q.clear()
        self.rx.q.clear()

    # config fields
    def f(self, reg, lo, width):
        return (self.cfg[reg] >> lo) & ((1 << width) - 1)

    def step(self, en, pins_in, time, events, imem, rs_prev):
        """Evaluate one clock. Returns a dict of requests; state updates are applied by commit()."""
        f = self.f
        clkdiv, wrap_bot, wrap_top = f(0, 0, 16), f(0, 16, 6), f(0, 22, 6)
        out_base, out_cnt, set_base, set_cnt = f(1, 0, 4), f(1, 4, 4), f(1, 8, 4), f(1, 12, 4)
        in_base, jmp_pin, jmp_src = f(1, 16, 4), f(1, 20, 4), f(1, 24, 1)
        out_shr, in_shr, autopull, autopush = f(1, 25, 1), f(1, 26, 1), f(1, 27, 1), f(1, 28, 1)
        pull_th = f(2, 0, 5) or 32
        push_th = f(2, 5, 5) or 32
        stuff_n, stuff_ones = f(2, 10, 4), f(2, 14, 1)
        rs_pin, rs_edge = f(2, 15, 4), f(2, 19, 2)
        bt_period, bt_point = f(3, 0, 16), f(3, 16, 16)
        wm1 = self.crcw
        crc_mask = M32 >> (31 - wm1)

        req = dict(pv_mask=0, pv_val=0, pd_mask=0, pd_val=0, ev_set=0, ev_clr=0,
                   tx_pop=False, rx_push=None)
        new = {}

        rs_cur = bit(pins_in, rs_pin)
        rs_hit = ((rs_edge & 1) and rs_prev and not rs_cur) or ((rs_edge & 2) and not rs_prev and rs_cur)
        bt_on = bt_period != 0
        bt_eff = 0 if rs_hit else self.bt_cnt
        bt_tick = bt_on and bt_eff == bt_point
        pace_ok = (self.bt_pend and not rs_hit) or bt_tick

        instr = imem[self.pc]
        op, dl, pace, a = instr >> 15, (instr >> 10) & 31, (instr >> 9) & 1, instr & 0x1FF
        f86 = a >> 6
        n = (a & 31) or 32
        nsh = 32 - n

        rot = ((pins_in >> in_base) | (pins_in << (16 - in_base))) & 0xFFFF
        crc_la = (self.crc << (31 - wm1)) & M32

        def src(s):
            return [rot, self.x, self.y, 0, time, crc_la, self.isr, self.osr][s]

        stuff_active = self.stuff_en and stuff_n != 0
        stuff_due = stuff_active and self.run == stuff_n
        stuff_bit = 0 if stuff_ones else 1 - self.last_bit

        is_out = op == 3
        out_pin1 = is_out and f86 == 0 and n == 1
        do_stuff_out = out_pin1 and stuff_due
        osr_empty = self.osr_cnt >= pull_th
        need_pull = is_out and autopull and osr_empty and not do_stuff_out
        osr_src = self.tx.head if need_pull else self.osr
        ocnt_base = 0 if need_pull else self.osr_cnt
        if out_shr:
            out_data = osr_src & lowmask(n)
            osr_after = (osr_src >> n) if n < 32 else 0
        else:
            out_data = osr_src >> nsh
            osr_after = ((osr_src << n) & M32) if n < 32 else 0
        ocnt_after = min(32, ocnt_base + n)

        is_in = op == 2
        in_pin1 = is_in and f86 == 0 and n == 1
        do_stuff_in = in_pin1 and stuff_due
        in_data = src(f86)
        if in_shr:
            isr_shifted = ((self.isr >> n) if n < 32 else 0) | ((in_data << nsh) & M32)
        else:
            isr_shifted = (((self.isr << n) & M32) if n < 32 else 0) | (in_data & lowmask(n))
        icnt_after = min(32, self.isr_cnt + n)
        autopush_hit = is_in and autopush and not do_stuff_in and icnt_after >= push_th

        is_pp = op == 4
        pp_pull, pp_block, pp_if = bit(a, 8), bit(a, 7), bit(a, 6)
        push_skip = pp_if and self.isr_cnt < push_th
        pull_skip = pp_if and not osr_empty

        mov_raw = src(a & 7)
        mop = (a >> 3) & 3
        mov_val = (~mov_raw & M32) if mop == 1 else bitrev32(mov_raw) if mop == 2 else mov_raw

        wsrc = (a >> 6) & 3
        if wsrc == 0:
            wait_val = bit(pins_in, a & 15)
        elif wsrc == 1:
            wait_val = bit(pins_in, (in_base + (a & 15)) & 15)
        elif wsrc == 2:
            wait_val = bit(events, a & 3)
        else:
            wait_val = 0

        jpin = self.last_in if jmp_src else bit(pins_in, jmp_pin)
        jtake = [True, self.x == 0, self.x != 0, self.y == 0, self.y != 0, self.x != self.y,
                 bool(jpin), not osr_empty][f86]

        tick = en and self.div == 0
        active = tick and self.dly == 0
        if op == 1:
            op_stall = wait_val != bit(a, 8)
        elif op == 2:
            op_stall = autopush_hit and self.rx.full
        elif op == 3:
            op_stall = need_pull and self.tx.empty
        elif op == 4:
            op_stall = (not pull_skip and self.tx.empty and pp_block) if pp_pull else \
                       (not push_skip and self.rx.full and pp_block)
        else:
            op_stall = False
        execute = active and not op_stall and not (pace and not pace_ok)

        if not en:
            return req, new

        # always-running parts (when enabled)
        new['div'] = clkdiv if self.div == 0 else self.div - 1
        if not bt_on:
            new['bt_cnt'] = 0
        else:
            new['bt_cnt'] = 0 if bt_eff == bt_period - 1 else bt_eff + 1
        if execute and pace:
            new['bt_pend'] = 0
        elif rs_hit:
            new['bt_pend'] = int(bt_tick)
        else:
            new['bt_pend'] = int(self.bt_pend or bt_tick)
        if tick and self.dly != 0:
            new['dly'] = self.dly - 1

        if not execute:
            return req, new

        st = STATS
        st[OPS[op]] += 1
        if pace:
            st['paced'] += 1
        if do_stuff_out:
            st['stuff_out'] += 1
        if do_stuff_in:
            st['stuff_in'] += 1
        if need_pull:
            st['autopull'] += 1
        if autopush_hit:
            st['autopush'] += 1
        if (out_pin1 and self.tap_out and not do_stuff_out) or (in_pin1 and self.tap_in and not do_stuff_in):
            st['crc_tap'] += 1
        new['dly'] = dl
        pc_seq = wrap_bot if self.pc == wrap_top else (self.pc + 1) & 63
        new['pc'] = pc_seq
        out_win = window(out_base, out_cnt)
        set_win = window(set_base, set_cnt)

        def crc_feed(b):
            fb = b ^ bit(self.crc, wm1)
            new['crc'] = ((self.crc << 1) & crc_mask) ^ ((self.poly & crc_mask) if fb else 0)

        def run_update(b):
            if stuff_ones:
                new['run'] = min(15, self.run + 1) if b else 0
            else:
                new['run'] = min(15, self.run + 1) if (b == self.last_bit and self.run != 0) else 1
            new['last_bit'] = b

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
        elif op == 2:
            if do_stuff_in:
                new['pc'] = self.pc
                new['last_bit'] = rot & 1
                new['run'] = (rot & 1) if stuff_ones else 1
            else:
                new['last_in'] = in_data & 1
                if autopush_hit:
                    new['isr'], new['isr_cnt'] = 0, 0
                    req['rx_push'] = isr_shifted
                else:
                    new['isr'], new['isr_cnt'] = isr_shifted, icnt_after
                if in_pin1 and stuff_active:
                    run_update(rot & 1)
                if in_pin1 and self.tap_in:
                    crc_feed(rot & 1)
        elif op == 3:
            if do_stuff_out:
                new['pc'] = self.pc
                new['last_bit'] = stuff_bit
                new['run'] = 0 if stuff_ones else 1
                req['pv_mask'] = out_win
                req['pv_val'] = (stuff_bit << out_base) & 0xFFFF
            else:
                if need_pull:
                    req['tx_pop'] = True
                new['osr'], new['osr_cnt'] = osr_after, ocnt_after
                if out_pin1 and stuff_active:
                    run_update(out_data & 1)
                if out_pin1 and self.tap_out:
                    crc_feed(out_data & 1)
                if f86 == 0:
                    req['pv_mask'] = out_win
                    req['pv_val'] = ((out_data & 0xFFFF) << out_base) & 0xFFFF
                elif f86 == 1:
                    new['x'] = out_data
                elif f86 == 2:
                    new['y'] = out_data
                elif f86 == 4:
                    req['pd_mask'] = out_win
                    req['pd_val'] = ((out_data & 0xFFFF) << out_base) & 0xFFFF
                elif f86 == 5:
                    new['pc'] = out_data & 63
                elif f86 == 6:
                    new['isr'], new['isr_cnt'] = out_data, n
                elif f86 == 7:
                    crc_feed(out_data & 1)
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
                        req['rx_push'] = self.isr
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
                new['stuff_en'], new['run'], new['last_bit'] = a & 1, 0, 0
            elif f86 == 3:
                req['ev_set'] |= 1 << (a & 3)
            elif f86 == 4:
                req['ev_clr'] |= 1 << (a & 3)
        return req, new


class Core:
    def __init__(self):
        self.eng = [Engine() for _ in range(4)]
        self.imem = [0] * 64
        self.ctrl_en = 0
        self.imem_ptr = 0
        self.pin_od = 0
        self.events = 0
        self.time = 0
        self.pin_val = 0
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
            if addr == 0x3:
                return self.imem[self.imem_ptr]
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
        if addr < 0x30:
            e, k = (addr - 0x10) >> 3, (addr - 0x10) & 7
            g = self.eng[e]
            if k < 4:
                return g.cfg[k]
            if k == 4:
                return g.poly
            if k == 5:
                return g.crcw
            if k == 6:
                return 0
            return g.rx.head
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
        eng_sel = 0x10 <= addr < 0x30
        esel, ereg = ((addr - 0x10) >> 3) & 3, (addr - 0x10) & 7
        restart = ((wdata >> 4) & 0xF) if (we and g_sel and addr == 1) else 0

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
                self.ctrl_en = wdata & 0xF
            elif addr == 0x2:
                self.imem_ptr = wdata & 63
            elif addr == 0x3:
                self.imem[self.imem_ptr] = wdata & 0x3FFFF
                self.imem_ptr = (self.imem_ptr + 1) & 63
            elif addr == 0x6:
                self.pin_od = wdata & 0x3FFF
            elif addr == 0x7:
                self.events = wdata & 0xF
        if we and eng_sel:
            g = self.eng[esel]
            if ereg == 0:
                g.cfg[0] = wdata & CFG0_MASK
            elif ereg == 1:
                g.cfg[1] = wdata & CFG1_MASK
            elif ereg == 2:
                g.cfg[2] = wdata & CFG2_MASK
            elif ereg == 3:
                g.cfg[3] = wdata & M32
            elif ereg == 4:
                g.poly = wdata & M32
            elif ereg == 5:
                g.crcw = wdata & 31

        self.sync2, self.sync1 = self.sync1, raw
        return rdata
