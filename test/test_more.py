# SPDX-FileCopyrightText: © 2026 Rajat Gupta
# SPDX-License-Identifier: Apache-2.0
#
# Protocols the chip was not designed around, written as programs after the hardware was done:
# WS2812 LED strips (pulse-width coded) and 1-Wire (a DS18B20 temperature sensor).

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

from chip import setup, prog, R
from devices import PinBus, Ds18b20, crc8_maxim
from pemu import EngineConfig

WS_PIN, OW_PIN = 6, 7


@cocotb.test()
async def ws2812_strip(dut):
    """Three LED colours come out with WS2812 timing (0: ~0.4 us high, 1: ~0.8 us, 1.25 us per bit)."""
    chip = await setup(dut)
    p = prog('ws2812', 0)
    await chip.load(0, EngineConfig(clkdiv=7, out_base=WS_PIN, out_cnt=1, set_base=WS_PIN, set_cnt=1,
                                    autopull=True, pull_thresh=24), p)
    await chip.write(R.CTRL, 0x11)
    colours = [0x00FF00, 0x123456, 0xA5005A]            # GRB
    highs, periods = [], []

    async def capture():
        prev, t, rise, last_rise = 0, 0, None, None
        while len(highs) < 72:
            await FallingEdge(dut.clk)
            t += 1
            v = (int(dut.uio_out.value) >> WS_PIN) & 1 if int(dut.uio_oe.value) >> WS_PIN & 1 else 1
            if v and not prev:
                if last_rise is not None:
                    periods.append(t - last_rise)
                last_rise = rise = t
            if prev and not v:
                highs.append(t - rise)
            prev = v
    cap = cocotb.start_soon(capture())
    await chip.write(R.TXF(0), [c << 8 for c in colours])
    await ClockCycles(dut.clk, 72 * 80 + 2000)
    assert len(highs) == 72, len(highs)
    bits = [1 if h > 36 else 0 for h in highs]          # 36 clocks = 0.6 us at 60 MHz
    got = [int(''.join(map(str, bits[i:i + 24])), 2) for i in range(0, 72, 24)]
    assert got == colours, [hex(g) for g in got]
    assert set(highs) <= {24, 48}, set(highs)          # 0.4 us and 0.8 us
    assert set(periods[:70]) == {80}, set(periods)       # 1.25 us per bit


@cocotb.test()
async def onewire_ds18b20(dut):
    """1-Wire: reset/presence, SKIP ROM + READ SCRATCHPAD from a DS18B20; temperature and CRC check."""
    chip = await setup(dut)
    bus = PinBus(dut, chip)
    sensor = Ds18b20(bus, OW_PIN, temp_c=23.5)
    p = prog('onewire', 0)
    await chip.load(0, EngineConfig(clkdiv=119, out_base=OW_PIN, out_cnt=1, set_base=OW_PIN, set_cnt=1,
                                    in_base=OW_PIN, out_shr=True, in_shr=True, autopush=True,
                                    push_thresh=8), p)
    await chip.write(R.PIN_OD, 1 << OW_PIN)
    await chip.write(R.CTRL, 0x11)

    async def step(name, byte=0):
        await chip.write(R.TXF(0), p.labels[name] | (byte << 6))
        for _ in range(400):
            if not (await chip.read(R.STATUS)) & 1:
                return await chip.read(R.RXF(0))
            await ClockCycles(dut.clk, 2000)
        raise AssertionError(f'{name}: no reply; pc {(await chip.read(R.PCS)) & 63}, sensor {sensor.log}')

    assert (await step('reset')) & 1 == 0, 'no presence pulse'
    assert (await step('byte', 0xCC)) >> 24 == 0xCC       # what we wrote reads back
    await step('byte', 0xBE)
    pad = [(await step('byte', 0xFF)) >> 24 for _ in range(9)]
    assert sensor.log[:3] == ['reset', 0xCC, 0xBE], sensor.log
    assert crc8_maxim(pad[:8]) == pad[8], f'bad CRC: {pad}'
    temp = ((pad[1] << 8 | pad[0]) ^ 0x8000) - 0x8000
    assert temp / 16 == 23.5


@cocotb.test()
async def manchester_link(dut):
    """Manchester TX on engine 0 -> loopback wire -> RX on engine 1: the receiver has no bit
    timer, it recovers the clock from the mid-bit transitions. Also checks the line coding."""
    chip = await setup(dut)
    chip.loop = True
    tx = prog('manchester_tx', 0)
    rx = prog('manchester_rx', len(tx))
    clkdiv = 3                                            # 16 ticks x 4 clocks = 64 clocks per bit
    await chip.load(0, EngineConfig(clkdiv=clkdiv, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2),
                                    set_cnt=1, out_shr=True), tx)
    await chip.load(1, EngineConfig(clkdiv=clkdiv, in_base=R.ui(3), jmp_pin=R.ui(3), in_shr=True), rx)
    await chip.write(R.CTRL, 0x33)
    edges = []

    async def watch():                                    # times of transitions on the wire
        prev, t = chip.uo(2), 0
        while True:
            await FallingEdge(dut.clk)
            t += 1
            v = chip.uo(2)
            if v != prev:
                edges.append(t)
            prev = v
    w = cocotb.start_soon(watch())
    msg = [0x00, 0xFF, 0x55, 0xA5, 0x3C, 0x01, 0x80, 0x7E]
    got = []
    for i in range(0, len(msg), 4):
        await chip.write(R.TXF(0), msg[i:i + 4])
        for _ in range(4):
            for _ in range(100):
                if not (await chip.read(R.STATUS)) >> 4 & 1:    # engine 1 RX FIFO not empty
                    break
                await ClockCycles(dut.clk, 200)
            got.append(await chip.read(R.RXF(1)) & 0xFF)
    w.kill()
    assert got == msg, [hex(g) for g in got]
    gaps = {b - a for a, b in zip(edges, edges[1:])}
    assert {32, 64} <= gaps, sorted(gaps)                 # half-bit and full-bit spacing only ...
    assert all(g in (32, 64) or g > 64 for g in gaps), sorted(gaps)   # ... (and idle between bytes)


@cocotb.test()
async def ir_remote_nec(dut):
    """IR remote: a NEC frame on a 38 kHz carrier, decoded from the pin by its envelope."""
    from pemu.ir import nec_words, nec_decode
    chip = await setup(dut)
    p = prog('ir_tx', 0)
    clkdiv = 9                                     # carrier = 4 ticks x 10 clocks (fast for sim)
    await chip.load(0, EngineConfig(clkdiv=clkdiv, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2),
                                    set_cnt=1, out_shr=True, autopull=True, pull_thresh=0), p)
    await chip.write(R.CTRL, 0x11)
    period = 4 * (clkdiv + 1)
    rises, highs = [], set()

    async def watch():
        prev, t, r = 0, 0, 0
        while True:
            await FallingEdge(dut.clk)
            t += 1
            v = chip.uo(2)
            if v and not prev:
                rises.append(t)
                r = t
            if prev and not v:
                highs.add(t - r)
            prev = v
    w = cocotb.start_soon(watch())
    words = nec_words(0x5A, 0xC3)
    for wd in words:                               # keep the 4-word TX FIFO topped up
        while (await chip.read(R.STATUS)) >> 3 & 1:
            await ClockCycles(dut.clk, 100)
        await chip.write(R.TXF(0), wd)
    await ClockCycles(dut.clk, 200 * period)
    w.kill()
    assert highs == {2 * (clkdiv + 1)}, highs      # 50% duty carrier
    # envelope: a gap longer than one carrier period ends a mark
    pairs, start = [], rises[0]
    for a, b in zip(rises, rises[1:]):
        if b - a > period:
            mark = (a - start) // period + 1
            pairs.append((mark, round((b - a) / period - 1.5)))   # last cycle + out = 1.5 periods
            start = b
    assert nec_decode(pairs) == (0x5A, 0xC3), pairs[:4]


@cocotb.test()
async def swd_debug_port(dut):
    """ARM SWD: switch the target from JTAG to SWD, read the DP IDCODE (an STM32's 0x2BA01477),
    request debug power-up and read it acknowledged; one wire that turns around mid-transaction."""
    from devices import SwdTarget
    from pemu import swd
    SWDIO, SWCLK = 0, 1
    chip = await setup(dut)
    bus = PinBus(dut, chip)
    target = SwdTarget(bus, SWDIO, SWCLK)
    p = prog('swd', 0)
    await chip.load(0, EngineConfig(clkdiv=3, serial=True, clk=True, clk_pin=SWCLK, out_base=SWDIO,
                                    out_cnt=1, set_base=SWDIO, set_cnt=2, in_base=SWDIO,
                                    out_shr=True, in_shr=True), p)
    await chip.write(R.CTRL, 0x11)

    async def xfer(words, nret):
        for w in words:
            while (await chip.read(R.STATUS)) >> 3 & 1:
                await ClockCycles(dut.clk, 50)
            await chip.write(R.TXF(0), w)
        out = []
        for _ in range(nret):
            for _ in range(200):
                if not (await chip.read(R.STATUS)) & 1:
                    break
                await ClockCycles(dut.clk, 100)
            else:
                raise AssertionError(f'no reply; target {target.log}')
            out.append(await chip.read(R.RXF(0)))
        return out

    host = swd.Swd(p.labels, xfer)
    await host.seq(swd.JTAG_TO_SWD)
    ack, idcode = await host.read(0, swd.DP_IDCODE)
    assert ack == swd.ACK_OK and idcode == 0x2BA01477, (ack, hex(idcode or 0), target.log)
    assert await host.write(0, swd.DP_CTRLSTAT, 0x50000000) == swd.ACK_OK
    ack, stat = await host.read(0, swd.DP_CTRLSTAT)
    assert ack == swd.ACK_OK and stat == 0xF0000000, hex(stat or 0)   # CSYS/CDBG PWRUPACK set
    assert target.log[:2] == [('swd',), ('reset',)], target.log
    assert ('W', 0x4, 0x50000000) in target.log


@cocotb.test()
async def dmx512_packet(dut):
    """DMX512: BREAK, MARK AFTER BREAK, start code and channels at 250 kbit/s 8N2, decoded from the
    pin with the spec's timing limits; two packets back to back."""
    chip = await setup(dut)
    p = prog('dmx_tx', 0)
    bit = 240                                       # 250 kbit/s at 60 MHz
    await chip.load(0, EngineConfig(serial=True, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2),
                                    set_cnt=1, out_shr=True, bt_period=bit, bt_point=bit // 2), p)
    await chip.write(R.CTRL, 0x11)
    runs = []                                       # (level, length in clocks)

    async def watch():
        prev, n = chip.uo(2), 0
        while True:
            await FallingEdge(dut.clk)
            v = chip.uo(2)
            if v != prev:
                runs.append((prev, n))
                prev, n = v, 0
            n += 1
    w = cocotb.start_soon(watch())
    packets = [[0, 255, 128, 0, 17, 42], [0, 1, 2, 3]]
    for pk in packets:
        for i, b in enumerate(pk):
            while (await chip.read(R.STATUS)) >> 3 & 1:
                await ClockCycles(dut.clk, 200)
            await chip.write(R.TXF(0), (b << 1) | (i == 0))
    while not (await chip.read(R.STATUS)) >> 2 & 1:   # TX FIFO empty
        await ClockCycles(dut.clk, 200)
    await ClockCycles(dut.clk, 30 * bit)
    w.kill()
    # rebuild the waveform as levels per clock, then decode it
    wave = []
    for lv, n in runs[1:]:                         # skip the idle before the first break
        wave += [lv] * n
    wave += [1] * (20 * bit)
    got, i = [], 0
    while i < len(wave):
        if wave[i]:
            i += 1
            continue
        j = i
        while j < len(wave) and not wave[j]:
            j += 1
        if j - i >= 88 * 60:                        # BREAK >= 88 us
            k = j
            while wave[k]:
                k += 1
            assert k - j >= 8 * 60, f'MAB {(k - j) / 60:.1f} us'
            got.append([])
            i = k
            continue
        b = sum(wave[i + bit // 2 + bit * (n + 1)] << n for n in range(8))   # 8 data bits
        assert wave[i + bit * 9 + bit // 2] and wave[i + bit * 10 + bit // 2], 'missing stop bits'
        got[-1].append(b)
        i += bit * 10
    assert got == packets, got


@cocotb.test()
async def crc32_ethernet(dut):
    """The CRC unit at full width: CRC-32 (Ethernet / zip / PNG) of messages, checked against zlib."""
    import zlib
    chip = await setup(dut)
    p = prog('crc32', 0)
    await chip.load(0, EngineConfig(out_shr=True, autopull=True, pull_thresh=0,
                                    crc_poly=0x04C11DB7, crc_width=32), p)
    await chip.write(R.CTRL, 0x11)
    for msg in [b'123456789', b'\x00' * 8, b'\xff' * 12, bytes(range(32)), b'rover']:
        data = msg + b'\x00' * (-len(msg) % 4)
        words = [8 * len(msg) - 1] + [int.from_bytes(data[i:i + 4], 'little') for i in range(0, len(data), 4)]
        for wd in words:
            while (await chip.read(R.STATUS)) >> 3 & 1:
                await ClockCycles(dut.clk, 20)
            await chip.write(R.TXF(0), wd)
        for _ in range(200):
            if not (await chip.read(R.STATUS)) & 1:
                break
            await ClockCycles(dut.clk, 50)
        got = await chip.read(R.RXF(0))
        assert got == zlib.crc32(msg), (msg, hex(got), hex(zlib.crc32(msg)))


@cocotb.test()
async def ir_remote_receive_and_rgb(dut):
    """The LED-board demo: NEC remote frames (as the IR receiver module outputs them: low during
    a burst) decoded on ui_in[7] by ir_nec_rx, with repeat codes and glitches ignored; and
    rgb_pwm driving three pins whose duty cycles match the colour."""
    from pemu.ir import nec_word, nec_config
    from pemu.rgb import word
    chip = await setup(dut)
    rx = prog('ir_nec_rx', 0)
    pw = prog('rgb_pwm', len(rx))
    unit = 80                                         # clocks per NEC unit (562.5 us), fast for sim
    await chip.load(0, nec_config(R.ui(7), unit / 562.5e-6), rx)
    await chip.load(1, EngineConfig(clkdiv=0, out_base=R.uo(2), out_cnt=3, out_shr=True).for_program(pw), pw)
    await chip.write(R.CTRL, 0x33)

    async def mark_space(m, s):
        chip.set_ui(7, 0)
        await ClockCycles(dut.clk, m * unit)
        chip.set_ui(7, 1)
        await ClockCycles(dut.clk, s * unit)

    async def frame(addr, cmd):
        data = addr | (addr ^ 0xFF) << 8 | cmd << 16 | (cmd ^ 0xFF) << 24
        await mark_space(16, 8)
        for i in range(32):
            await mark_space(1, 3 if data >> i & 1 else 1)
        await mark_space(1, 40)

    async def pop():
        for _ in range(100):
            if not (await chip.read(R.STATUS)) & 1:
                return await chip.read(R.RXF(0))
            await ClockCycles(dut.clk, 100)
        raise AssertionError(f'no IR word; pcs {(await chip.read(R.PCS)):#x} labels {rx.labels}')

    await ClockCycles(dut.clk, 20 * unit)
    await frame(0x04, 0x08)                           # e.g. a TV's address and a button
    assert nec_word(await pop()) == (0x04, 0x08)
    for _ in range(3):                                # button held: repeat codes are ignored
        await mark_space(16, 4)
        await mark_space(1, 40)
    await mark_space(2, 20)                           # glitches (data-length marks) are ignored
    await mark_space(1, 5)
    await frame(0x00, 0x45)
    assert nec_word(await pop()) == (0x00, 0x45)
    assert (await chip.read(R.STATUS)) & 1, 'an extra word came from a repeat code or a glitch'

    for colour in [(255, 0, 0), (0, 128, 64), (17, 200, 3)]:
        await chip.write(R.TXF(1), word(*colour))
        await ClockCycles(dut.clk, 600)               # let the new colour start
        highs = [0, 0, 0]
        for _ in range(255 * 4):
            await FallingEdge(dut.clk)
            for c in range(3):
                highs[c] += chip.uo(2 + c)
        duty = [round(h / 4) for h in highs]
        assert all(abs(d - v) <= 2 for d, v in zip(duty, colour)), (colour, duty)


@cocotb.test()
async def servo_pulse(dut):
    """The tutorial's example (docs/TUTORIAL.md): servo pulses of the requested width and period,
    and a new width taking over at the next period."""
    from pemu.servo import word
    chip = await setup(dut)
    p = prog('servo', 0)
    await chip.load(0, EngineConfig(set_base=R.uo(2), set_cnt=1, out_shr=True), p)
    await chip.write(R.CTRL, 0x11)
    await chip.write(R.TXF(0), word(150, 1000))      # 1 tick = 1 clock here
    runs = []

    async def watch():
        prev, n = chip.uo(2), 0
        while True:
            await FallingEdge(dut.clk)
            v = chip.uo(2)
            if v != prev:
                runs.append((prev, n))
                prev, n = v, 0
            n += 1
    w = cocotb.start_soon(watch())
    await ClockCycles(dut.clk, 3500)
    await chip.write(R.TXF(0), word(200, 1000))
    await ClockCycles(dut.clk, 3500)
    w.kill()
    highs = [n for lv, n in runs if lv == 1][1:]
    lows = [n for lv, n in runs if lv == 0][1:]
    assert highs[:2] == [150, 150] and highs[-2:] == [200, 200], highs
    assert all(h + l == 1000 for h, l in zip(highs, lows)), list(zip(highs, lows))


@cocotb.test()
async def protocol_detective(dut):
    """edge_capture timestamps every edge of a pin (here: another engine's output, looped back);
    the host's classifier then names the protocol and its rate (60 MHz clock assumed)."""
    from pemu.detect import classify, from_times
    from pemu.servo import word as servo_word
    cases = [
        ('uart_tx', EngineConfig(serial=True, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1,
                                 out_shr=True, bt_period=521, bt_point=260), list(b'Rover 42\x00\xff'), 'UART'),
        ('manchester_tx', EngineConfig(clkdiv=74, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1,
                                       out_shr=True), [0xA5, 0x3C, 0x00, 0xFF, 0x81], 'Manchester'),
        ('servo', EngineConfig(clkdiv=5, set_base=R.uo(2), set_cnt=1, out_shr=True),
         [servo_word(300, 1000)], 'PWM'),
    ]
    for name, cfg, words, want in cases:
        chip = await setup(dut)
        chip.loop = True
        src = prog(name, 0)
        cap = prog('edge_capture', len(src))
        await chip.load(0, cfg, src)
        await chip.load(1, EngineConfig(in_base=R.ui(3)).for_program(cap), cap)
        await chip.write(R.CTRL, 0x22)                # the recorder first
        await chip.write(R.CTRL, 0x33)
        for w in words:
            while (await chip.read(R.STATUS)) >> 3 & 1:
                await ClockCycles(dut.clk, 50)
            await chip.write(R.TXF(0), w)
        times, quiet = [], 0
        for _ in range(3000):
            if not (await chip.read(R.STATUS)) >> 4 & 1:
                times.append(await chip.read(R.RXF(1)))
                quiet = 0
                if len(times) >= 80:
                    break
            else:
                quiet += 1
                if times and quiet > 150:
                    break
        assert all(b > a for a, b in zip(times, times[1:]))   # every edge, in order
        kind, details, sentence = classify(from_times(times), 60_000_000)
        dut._log.info(f'{name}: {len(times)} edges -> {sentence}')
        assert kind == want, (name, kind, details, len(times))


@cocotb.test()
async def lockstep_with_model(dut):
    """`pe.py lockstep` on the RTL: three engines (UART TX, servo, pin logger on the loopback
    wires) started on one clock; the RTL's change log equals the golden model's, change for
    change and clock for clock."""
    from pemu import lockstep
    from pemu.board import ModelBoard
    from pemu.host import Host
    chip = await setup(dut)
    chip.loop = True
    words = []
    for op in lockstep.plan():
        if op[0] == 'write':
            await chip.write(op[1], op[2])
        elif op[0] == 'load':
            await chip.load(op[1], op[2], op[3])
        elif op[0] == 'push':
            for w in op[2]:
                await chip.write(R.TXF(op[1]), w)
    n = 40
    for _ in range(20000):
        if not (await chip.read(R.STATUS)) >> 8 & 1:
            words.append(await chip.read(R.RXF(2)))
            if len(words) >= n:
                break
    rtl = lockstep.to_log(words)
    m = ModelBoard()
    model = lockstep.run_sync(Host(m), m, n)
    matched, diff = lockstep.compare(rtl, model)
    assert diff is None and matched == n, (matched, diff, rtl[:6], model[:6])
    dut._log.info(f'{n} changes over {rtl[-1][0]} clocks: RTL and model identical')


@cocotb.test()
async def uart_autobaud(dut):
    """CTL BAUD: the engine times the sender's 'U', retimes its own bit timer and receives at
    whatever rate the sender chose. No baud rate is configured by the host."""
    from chip import pop_bytes
    chip = await setup(dut)
    p = prog('uart_autobaud', 0)
    pin = R.ui(3)
    cfg = EngineConfig(serial=True, in_base=pin, jmp_pin=pin, in_shr=True, out_shr=True,
                       resync_pin=pin, resync_edge=1)

    async def send(data, bit):
        for b in data:
            for v in [0] + [(b >> i) & 1 for i in range(8)] + [1]:
                chip.set_ui(3, v)
                await ClockCycles(dut.clk, bit)
        chip.set_ui(3, 1)
        await ClockCycles(dut.clk, 2 * bit)

    chip.set_ui(3, 1)
    await chip.load(0, cfg, p)
    for bit in (37, 10, 113):                 # 1.2 Mbaud, 4.5 Mbaud, 400 kbaud at 45 MHz
        await chip.write(R.CTRL, 0x11)        # (re)start: measure again
        await ClockCycles(dut.clk, 20)
        msg = f'{bit} clk/bit'.encode()
        await send(b'U', bit)
        await ClockCycles(dut.clk, 12 * bit)  # the idle check
        got = b''
        for i in range(0, len(msg), 4):         # the RX FIFO holds 4 bytes
            await send(msg[i:i + 4], bit)
            got += await pop_bytes(chip, 0, len(msg[i:i + 4]))
        assert got == msg, (bit, got)
        assert await chip.read(R.EVENTS) == 0
        dut._log.info(f'{bit} clocks/bit: received {got!r}')


@cocotb.test()
async def bit_rate_switch(dut):
    """CTL BAUD mid-frame (the CAN FD bit-rate switch): 16 bits at the nominal rate, switching at
    the sample point of the last one (BRS), then 32 bits at the data rate, then back. The pin is
    recorded every clock and compared with the exact expected waveform."""
    chip = await setup(dut)
    p = prog('brs_tx', 0)
    cfg = EngineConfig(serial=True, out_base=R.uo(2), out_cnt=1, set_base=R.uo(2), set_cnt=1)
    header, data = 0x5A3C, 0xDEADBEEF
    for pn, pf in ((40, 10), (24, 4)):
        await chip.write(R.CTRL, 0)
        await chip.load(0, cfg, p)
        await chip.write(R.CTRL, 0x11)                 # (re)start: this also empties the FIFOs
        await chip.write(R.TXF(0), [(pn // 2) << 16 | pn, (pf // 2) << 16 | pf])
        await ClockCycles(dut.clk, 3 * pn)
        wave = []

        async def record():                       # runs while the host is still writing
            for _ in range(200 * pn):
                if len(wave) >= 16 * pn + 32 * pf + 4 * pn:
                    return
                await RisingEdge(dut.clk)
                if wave or chip.uo(2) == 0:      # from the first dominant bit (header MSB is 0)
                    wave.append(chip.uo(2))

        rec = cocotb.start_soon(record())
        await chip.write(R.TXF(0), [header << 16, data])
        await rec
        # expected: header bits pn clocks each, BRS cut at its sample point (+4 clocks for
        # NOP.P -> PULL -> MOV -> BAUD), data bits pf clocks each, then idle high
        exp = []
        for i in range(16):
            b = (header >> (15 - i)) & 1
            exp += [b] * (pn if i < 15 else pn // 2 + 4)
        for i in range(32):
            exp += [(data >> (31 - i)) & 1] * pf
        exp += [1] * (len(wave) - len(exp))
        def runs(x):
            out = []
            for v in x:
                if out and out[-1][0] == v:
                    out[-1][1] += 1
                else:
                    out.append([v, 1])
            return out
        assert wave == exp, (pn, pf, runs(wave), runs(exp))
        dut._log.info(f'nominal {pn} clocks/bit -> data {pf} clocks/bit and back: exact')
