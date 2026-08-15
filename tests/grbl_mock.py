import math
import re
import serial
import sys
import time

from types import SimpleNamespace as Entity

state = sys.modules[__name__]

PORT = sys.argv[1] if len(sys.argv) > 1 else 'COM40'
BAUD = sys.argv[2] if len(sys.argv) > 2 else 115200

print(f"[MOCK] Запуск динамического эмулятора GRBL на {PORT}...")

try:
    ser = serial.Serial(PORT, BAUD, timeout=0.01)
except Exception as e:
    print(f"[ОШИБКА] Не удалось открыть порт {PORT}: {e}")
    input()
    exit()

def serial_write(*strs, delay=0.01):
    time.sleep(delay)
    msg = ''.join([f'{s}\r\n' for s in strs])
    # print(msg)
    ser.write(msg.encode('ascii'))

def vzero():
  return {'X': 0.0, 'Y': 0.0, 'Z': 0.0}

def vdiv(v, s):
    return {k:u/s for k,u in v.items()}
def vmul(v, s):
    return {k:u*s for k,u in v.items()}

def vadd(a, b):
    return {k:u + (b[k] if k in b else 0) for k,u in a.items()}
def vsub(a, b):
    return {k:u - (b[k] if k in b else 0) for k,u in a.items()}
def veq(a, b):
    return all([math.isclose(u, (b[k] if k in b else 0), rel_tol=1e-6) for k,u in a.items()])

def vlen2(v):
    return sum([u*u for u in v.values()])
def vlen(v):
    return vlen2(v)**.5
def vnorm(v):
    l = vlen(v)
    return {k:u/l for k,u in v.items()}

PLANNED_MAX = 16
planned = []
FEED_MAX = 1200
feed = FEED_MAX
is_relative = False
pos = vzero()
target = vzero()
is_moving = False
is_on_hold = False

g_regex = re.compile(r'([FGXYZ])\s*(-?\d+\.?\d*)', re.IGNORECASE)
def parse_command(cmd):
    s = Entity(xyz={}, feed=None, is_relative=None, is_probe=False, is_motion=False, ok=False)
    matches = g_regex.findall(cmd)
    for axis, val in matches:
        if axis == 'F':
            s.feed = max(min(float(val), FEED_MAX), 0)
        elif axis == 'G':
            val = int(float(val))
            if int(val) in [ 0, 1, 38 ]:
                s.is_motion = True
                if val == 1:
                    s.feed = FEED_MAX
                s.is_probe = val == 38
            elif val == 90: s.is_relative = False
            elif val == 91: s.is_relative = True
        elif axis in ['X', 'Y', 'Z']:
            s.xyz[axis]=float(val)
    s.ok = True
    return s

def get_state():
    if state.is_on_hold: return 'Hold'
    if state.is_moving: return 'Run'
    return 'Idle'

cmds = []
def do_immediate(cmd):
    if cmd == '\x18' or cmd == '$X':
        cmds.clear()
        state.planned.clear()
        state.target = dict(state.pos)
        state.is_moving = False
        state.is_on_hold = False
        serial_write('ok')
        return False

    if cmd == '!':
        state.is_on_hold = True
        print('HOLD!')
        return True
    if cmd == '~':
        if state.is_on_hold:
            serial_write('[MSG:]')
        state.is_on_hold = False
        print('RELEASE!')
        serial_write('ok')
        return True

    if cmd == '?':
        serial_write(f"<{get_state()}"
f"|MPos:{pos['X']:.3f},{pos['Y']:.3f},{pos['Z']:.3f}"
f"|WPos:{pos['X']:.3f},{pos['Y']:.3f},{pos['Z']:.3f}"
f"|Bf:{max(PLANNED_MAX - len(state.planned), 0)},128"
f"|FS:{state.feed:.3f},0"
f"|WCO:0.000,0.000,0.000"
">")
        return True

    if cmd == '$G':
        serial_write(f'[GC:G0 G54 G92 G17 G21 G{91 if state.is_relative else 90} G94 G49 G98 G50 M5 M9 T0 F{state.feed:.3f} S0.]', 'ok')
        return True

    if cmd == '$H':
        state.planned.clear()
        state.pos = vzero()
        state.target = vzero()
        state.is_moving = False
        state.is_on_hold = False
        state.serial_write('ok')
        print('HOME!')
        return True

    if cmd.startswith('$J='):
        s = parse_command(cmd[3:])
        if s.ok:
            state.planned.insert(0, (s.xyz, s.feed, s.is_relative, False))
            serial_write('ok')
        return True

    return False


def enqueue_planned(cmd):
    s = parse_command(cmd)
    if not s.ok:
        return False
    if s.is_motion:
        if len(state.planned) >= PLANNED_MAX:
            return False
        if s.feed is not None:
            state.feed = s.feed
        if s.is_relative:
            state.is_relative = s.is_relative
        state.planned.append((s.xyz, state.feed, state.is_relative, s.is_probe))
    serial_write('ok')
    return True

buf = ""
def read_serial():
    global buf
    if ser.in_waiting <= 0:
        return
    chars = ser.read(ser.in_waiting).decode('ascii', errors='ignore')
    for c in chars:
        if c not in ['\n', '\r']:
            buf += c
            if c not in ['?', '!', '~', '\x18']:
                continue
        if buf:
            cmd = buf.strip().upper()
            buf = ""
            if cmd and (not cmds or cmds[-1] != cmd):
                if cmd != '?':
                    print(cmd)
                cmds.append(cmd)

def process_commands():
    i = 0
    while i < len(cmds):
        if do_immediate(cmds[i]):
            del cmds[i]
        else:
            i += 1
    if state.is_on_hold:
        return
    while cmds and enqueue_planned(cmds[0]):
        del cmds[0]

last_update = time.time()
def update_mpos():
    global last_update
    now = time.time()
    dt = (now - last_update) / 60.0 # mm/min
    last_update = now
    if state.is_on_hold or not planned:
        state.is_moving = False
        return

    xyz, f, rel, prb = state.planned[0]
    if not state.is_moving:
        if prb:
            serial_write(f"[PRB:{state.target['X']:.3f},{state.target['Y']:.3f},{0.001}:1")
            del state.planned[0]
            return
        if rel:
            state.target = vadd(state.target, xyz)
        else:
            for k,u in xyz.items():
                state.target[k] = u
        state.is_moving = True
        print('START MOVING!', state.target)

    if state.is_moving:
        diff = vsub(state.target, state.pos)
        diff_l = vlen(diff)
        if diff_l > f * dt:
            state.pos = vadd(pos, vmul(vdiv(diff, diff_l), f * dt))
        else:
            state.pos = dict(state.target)
            state.is_moving = False
            del state.planned[0]
            print('END MOVING!', state.target)
            print(planned)

ser.write(b"\r\nGrbl 1.1f ['$' for help]\r\n")
print("[MOCK] Отправлено приветствие GRBL. Ожидание команд...")

while True:
    read_serial()
    process_commands()
    update_mpos()

