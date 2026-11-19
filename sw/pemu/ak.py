"""CubeMars AK motors in MIT mode: command and reply frames (standard 11-bit IDs).

Command to the motor's ID, 8 bytes:
  position 16 bit, velocity 12, kp 12, kd 12, torque 12 (each mapped linearly onto its range)
Reply (to the master's ID, 0 by default), 6 bytes:
  [motor id][position 16][velocity 12][current/torque 12]
Special commands: enter motor mode FF..FC, exit FF..FD, set zero FF..FE.

The ranges differ by motor model; check them against the CubeMars manual for your firmware
before driving a motor (the rover's drive motors are AK80-8).
"""

from dataclasses import dataclass

from .can import Frame


@dataclass
class Limits:
    p: float = 12.5      # rad
    v: float = 37.5      # rad/s
    kp: float = 500.0
    kd: float = 5.0
    t: float = 32.0      # N m


MODELS = {
    'AK80-8': Limits(12.5, 37.5, 500, 5, 32.0),
    'AK70-10': Limits(12.5, 50.0, 500, 5, 25.0),
    'AK80-9': Limits(12.5, 50.0, 500, 5, 18.0),
    'AK60-6': Limits(12.5, 45.0, 500, 5, 15.0),
}

ENTER = bytes([0xFF] * 7 + [0xFC])
EXIT = bytes([0xFF] * 7 + [0xFD])
ZERO = bytes([0xFF] * 7 + [0xFE])


def f2u(x, lo, hi, bits):
    x = min(max(x, lo), hi)
    return int(round((x - lo) * ((1 << bits) - 1) / (hi - lo)))


def u2f(u, lo, hi, bits):
    return u * (hi - lo) / ((1 << bits) - 1) + lo


def command(motor_id, p=0.0, v=0.0, kp=0.0, kd=0.0, t=0.0, lim=Limits()):
    pu = f2u(p, -lim.p, lim.p, 16)
    vu = f2u(v, -lim.v, lim.v, 12)
    kpu = f2u(kp, 0, lim.kp, 12)
    kdu = f2u(kd, 0, lim.kd, 12)
    tu = f2u(t, -lim.t, lim.t, 12)
    d = bytes([pu >> 8, pu & 0xFF, vu >> 4, ((vu & 0xF) << 4) | (kpu >> 8), kpu & 0xFF,
               kdu >> 4, ((kdu & 0xF) << 4) | (tu >> 8), tu & 0xFF])
    return Frame(motor_id, d)


def special(motor_id, data):
    return Frame(motor_id, data)


def reply(frame, lim=Limits()):
    """Decode a motor reply: dict(id, p, v, t) or None if it is not one."""
    d = frame.data
    if frame.ext or len(d) < 6:
        return None
    pu = (d[1] << 8) | d[2]
    vu = (d[3] << 4) | (d[4] >> 4)
    tu = ((d[4] & 0xF) << 8) | d[5]
    return dict(id=d[0], p=u2f(pu, -lim.p, lim.p, 16), v=u2f(vu, -lim.v, lim.v, 12),
                t=u2f(tu, -lim.t, lim.t, 12))
