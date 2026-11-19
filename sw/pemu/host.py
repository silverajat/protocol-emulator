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
    """LED board link: ESP32 USB serial, raw bytes passed through to the FPGA's host_bridge.
    Messages from the bridge are (type, length, payload); type 0xA8 carries stream words."""

    def __init__(self, port, baud=1_000_000, timeout=1.0):
        import serial  # pyserial
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.stream = []          # (engine, word) received in stream mode

    def _read(self, n):
        b = self.ser.read(n)
        if len(b) != n:
            raise TimeoutError('no reply from the board')
        return b

    def _message(self):
        while True:
            t = self._read(1)[0]
            if t >= 0xA5:          # anything below is boot text from the ESP32: skip it
                break
        n = self._read(1)[0]
        return t, self._read(n)

    def _expect(self, kind):
        while True:
            t, payload = self._message()
            if t == 0xA8:
                self.stream.append((payload[0], int.from_bytes(payload[1:5], 'big')))
            elif t == kind:
                return payload

    def spi(self, data):
        assert 0 < len(data) < 256
        self.ser.write(bytes([0x5A, len(data)]) + data)
        got = self._expect(0xA5)
        if len(got) != len(data):
            raise IOError('bad reply length from the board')
        return got

    def reset_chip(self):
        self.ser.write(b'\x5B')
        self._expect(0xA7)

    def irq(self):
        self.ser.write(b'\x5C')
        return self._expect(0xA6)[0]

    def stream_on(self, mask):
        """Let the bridge pop the RX FIFOs of the engines in mask by itself (0 = stop)."""
        self.ser.write(bytes([0x5D, mask & 7]))

    def poll_stream(self, timeout=0.05):
        """Collect stream words that have arrived (waits up to timeout for the first)."""
        old = self.ser.timeout
        self.ser.timeout = timeout
        try:
            while self.ser.in_waiting or not self.stream:
                try:
                    t, payload = self._message()
                except TimeoutError:
                    break
                if t == 0xA8:
                    self.stream.append((payload[0], int.from_bytes(payload[1:5], 'big')))
        finally:
            self.ser.timeout = old
        out, self.stream = self.stream, []
        return out
