"""CAN 2.0 frames for sw/programs/can.pio: building TX words, decoding RX words, and the
bit-level helpers (CRC-15, stuffing) shared with the testbench's bus model."""

from dataclasses import dataclass

CRC15_POLY = 0x4599


def bits_of(v, n):
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def crc15(bits):
    crc = 0
    for b in bits:
        fb = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if fb:
            crc ^= CRC15_POLY
    return crc


def stuff(bits, n=5):
    out, run, last = [], 0, None
    for b in bits:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == n:
            out.append(1 - b)
            last, run = 1 - b, 1
    return out


@dataclass
class Frame:
    ident: int
    data: bytes = b''
    ext: bool = False
    rtr: bool = False
    dlc: int = None
    crc_ok: bool = True
    time: int = None          # chip TIME at the start of frame (received frames)

    def __post_init__(self):
        if self.dlc is None:
            self.dlc = len(self.data)

    def head_bits(self):
        """SOF through the data field (what the chip shifts out before the CRC)."""
        b = [0]
        if self.ext:
            b += bits_of(self.ident >> 18, 11) + [1, 1] + bits_of(self.ident & 0x3FFFF, 18) + [int(self.rtr), 0, 0]
        else:
            b += bits_of(self.ident, 11) + [int(self.rtr), 0, 0]
        b += bits_of(self.dlc, 4)
        for d in self.data:
            b += bits_of(d, 8)
        return b

    def bits(self):
        """SOF..CRC, unstuffed."""
        h = self.head_bits()
        return h + bits_of(crc15(h), 15)

    def wire(self):
        """Everything a transmitter puts on the bus, ACK slot recessive, EOF included."""
        return stuff(self.bits()) + [1, 1, 1] + [1] * 7


def tx_words(frame):
    """Words to push into the CAN engine's TX FIFO for one frame."""
    h = frame.head_bits()
    words = [len(h) - 1]
    for i in range(0, len(h), 32):
        chunk = h[i:i + 32]
        words.append(sum(b << k for k, b in enumerate(chunk)))
    return words


def rx_word_count(nbits):
    """RX words for a frame of nbits (SOF..CRC). The program's final PUSH ALIGN always adds a
    word, which is empty when nbits is a multiple of 32."""
    return nbits // 32 + 1


def _stream(words):
    s = []
    for w in words:
        s += [(w >> k) & 1 for k in range(32)]
    return s


def frame_length(stream):
    """Total bits SOF..CRC, or None if the header is not complete yet."""
    if len(stream) < 14:
        return None
    hdr = 39 if stream[13] else 19
    if len(stream) < hdr:
        return None
    dlc = int(''.join(map(str, stream[hdr - 4:hdr])), 2)
    return hdr + 8 * min(dlc, 8) + 15


def rx_need(words):
    """How many RX words this frame takes (timestamp word included), given its first words;
    None = read another word first."""
    n = frame_length(_stream(words[1:]))
    return None if n is None else 1 + rx_word_count(n)


def decode(words):
    """Decode the RX words of one frame (rx_need() of them). Returns the Frame, with .time set
    to the chip's TIME at the start of frame."""
    s = _stream(words[1:])
    f = parse(s[:frame_length(s)])
    f.time = words[0]
    return f


def parse(b):
    def num(bits):
        return int(''.join(map(str, bits)), 2) if bits else 0
    ide = b[13]
    if ide:
        ident = (num(b[1:12]) << 18) | num(b[14:32])
        rtr, p = b[32], 35
    else:
        ident, rtr, p = num(b[1:12]), b[12], 15
    dlc = num(b[p:p + 4])
    p += 4
    data = bytes(num(b[p + 8 * i:p + 8 * i + 8]) for i in range(min(dlc, 8)))
    p += 8 * min(dlc, 8)
    crc = num(b[p:p + 15])
    ok = crc == crc15(b[:p])
    return Frame(ident, data, bool(ide), bool(rtr), dlc, ok)
