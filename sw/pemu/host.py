"""Host-side access to the chip's register bus.

The chip's host port is SPI: one command byte {read, addr[6:0]}, then 4-byte big-endian words.
`Host` builds those transactions on top of any link with a `spi(bytes) -> bytes` method:

- `BridgeLink`: the LED board (laptop USB -> ESP32 -> FPGA host_bridge -> SPI), 1 Mbaud frames
  0x5A n bytes... -> 0xA5 n miso...
- the cocotb tests drive the SPI pins directly and share the framing helpers below.
"""

from . import config as R

MAX_WORDS = 63          # one bridge frame carries at most 255 bytes


def write_bytes(addr, words):
    return bytes([addr & 0x7F]) + b''.join((w & 0xFFFFFFFF).to_bytes(4, 'big') for w in words)


def read_bytes(addr, n):
    return bytes([0x80 | (addr & 0x7F)]) + bytes(4 * n)


def decode_words(miso, n):
    d = miso[1:]
    return [int.from_bytes(d[4 * i:4 * i + 4], 'big') for i in range(n)]


class Host:
    def __init__(self, link):
        self.link = link

    def write(self, addr, words):
        if isinstance(words, int):
            words = [words]
        words = list(words)
        for i in range(0, len(words), MAX_WORDS):
            self.link.spi(write_bytes(addr, words[i:i + MAX_WORDS]))

    def read(self, addr, n=1):
        out = []
        while len(out) < n:
            k = min(MAX_WORDS, n - len(out))
            out += decode_words(self.link.spi(read_bytes(addr, k)), k)
        return out if n > 1 else out[0]

    def load(self, engine, cfg, program=None):
        for addr, words in R.load_writes(engine, cfg, program):
            self.write(addr, words)

    def status(self, engine):
        s = (self.read(R.STATUS) >> (4 * engine)) & 0xF
        return dict(rx_empty=bool(s & 1), rx_full=bool(s & 2), tx_empty=bool(s & 4), tx_full=bool(s & 8))

    def send(self, engine, values):
        """Push values into an engine's TX FIFO, waiting while it is full."""
        for v in values:
            while self.status(engine)['tx_full']:
                pass
            self.write(R.TXF(engine), v)

    def recv(self, engine, max_n=None):
        """Pop whatever is in an engine's RX FIFO (up to max_n values)."""
        out = []
        while max_n is None or len(out) < max_n:
            if self.status(engine)['rx_empty']:
                break
            out.append(self.read(R.RXF(engine)))
        return out


class BridgeLink:
    """LED board link: ESP32 USB serial, raw bytes passed through to the FPGA's host_bridge."""

    def __init__(self, port, baud=1_000_000, timeout=1.0):
        import serial  # pyserial
        self.ser = serial.Serial(port, baud, timeout=timeout)

    def _expect(self, marker):
        while True:
            b = self.ser.read(1)
            if not b:
                raise TimeoutError(f'no reply from the board (waiting for 0x{marker:02x})')
            if b[0] == marker:
                return
            # anything else is boot text from the ESP32; skip it

    def spi(self, data):
        assert 0 < len(data) < 256
        self.ser.write(bytes([0x5A, len(data)]) + data)
        self._expect(0xA5)
        n = self.ser.read(1)
        if not n or n[0] != len(data):
            raise IOError('bad reply length from the board')
        got = self.ser.read(len(data))
        if len(got) != len(data):
            raise TimeoutError('short reply from the board')
        return got

    def reset_chip(self):
        self.ser.write(b'\x5B')
        self._expect(0xA7)

    def irq(self):
        self.ser.write(b'\x5C')
        self._expect(0xA6)
        return self.ser.read(1)[0]
