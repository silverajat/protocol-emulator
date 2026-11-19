"""A virtual LED board: the golden model wired like fpga/ledboard_top.v, behind the same
link interface as the real board (`spi(bytes) -> bytes`). `pe.py --sim` uses it, so every
demo can run with no hardware, and the laptop tool is tested against the model.

Wiring (as on the board): uo_out[5:2] -> ui_in[6:3], J3 jumper between uio6 and uio7
(wired-AND with pull-ups), uio pull-ups everywhere else.
"""

from .model import Core


class ModelBoard:
    def __init__(self, cycles_per_transfer=2000, jumper_67=True):
        self.core = Core()
        self.gap = cycles_per_transfer      # the USB/UART bridge is slow: chip clocks per transfer
        self.jumper = jumper_67
        self.ui = 0
        self.uio = 0xFF
        self.core.step(rst=True)
        self.core.step(rst=True)

    def _pins(self):
        c = self.core
        uo = (c.uo_gp << 2) | (c.irq << 1)
        ui = ((uo >> 2) & 0xF) << 3          # loopback wires
        drive_low = c.uio_oe & ~c.uio_out & 0xFF
        uio = 0xFF & ~drive_low
        uio |= c.uio_oe & c.uio_out
        if self.jumper:
            bus = (uio >> 6) & (uio >> 7) & 1
            uio = (uio & 0x3F) | (bus << 6) | (bus << 7)
        return ui, uio

    def run(self, n, **bus):
        rd = 0
        for i in range(n):
            ui, uio = self._pins()
            rd = self.core.step(ui, uio, **(bus if i == 0 else {}))
        return rd

    def spi(self, data):
        cmd, payload = data[0], data[1:]
        addr, rd = cmd & 0x7F, bool(cmd & 0x80)
        out = bytearray([0])
        self.run(self.gap)
        for i in range(0, len(payload), 4):
            if rd:
                v = self.run(1, addr=addr)
                self.run(1, addr=addr, re=True)           # the word was clocked out: pop
                out += v.to_bytes(4, 'big')
            else:
                w = int.from_bytes(payload[i:i + 4], 'big')
                self.run(1, addr=addr, we=True, wdata=w)
                out += bytes(4)
            self.run(256)                                  # 32 SPI bits at clock / 8
        return bytes(out)

    def reset_chip(self):
        self.core.step(rst=True)

    def irq(self):
        return self.core.irq
