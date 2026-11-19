"""Host side of sw/programs/swd.pio: Serial Wire Debug requests for an ARM debug port."""

ACK_OK, ACK_WAIT, ACK_FAULT = 1, 2, 4

# ≥50 ones, the 16-bit JTAG-to-SWD select sequence (0xE79E, LSB first), ≥50 ones, idle
JTAG_TO_SWD = [0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFE79E, 0xFFFFFFFF, 0xFFFFFFFF, 0x00000000]
IDLE = [0x00000000]

# DP registers (address bits A[3:2])
DP_IDCODE, DP_CTRLSTAT, DP_SELECT, DP_RDBUFF = 0x0, 0x4, 0x8, 0xC


def parity(v):
    return bin(v).count('1') & 1


def request(ap, read, addr):
    """The 8-bit request: start, APnDP, RnW, A2, A3, parity, stop, park (sent LSB first)."""
    a2, a3 = addr >> 2 & 1, addr >> 3 & 1
    p = (ap + read + a2 + a3) & 1
    return 1 | ap << 1 | read << 2 | a2 << 3 | a3 << 4 | p << 5 | 0 << 6 | 1 << 7


class Swd:
    """Drives the program through any `step(label, data, extra=None)` callable that writes the
    step word (plus an optional following word) and returns the words the engine pushes back."""

    def __init__(self, labels, xfer):
        self.labels, self.xfer = labels, xfer

    async def seq(self, words):
        for w in words:
            await self.xfer([self.labels['seq'], w], 0)

    async def _req(self, ap, read, addr):
        (r,) = await self.xfer([self.labels['req'] | request(ap, read, addr) << 6], 1)
        return r >> 1 & 7

    async def read(self, ap, addr):
        ack = await self._req(ap, 1, addr)
        if ack != ACK_OK:
            return ack, None
        data, p = await self.xfer([self.labels['rdata']], 2)
        assert p & 1 == parity(data), 'SWD read parity error'
        await self.seq(IDLE)
        return ack, data

    async def write(self, ap, addr, data):
        ack = await self._req(ap, 0, addr)
        if ack == ACK_OK:
            await self.xfer([self.labels['wdata'] | parity(data) << 6, data], 0)
        await self.seq(IDLE)
        return ack
