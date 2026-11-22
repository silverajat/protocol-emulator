"""Draws docs/video/brs_waveform.png: brs_tx.pio on the golden model, one frame with the bit rate
switched mid-frame by CTL BAUD (40 -> 10 clocks per bit and back)."""
import sys
sys.path.insert(0, 'sw')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pemu import load_file, EngineConfig
from pemu.config import load_writes
from pemu.model import Core
import pemu.config as R

PN, PF, HEADER, DATA = 40, 10, 0x5A3C, 0xDEADBEEF
p = load_file('sw/programs/brs_tx.pio')
pin = R.uo(2)
cfg = EngineConfig(serial=True, out_base=pin, out_cnt=1, set_base=pin, set_cnt=1)
c = Core()
c.step(rst=True)
for a, ws in load_writes(0, cfg, p):
    for w in ws:
        c.step(we=True, addr=a, wdata=w)
for w in [(PN // 2) << 16 | PN, (PF // 2) << 16 | PF, HEADER << 16, DATA]:
    c.step(we=True, addr=R.TXF(0), wdata=w)
c.step(we=True, addr=R.CTRL, wdata=1)
wave = []
for _ in range(1400):
    c.step()
    wave.append((c.pin_val >> pin) & 1)
t0 = wave.index(0) - 20
wave = wave[t0:t0 + 16 * PN + 32 * PF + 120]
brs_end = 20 + 15 * PN + PN // 2 + 4
data_end = brs_end + 32 * PF

INK, MUTED, BG, LINE = '#14231b', '#566a5f', '#f4f2ec', '#0f5c3f'
fig, ax = plt.subplots(figsize=(12, 3.0), dpi=150)
fig.patch.set_facecolor(BG); ax.set_facecolor(BG)
ax.axvspan(20, brs_end, color='#e4ece6', lw=0)
ax.axvspan(brs_end, data_end, color='#f6e7d2', lw=0)
ax.step(range(len(wave)), wave, where='post', color=LINE, lw=2)
ax.text((20 + brs_end) / 2, 1.28, f'nominal rate: {PN} clocks per bit', ha='center', color=INK, fontsize=11)
ax.text((brs_end + data_end) / 2, 1.28, f'data rate: {PF} clocks per bit', ha='center', color=INK, fontsize=11)
ax.annotate('CTL BAUD at the\nsample point of BRS', xy=(brs_end, 0.5), xytext=(brs_end - 150, -0.55),
            color=INK, fontsize=10, arrowprops=dict(arrowstyle='->', color=INK))
ax.annotate('CTL BAUD:\nback to nominal', xy=(data_end, 0.5), xytext=(data_end + 20, -0.55),
            color=INK, fontsize=10, arrowprops=dict(arrowstyle='->', color=INK))
ax.set_ylim(-0.75, 1.5); ax.set_yticks([0, 1]); ax.set_yticklabels(['0', '1'], color=MUTED)
ax.set_xlim(0, len(wave)); ax.set_xlabel('clock cycles', color=MUTED)
ax.tick_params(colors=MUTED)
for s in ('top', 'right', 'left'):
    ax.spines[s].set_visible(False)
ax.spines['bottom'].set_color('#b9b3a5')
fig.suptitle('One engine, two bit rates in one frame (the CAN FD bit-rate switch)', x=0.01, ha='left',
             fontsize=13, fontweight='bold', color=INK)
fig.tight_layout(rect=(0, 0, 1, 0.93))
fig.savefig('docs/video/brs_waveform.png', facecolor=BG)
