"""An emulated CubeMars AK motor (MIT mode) for hardware-in-the-loop testing of the rover's
controller: the chip is the CAN node (sw/programs/can_fault.pio), this is the motor behind it.

The controller sends MIT commands (p_des, v_des, kp, kd, t_ff) to the motor's ID; the motor
applies  torque = kp (p_des - p) + kd (v_des - v) + t_ff  (clamped), integrates a simple
wheel model, and answers each command with its position, velocity and torque, as a real one
does. Faults can be switched on at any time to see how the controller copes:

  none        behave
  offline     no ACK and no reply (the engine is stopped: as if the motor lost power)
  late MS     reply MS milliseconds late
  drop N      no reply to every Nth command (the command is still ACKed)
  bad-crc     replies go out with a corrupted CRC (receivers reject them)
  stuck       the encoder freezes: position and velocity stop changing in the replies
  overtemp    the temperature reads 90 C and the error byte is set
"""

import time
from dataclasses import dataclass, field

from .ak import Limits, MODELS, ENTER, EXIT, ZERO, f2u, u2f
from .can import Frame


@dataclass
class Wheel:
    inertia: float = 0.02         # kg m^2 at the output shaft (a rover wheel, roughly)
    friction: float = 0.15        # N m s/rad viscous
    coulomb: float = 0.3          # N m dry friction


@dataclass
class EmulatedMotor:
    motor_id: int = 1
    master_id: int = 0
    model: str = 'AK80-8'
    wheel: Wheel = field(default_factory=Wheel)
    p: float = 0.0
    v: float = 0.0
    t: float = 0.0
    enabled: bool = False
    fault: str = 'none'
    fault_arg: float = 0
    temp_c: int = 31
    commands: int = 0
    last_t: float = None
    frozen: tuple = None

    @property
    def lim(self) -> Limits:
        return MODELS[self.model]

    def set_fault(self, name, arg=0):
        self.fault, self.fault_arg = name, arg
        self.frozen = (self.p, self.v) if name == 'stuck' else None

    def _advance(self, now):
        if self.last_t is None:
            self.last_t = now
            return
        dt = max(0.0, min(now - self.last_t, 0.05))
        self.last_t = now
        steps = max(1, int(dt / 0.0005))
        h = dt / steps
        for _ in range(steps):
            fric = self.wheel.friction * self.v + (self.wheel.coulomb if self.v > 0 else -self.wheel.coulomb if self.v < 0 else 0)
            a = (self.t - fric) / self.wheel.inertia
            nv = self.v + a * h
            if self.v and nv * self.v < 0 and abs(self.t) < self.wheel.coulomb:
                nv = 0.0                      # dry friction holds it
            self.v = max(-self.lim.v, min(self.lim.v, nv))
            self.p += self.v * h

    def handle(self, frame, now=None):
        """A frame from the bus -> (reply Frame or None, delay in seconds, corrupt_crc)."""
        now = time.monotonic() if now is None else now
        if frame.ext or frame.ident != self.motor_id or len(frame.data) != 8 or not frame.crc_ok:
            return None, 0, False
        self._advance(now)
        d = frame.data
        if d == ENTER:
            self.enabled = True
        elif d == EXIT:
            self.enabled, self.t = False, 0.0
        elif d == ZERO:
            self.p = 0.0
        else:
            lim = self.lim
            pu = d[0] << 8 | d[1]
            vu = d[2] << 4 | d[3] >> 4
            kpu = (d[3] & 0xF) << 8 | d[4]
            kdu = d[5] << 4 | d[6] >> 4
            tu = (d[6] & 0xF) << 8 | d[7]
            p_des, v_des = u2f(pu, -lim.p, lim.p, 16), u2f(vu, -lim.v, lim.v, 12)
            kp, kd, tff = u2f(kpu, 0, lim.kp, 12), u2f(kdu, 0, lim.kd, 12), u2f(tu, -lim.t, lim.t, 12)
            torque = kp * (p_des - self.p) + kd * (v_des - self.v) + tff if self.enabled else 0.0
            self.t = max(-lim.t, min(lim.t, torque))
        self.commands += 1
        if self.fault == 'drop' and self.fault_arg and self.commands % int(self.fault_arg) == 0:
            return None, 0, False
        return self.reply(), (self.fault_arg / 1000 if self.fault == 'late' else 0), self.fault == 'bad-crc'

    def reply(self):
        lim = self.lim
        p, v = self.frozen if self.fault == 'stuck' and self.frozen else (self.p, self.v)
        pu = f2u(p, -lim.p, lim.p, 16)
        vu = f2u(v, -lim.v, lim.v, 12)
        tu = f2u(self.t, -lim.t, lim.t, 12)
        temp, err = (90, 1) if self.fault == 'overtemp' else (self.temp_c, 0)
        data = bytes([self.motor_id, pu >> 8, pu & 0xFF, vu >> 4, ((vu & 0xF) << 4) | (tu >> 8),
                      tu & 0xFF, temp, err])     # temperature and error byte: as the newer firmware; check yours
        return Frame(self.master_id, data)
