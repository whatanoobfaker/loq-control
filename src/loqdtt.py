import glob
import lzma
import os
import struct
import time

import loqcommon as lc

DTT_DEVICES = ["/sys/bus/platform/devices/INTC1041:00", "/sys/bus/platform/devices/INTC10A0:00",
               "/sys/bus/platform/devices/INTC1040:00", "/sys/bus/platform/devices/INT3400:00"]
HEADER_SIG = 0x1FE5
KEY_SIG = 0xA0D8
COMPRESSED = 0x40000000

C_DEFAULT = 0x01
C_POWER_SOURCE = 0x08
C_LID = 0x0A
C_TEMPERATURE = 0x11
C_OEM0 = 0x13
C_OEM5 = 0x18
C_TEMP_NO_HYST = 0x2F
C_POWER_SLIDER = 0x38
OEM_BASE = 0x1000
SW_OEM_BASE = 0x2000

EQ, LE, GE, NE = 1, 2, 3, 4
OP_FOR = 2

PROFILE_OEM2 = {"balanced": 0, "performance": 1, "low-power": 2, "quiet": 2, "custom": 3, "max-power": 4}

THROTTLE_STEP_W = 1.0
RELAX_STEP_W = 0.5
HYSTERESIS_C = 1.0


class ParseError(Exception):
    pass


class Reader:
    def __init__(self, buf):
        self.b = buf
        self.o = 0

    def left(self):
        return len(self.b) - self.o

    def u32(self):
        if self.o + 4 > len(self.b):
            raise ParseError("u32 past end")
        v = struct.unpack_from("<I", self.b, self.o)[0]
        self.o += 4
        return v

    def peek_type(self):
        return struct.unpack_from("<I", self.b, self.o)[0]

    def u64(self):
        t = self.u32()
        if t != 4:
            raise ParseError("expected u64 object, got type %d" % t)
        if self.o + 8 > len(self.b):
            raise ParseError("u64 past end")
        v = struct.unpack_from("<Q", self.b, self.o)[0]
        self.o += 8
        return v

    def string(self):
        t = self.u32()
        if t != 8:
            raise ParseError("expected string object, got type %d" % t)
        n = struct.unpack_from("<Q", self.b, self.o)[0]
        self.o += 8
        if self.o + n > len(self.b):
            raise ParseError("string past end")
        s = self.b[self.o:self.o + n].split(b"\0", 1)[0].decode("ascii", "replace")
        self.o += n
        return s

    def skip(self, n):
        self.o += n


def short_name(path):
    return path.rsplit(".", 1)[-1].rstrip("_")


class Gddv:
    def __init__(self):
        self.ppcc = {}
        self.psvts = {}
        self.targets = {}
        self.conditions = []

    @classmethod
    def load(cls, device=None):
        dev = device or next((d for d in DTT_DEVICES if os.path.exists(os.path.join(d, "data_vault"))), None)
        if not dev:
            return None, None
        with open(os.path.join(dev, "data_vault"), "rb") as f:
            raw = f.read()
        g = cls()
        g.parse(raw, 0)
        return g, dev

    def parse(self, buf, depth):
        if depth > 30 or len(buf) < 8:
            raise ParseError("bad data vault segment")
        sig, hsize, version = struct.unpack_from("<HHI", buf, 0)
        if sig != HEADER_SIG:
            raise ParseError("bad data vault signature 0x%x" % sig)
        major = version >> 24
        if major not in (1, 2):
            raise ParseError("unsupported data vault version %d" % major)
        if major == 2:
            flags = struct.unpack_from("<I", buf, 8)[0]
            if flags & COMPRESSED:
                payload = lzma.decompress(buf[hsize:], format=lzma.FORMAT_ALONE)
                hdr = bytearray(buf[:hsize])
                struct.pack_into("<I", hdr, 8, flags & ~COMPRESSED)
                return self.parse(bytes(hdr) + payload, depth)
        off = hsize
        while off + hsize < len(buf):
            if major == 2:
                s = struct.unpack_from("<H", buf, off)[0]
                if s == KEY_SIG:
                    off += 2
                    off += self.parse_key(buf, off)
                elif s == HEADER_SIG:
                    off += self.parse(buf[off:], depth + 1)
                else:
                    raise ParseError("unknown item signature 0x%04x" % s)
            else:
                off += self.parse_key(buf, off)
        return off

    def parse_key(self, buf, off):
        start = off
        _flags, klen = struct.unpack_from("<II", buf, off)
        off += 8
        key = buf[off:off + klen].split(b"\0", 1)[0].decode("ascii", "replace")
        off += klen
        _vtype, vlen = struct.unpack_from("<II", buf, off)
        off += 8
        val = buf[off:off + vlen]
        off += vlen
        parts = [p for p in key.split("/") if p]
        name = typ = point = None
        if parts and parts[0] == "participants":
            name, typ, point = (parts[1:] + [None, None, None])[:3]
        elif parts and parts[0] == "shared":
            typ = parts[2] if len(parts) > 2 else None
            point = parts[3] if len(parts) > 3 and parts[1] == "tables" else None
        try:
            if name and typ == "ppcc":
                self.parse_ppcc(name, val)
            elif typ == "psvt":
                self.parse_psvt(point or name or "Default", val)
            elif typ == "apat":
                self.parse_apat(val)
            elif typ == "apct":
                self.parse_apct(val)
        except (ParseError, struct.error):
            pass
        return off - start

    def parse_ppcc(self, name, b):
        if len(b) < 84:
            return
        q = lambda o: struct.unpack_from("<Q", b, o)[0]
        self.ppcc[short_name(name)] = {"min": q(28), "max": q(40), "step": q(76)}

    def parse_psvt(self, name, b):
        r = Reader(b)
        if r.u64() > 2:
            raise ParseError("psvt version")
        rows = []
        while r.left() > 0:
            src = r.string()
            tgt = r.string()
            prio = r.u64()
            period = r.u64()
            temp = r.u64()
            domain = r.u64()
            knob = r.u64()
            limit = r.string() if r.peek_type() == 8 else str(r.u64())
            step = r.u64()
            r.u64()
            r.u64()
            r.skip(12)
            rows.append({"source": short_name(src), "target": short_name(tgt), "priority": prio,
                         "period": max(period / 10.0, 0.5), "temp": (temp - 2732) / 10.0,
                         "domain": domain, "knob": knob, "limit": limit, "step": step})
        self.psvts[name] = rows

    def parse_apat(self, b):
        r = Reader(b)
        if r.u64() != 2:
            raise ParseError("apat version")
        while r.left() > 0:
            tid = r.u64()
            name = r.string()
            part = r.string()
            dom = r.u64()
            code = r.string()
            arg = r.string()
            t = self.targets.setdefault(tid, {"id": tid, "name": name, "actions": []})
            t["actions"].append({"participant": short_name(part), "domain": dom, "code": code, "argument": arg})

    def parse_apct(self, b):
        r = Reader(b)
        ver = r.u64()
        while r.left() > 0:
            target = r.u64()
            conds = []
            if ver == 1:
                i = 0
                while i < 10:
                    c = {"target": target, "condition": r.u64(), "device": "", "comparison": r.u64(),
                         "argument": r.u64(), "time": 0, "time_cmp": 0}
                    if i < 9:
                        op = r.u64()
                        if op == OP_FOR:
                            r.skip(12)
                            c["time_cmp"] = r.u64()
                            c["time"] = r.u64()
                            r.skip(12)
                            i += 1
                    conds.append(c)
                    i += 1
            elif ver == 2:
                count = r.u64()
                i = 0
                while i < count:
                    cond = r.u64()
                    dev = r.string()
                    r.skip(12)
                    c = {"target": target, "condition": cond, "device": dev, "comparison": r.u64(),
                         "argument": r.u64(), "time": 0, "time_cmp": 0}
                    if i < count - 1:
                        op = r.u64()
                        if op == OP_FOR:
                            r.skip(12)
                            r.string()
                            r.skip(12)
                            c["time_cmp"] = r.u64()
                            c["time"] = r.u64()
                            r.skip(12)
                            i += 1
                    conds.append(c)
                    i += 1
            else:
                raise ParseError("apct version %d" % ver)
            self.conditions.append(conds)


def compare(cmp, value, arg):
    if cmp == EQ:
        return value == arg
    if cmp == LE:
        return value <= arg
    if cmp == GE:
        return value >= arg
    if cmp == NE:
        return value != arg
    return False


def zone_temps():
    out = {}
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        t = lc.read(os.path.join(z, "type"))
        v = lc.read_int(os.path.join(z, "temp"))
        if t and v is not None:
            out[t] = v / 1000.0
    if "TCPU" not in out:
        c = lc.cpu_temp()
        if c is not None:
            out["TCPU"] = c
    return out


def lid_open():
    for f in glob.glob("/proc/acpi/button/lid/*/state"):
        return "closed" not in (lc.read(f) or "")
    return True


class DttEngine:
    def __init__(self, log):
        self.log = log
        self.gddv = None
        self.dev = None
        self.error = None
        self.target = None
        self.range = None
        self.pl1 = None
        self.pl2 = None
        self.base = {}
        self.written = {}
        self.active = False
        self.last_check = {}
        self.tripped = {}
        self.time_state = {}
        self.last_energy = None
        self.power = None

    def load(self):
        try:
            self.gddv, self.dev = Gddv.load()
            if not self.gddv:
                self.error = "no DTT data vault in this BIOS"
            elif not self.gddv.targets or not self.gddv.conditions:
                self.error = "DTT tables have no adaptive targets"
        except (OSError, ParseError, lzma.LZMAError, struct.error) as e:
            self.gddv = None
            self.error = "could not parse DTT tables: %s" % e
        if self.error:
            self.log("dtt: %s" % self.error)
        else:
            self.log("dtt: %d targets, %d condition sets, PSVTs: %s" % (
                len(self.gddv.targets), len(self.gddv.conditions), ", ".join(sorted(self.gddv.psvts))))
        return self.error is None

    def oem(self, index):
        if index == 2:
            prof = lc.get_profile()
            if prof in PROFILE_OEM2:
                return PROFILE_OEM2[prof]
        return lc.read_int(os.path.join(self.dev, "odvp%d" % index), 0)

    def eval_condition(self, c, temps):
        cond = c["condition"]
        if cond == C_DEFAULT:
            return True
        if C_OEM0 <= cond <= C_OEM5:
            v = self.oem(cond - C_OEM0)
        elif OEM_BASE <= cond < SW_OEM_BASE:
            v = self.oem(cond - OEM_BASE + 6)
        elif cond == C_POWER_SOURCE:
            v = 1 if lc.ac_online() is False else 0
        elif cond == C_LID:
            v = 1 if lid_open() else 0
        elif cond in (C_TEMPERATURE, C_TEMP_NO_HYST, 0):
            t = temps.get(short_name(c["device"]))
            if t is None:
                return False
            v = int(round(t * 10 + 2732))
        else:
            return False
        ok = compare(c["comparison"], v, c["argument"])
        if ok and c["time"] and c["target"] != self.target:
            key = id(c)
            first = self.time_state.setdefault(key, time.monotonic())
            return compare(c["time_cmp"], int(time.monotonic() - first), c["time"])
        self.time_state.pop(id(c), None)
        return ok

    def match(self, temps):
        for cs in self.gddv.conditions:
            if cs and all(self.eval_condition(c, temps) for c in cs):
                return cs[0]["target"]
        return None

    def capture_base(self):
        self.base = {}
        for d in lc.RAPL_DOMAINS:
            pl1, pl2 = lc.rapl_limits(d)
            if pl1 and pl2:
                self.base[d] = (pl1, pl2)

    def write(self, pl1, pl2):
        for d in self.base:
            try:
                lc.set_rapl(d, int(pl1 * 1e6), int(pl2 * 1e6))
                self.written[d] = (int(pl1 * 1e6), int(pl2 * 1e6))
            except OSError as e:
                self.log("dtt rapl %s: %s" % (d, e))

    def restore(self):
        for d, (pl1, pl2) in self.base.items():
            try:
                lc.set_rapl(d, pl1, pl2)
            except OSError as e:
                self.log("dtt restore %s: %s" % (d, e))
        self.active = False
        self.target = None
        self.pl1 = None

    def measure_power(self, dt):
        e = lc.rapl_energy()
        if e is not None and self.last_energy is not None and dt > 0:
            d = e - self.last_energy
            if d < 0:
                d += lc.rapl_max_energy() or (1 << 32)
            self.power = d / 1e6 / dt
        self.last_energy = e

    def apply_target(self, tid):
        t = self.gddv.targets.get(tid)
        ppcc = self.gddv.ppcc.get("TCPU") or next(iter(self.gddv.ppcc.values()), None)
        lo = ppcc["min"] / 1000.0 if ppcc else None
        hi = ppcc["max"] / 1000.0 if ppcc else None
        pl2 = None
        psvt = None
        for a in (t["actions"] if t else []):
            try:
                if a["code"] == "PL1MAX":
                    hi = int(a["argument"]) / 1000.0
                elif a["code"] == "PL1MIN":
                    lo = int(a["argument"]) / 1000.0
                elif a["code"] == "PL2PowerLimit":
                    pl2 = int(a["argument"]) / 1000.0
                elif a["code"] == "PSVT":
                    psvt = a["argument"]
            except ValueError:
                pass
        if hi is None:
            hi = min(v[0] for v in self.base.values()) / 1e6
        if lo is None or lo > hi:
            lo = hi
        if pl2 is None:
            pl2 = min(v[1] for v in self.base.values()) / 1e6
        self.range = (lo, hi)
        self.pl2 = max(pl2, hi)
        self.psvt_name = psvt if psvt in self.gddv.psvts else ("Default" if "Default" in self.gddv.psvts else None)
        self.pl1 = hi if self.pl1 is None else min(max(self.pl1, lo), hi)
        self.tripped = {}
        self.last_check = {}
        self.target = tid
        self.target_name = t["name"] if t else "none"
        self.write(self.pl1, self.pl2)
        self.log("dtt: target %s (%s) PL1 %.0f-%.0f W PL2 %.0f W PSVT %s" % (
            tid, self.target_name, lo, hi, self.pl2, self.psvt_name))

    def trips(self):
        rows = self.gddv.psvts.get(self.psvt_name) or []
        out = {}
        for row in rows:
            key = row["target"]
            cur = out.get(key)
            if cur is None or row["temp"] < cur["temp"]:
                out[key] = row
        counts = {}
        for row in rows:
            counts[row["target"]] = counts.get(row["target"], 0) + 1
        res = []
        for key, row in out.items():
            r = dict(row)
            if counts[key] == 1 and str(r["limit"]).upper().startswith("MAX"):
                r["temp"] += 1.0
            res.append(r)
        return res

    def step(self, dt, status_extra=None):
        if not self.gddv:
            return
        if not self.base:
            self.capture_base()
        now = time.monotonic()
        self.measure_power(dt)
        temps = zone_temps()
        tid = self.match(temps)
        if tid is not None and tid != self.target:
            self.apply_target(tid)
        if self.target is None:
            return
        self.active = True
        lo, hi = self.range
        for row in self.trips():
            key = row["target"]
            if now - self.last_check.get(key, 0) < row["period"]:
                continue
            self.last_check[key] = now
            t = temps.get(key)
            if t is None:
                continue
            if t > row["temp"]:
                self.tripped[key] = True
            elif t < row["temp"] - HYSTERESIS_C:
                self.tripped[key] = False
        hot = [k for k, v in self.tripped.items() if v]
        old = self.pl1
        if hot:
            start = self.pl1
            if self.pl1 >= hi and self.power is not None:
                start = min(hi, max(lo, self.power))
            self.pl1 = max(lo, start - THROTTLE_STEP_W * dt)
        else:
            self.pl1 = min(hi, self.pl1 + RELAX_STEP_W * dt)
        if int(round(self.pl1)) != int(round(old)) or now - getattr(self, "last_write_t", 0) > 10:
            self.last_write_t = now
            self.write(round(self.pl1), self.pl2)
        self.hot = hot

    def status(self):
        if not self.gddv:
            return {"engine": "dtt", "active": False, "error": self.error}
        return {"engine": "dtt", "active": self.active, "target": self.target,
                "target_name": getattr(self, "target_name", None), "range": self.range, "pl1": self.pl1,
                "pl2": self.pl2, "psvt": getattr(self, "psvt_name", None), "hot": getattr(self, "hot", []),
                "trips": [(r["target"], r["temp"]) for r in self.trips()] if self.target is not None else [],
                "power": self.power}
