"""Draws docs/img/timing_story.svg: worst-corner fmax of each GDS run in the timing-closure story
(WRITEUP section 5). Numbers are from the runs' post-route STA (slow corner, 1.08 V, 125 C)."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

RUNS = [  # label, fmax MHz (None = did not route), note
    ('30 ns, resource sharing on', 37.0, ''),
    ('30 ns, sharing off', 41.0, ''),
    ('30 ns, + RTL path fixes', 41.5, ''),
    ('22 ns, spread placement + wire buffering', 48.0, ''),
    ('22 ns, + CTL BAUD', 41.6, 'missed 22 ns by 2.0 ns'),
    ('22 ns, Hardcaml-generated chip', None, 'did not route in 6 h'),
    ('25 ns, Hardcaml chip + CTL BAUD (submitted)', 57.0, ''),
]
INK, MUTED, BG = '#14231b', '#566a5f', '#f4f2ec'
MAIN, STEP, FAIL = '#0f5c3f', '#7aa58f', '#c8c2b4'

fig, ax = plt.subplots(figsize=(9.6, 4.6), dpi=100)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
ys = list(range(len(RUNS)))[::-1]
for y, (label, f, note) in zip(ys, RUNS):
    if f is None:
        ax.text(0.8, y, note, va='center', ha='left', color=MUTED, fontsize=10, style='italic')
        continue
    last = 'submitted' in label
    color = MAIN if last else (FAIL if note else STEP)
    ax.barh(y, f, height=0.62, color=color, edgecolor=BG, linewidth=2)
    txt = f'{f:g} MHz' + (f'   {note}' if note else '')
    ax.text(max(f, 40.3) + 0.8, y, txt, va='center', ha='left', color=INK if last else MUTED,
            fontsize=10, fontweight='bold' if last else 'normal')
ax.set_yticks(ys)
ax.set_yticklabels([r[0] for r in RUNS], fontsize=10, color=INK)
ax.axvline(40, color=INK, linewidth=1, linestyle=(0, (4, 3)))
ax.text(40, len(RUNS) - 0.35, ' rated clock, 40 MHz', color=INK, fontsize=9.5, va='bottom')
ax.set_xlim(0, 72)
ax.set_ylim(-0.6, len(RUNS) - 0.1)
ax.set_xlabel('worst-corner fmax (1.08 V, 125 C), MHz', color=MUTED, fontsize=10)
ax.tick_params(axis='x', colors=MUTED, labelsize=9)
ax.tick_params(axis='y', length=0)
for s in ('top', 'right', 'left'):
    ax.spines[s].set_visible(False)
ax.spines['bottom'].set_color('#b9b3a5')
ax.grid(axis='x', color='#dcd7cb', linewidth=0.8)
ax.set_axisbelow(True)
fig.suptitle('Timing closure, one measured GDS run at a time', x=0.02, ha='left', fontsize=14,
             fontweight='bold', color=INK)
fig.text(0.02, 0.885, 'Each bar is a full place-and-route run, signed off at the slow corner. '
         'The final 25 ns layout routed fastest and has the most margin.', fontsize=9.5, color=MUTED)
fig.tight_layout(rect=(0, 0, 1, 0.88))
fig.savefig('docs/img/timing_story.svg', facecolor=BG)
fig.savefig('/tmp/timing_story.png', facecolor=BG, dpi=150)
