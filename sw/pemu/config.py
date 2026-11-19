"""Engine configuration and register-level helpers (docs/ISA.md, Registers)."""

from dataclasses import dataclass

# global registers
ID, CTRL, IMEM_ADDR, IMEM_DATA, STATUS, TIME, PIN_OD, EVENTS, PINS, PCS = range(10)
CHIP_ID = 0x50454D55


def ereg(engine, k):
    """Address of engine register k (0 CFG0 .. 5 CRC_CFG, 6 TXF, 7 RXF)."""
    return 0x10 + 8 * engine + k


def TXF(e):
    return ereg(e, 6)


def RXF(e):
    return ereg(e, 7)


# pin space helpers
def uio(i):
    return i            # output and input space 0-7


def uo(i):
    """uo_out[i] (i = 2..7) in output space."""
    assert 2 <= i <= 7
    return 6 + i


def ui(i):
    """ui_in[i] (i = 3..7) in input space."""
    assert 3 <= i <= 7
    return 5 + i


@dataclass
class EngineConfig:
    clkdiv: int = 0
    wrap_bot: int = 0
    wrap_top: int = 63
    out_base: int = 0
    out_cnt: int = 0
    set_base: int = 0
    set_cnt: int = 0
    in_base: int = 0
    jmp_pin: int = 0
    jmp_src_last_in: bool = False
    out_shr: bool = False
    in_shr: bool = False
    autopull: bool = False
    autopush: bool = False
    pull_thresh: int = 0       # 0 = 32
    push_thresh: int = 0       # 0 = 32
    stuff_n: int = 0
    stuff_ones: bool = False
    resync_pin: int = 0
    resync_edge: int = 0       # 0 off, 1 falling, 2 rising, 3 both
    start: int = 0             # PC after reset / restart
    bt_period: int = 0
    bt_point: int = 0
    crc_poly: int = 0
    crc_width: int = 1

    def for_program(self, prog):
        self.wrap_bot, self.wrap_top = prog.wrap_bot, prog.wrap_top
        self.start = prog.origin
        return self

    def regs(self):
        cfg0 = (self.clkdiv & 0xFFFF) | (self.wrap_bot & 63) << 16 | (self.wrap_top & 63) << 22
        cfg1 = ((self.out_base & 15) | (self.out_cnt & 15) << 4 | (self.set_base & 15) << 8 |
                (self.set_cnt & 15) << 12 | (self.in_base & 15) << 16 | (self.jmp_pin & 15) << 20 |
                int(self.jmp_src_last_in) << 24 | int(self.out_shr) << 25 | int(self.in_shr) << 26 |
                int(self.autopull) << 27 | int(self.autopush) << 28)
        cfg2 = ((self.pull_thresh & 31) | (self.push_thresh & 31) << 5 | (self.stuff_n & 15) << 10 |
                int(self.stuff_ones) << 14 | (self.resync_pin & 15) << 15 | (self.resync_edge & 3) << 19 | (self.start & 63) << 21)
        cfg3 = (self.bt_period & 0xFFFF) | (self.bt_point & 0xFFFF) << 16
        return [cfg0, cfg1, cfg2, cfg3, self.crc_poly & 0xFFFFFFFF, (self.crc_width - 1) & 31]


def load_writes(engine, cfg, prog=None):
    """Register writes (addr, [words]) that load prog (if given) and configure engine."""
    w = []
    if prog is not None:
        w.append((IMEM_ADDR, [prog.origin]))
        w.append((IMEM_DATA, list(prog.words)))
        cfg.for_program(prog)
    for k, v in enumerate(cfg.regs()):
        w.append((ereg(engine, k), [v]))
    return w
