import glob
import json
import os
import pwd
import grp

LEGION = "/sys/bus/platform/devices/legion"
IDEAPAD = "/sys/bus/platform/devices/VPC2004:00"
FWA = "/sys/class/firmware-attributes/lenovo-wmi-other-0/attributes"
RAPL = "/sys/class/powercap/intel-rapl:0"
LEGACY_PROFILE = "/sys/firmware/acpi/platform_profile"
STATE_DIR = "/var/lib/loq-control"
CONFIG = os.path.join(STATE_DIR, "config.json")
FIRMWARE_CURVES = os.path.join(STATE_DIR, "firmware-curves.json")
RUN_DIR = "/run/loq-control"
GROUP = "wheel"
MAX_RPM = 4500
CURVE_POINTS = 10

PROFILE_LABELS = {
    "low-power": "Quiet",
    "quiet": "Quiet",
    "balanced": "Balanced",
    "performance": "Performance",
    "max-power": "Extreme",
    "custom": "Custom",
}

LIMIT_ATTRS = [
    ("cpu_temp", "CPU temperature target", "°C"),
    ("ppt_pl1_spl", "CPU sustained power (PL1)", "W"),
    ("ppt_pl2_sppt", "CPU burst power (PL2)", "W"),
    ("ppt_pl1_tau", "CPU burst duration", "s"),
    ("ppt_cpu_cl", "CPU power when GPU is loaded", "W"),
    ("gpu_temp", "GPU temperature limit", "°C"),
    ("gpu_nv_ctgp", "GPU configurable TGP", "W"),
    ("gpu_nv_ppab", "GPU Dynamic Boost", "W"),
    ("gpu_nv_ac_offset", "GPU total power offset", "W"),
]

TOGGLES = [
    ("conservation_mode", IDEAPAD, "Battery conservation", "Stop charging at about 80% to extend battery life", "battery"),
    ("rapidcharge", LEGION, "Rapid charge", "Charge the battery faster", "battery"),
    ("usb_charging", IDEAPAD, "Always-on USB", "Charge USB devices while the laptop sleeps", "battery"),
    ("fn_lock", IDEAPAD, "Fn lock", "Use F1–F12 without holding Fn", "input"),
    ("winkey", LEGION, "Windows key", "Enable the Windows/Super key", "input"),
    ("touchpad", LEGION, "Touchpad", "Enable the touchpad", "input"),
    ("overdrive", LEGION, "Display overdrive", "Faster pixel response on the internal panel", "display"),
    ("gsync", LEGION, "G-Sync", "Variable refresh on the internal panel", "display"),
    ("camera_power", IDEAPAD, "Camera", "Power the webcam", "display"),
]

EXCLUSIVE = {"conservation_mode": "rapidcharge", "rapidcharge": "conservation_mode"}


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path, default=None):
    v = read(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def profile_path():
    for d in glob.glob("/sys/class/platform-profile/platform-profile-*"):
        if read(os.path.join(d, "name")) == "lenovo-wmi-gamezone":
            return os.path.join(d, "profile")
    for d in glob.glob("/sys/class/platform-profile/platform-profile-*"):
        return os.path.join(d, "profile")
    return LEGACY_PROFILE


def profile_choices():
    p = profile_path()
    c = read(os.path.join(os.path.dirname(p), "choices")) or read(LEGACY_PROFILE + "_choices") or ""
    return c.split()


def get_profile():
    return read(profile_path())


def set_profile(name):
    write(profile_path(), name)


def hwmon(name):
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if read(os.path.join(d, "name")) == name:
            return d
    return None


def legion_hwmon():
    return hwmon("legion_hwmon")


def read_curve():
    raw = read(os.path.join(LEGION, "fancurve"))
    if not raw:
        return None
    pts = []
    for line in raw.splitlines():
        cpu, gpu, r1, r2 = (int(x) for x in line.split())
        pts.append([cpu, gpu, r1, r2])
    return pts


def curve_text(points):
    return "".join("%d %d %d %d\n" % tuple(p) for p in points)


def write_curve(points):
    write(os.path.join(LEGION, "fancurve"), curve_text(points))


def validate_curve(points):
    if len(points) != CURVE_POINTS:
        return "The curve needs %d points" % CURVE_POINTS
    if points[0][2] or points[0][3]:
        return "The first point must be 0 RPM"
    pc, pr = 0, 0
    for i, (cpu, gpu, r1, r2) in enumerate(points):
        if not (0 <= cpu <= 127 and 0 <= gpu <= 127):
            return "Point %d: temperature out of range" % (i + 1)
        if not (0 <= r1 <= MAX_RPM and 0 <= r2 <= MAX_RPM):
            return "Point %d: speed must be 0–%d RPM" % (i + 1, MAX_RPM)
        if cpu < pc:
            return "Point %d: CPU temperature must not decrease" % (i + 1)
        if max(r1, r2) < pr:
            return "Point %d: speed must not decrease" % (i + 1)
        pc, pr = cpu, max(r1, r2)
    return None


def limit_info(name):
    d = os.path.join(FWA, name)
    if not os.path.isdir(d):
        return None
    return {
        "value": read_int(os.path.join(d, "current_value")),
        "min": read_int(os.path.join(d, "min_value")),
        "max": read_int(os.path.join(d, "max_value")),
        "default": read_int(os.path.join(d, "default_value")),
        "step": read_int(os.path.join(d, "scalar_increment"), 1) or 1,
        "desc": read(os.path.join(d, "display_name"), name),
    }


def set_limit(name, value):
    write(os.path.join(FWA, name, "current_value"), int(value))


def toggle_path(name):
    for n, base, *_ in TOGGLES:
        if n == name:
            return os.path.join(base, n)
    return None


def leds():
    out = []
    for name, label in (("platform::kbd_backlight", "Keyboard backlight"),
                        ("platform::ylogo", "Lid logo light"),
                        ("platform::ioport", "Rear port light")):
        d = os.path.join("/sys/class/leds", name)
        if os.path.isdir(d):
            out.append((name, label, d))
    return out


def default_config():
    return {
        "restore_profile": False,
        "profile": None,
        "curves": {},
        "limits": {},
        "toggles": {},
        "leds": {},
    }


def load_json(path, default):
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(default, dict):
            merged = dict(default)
            merged.update(data)
            return merged
        return data
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.chmod(tmp, 0o664)
    if os.geteuid() == 0:
        os.chown(tmp, 0, grp.getgrnam(GROUP).gr_gid)
    os.replace(tmp, path)


def load_config():
    return load_json(CONFIG, default_config())


def save_config(cfg):
    save_json(CONFIG, cfg)


def load_firmware_curves():
    return load_json(FIRMWARE_CURVES, {})


def rapl_energy():
    return read_int(os.path.join(RAPL, "energy_uj"))


def rapl_max_energy():
    return read_int(os.path.join(RAPL, "max_energy_range_uj"))


def cpu_temp():
    d = hwmon("coretemp")
    if d:
        for f in sorted(glob.glob(os.path.join(d, "temp*_label"))):
            if read(f, "").startswith("Package"):
                v = read_int(f.replace("_label", "_input"))
                if v is not None:
                    return v / 1000
    d = legion_hwmon()
    v = read_int(os.path.join(d, "temp1_input")) if d else None
    return v / 1000 if v is not None else None


def gpu_temp():
    d = legion_hwmon()
    v = read_int(os.path.join(d, "temp2_input")) if d else None
    return v / 1000 if v else None


def fan_rpms():
    d = legion_hwmon()
    if not d:
        return None, None
    return read_int(os.path.join(d, "fan1_input")), read_int(os.path.join(d, "fan2_input"))


def cpu_freqs():
    out = []
    for f in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq")):
        v = read_int(f)
        if v:
            out.append(v / 1000)
    return out


def battery():
    for d in glob.glob("/sys/class/power_supply/BAT*"):
        cap = read_int(os.path.join(d, "capacity"))
        status = read(os.path.join(d, "status"), "")
        p = read_int(os.path.join(d, "power_now"))
        if p is None:
            c, v = read_int(os.path.join(d, "current_now")), read_int(os.path.join(d, "voltage_now"))
            p = c * v // 1000000 if c and v else None
        return {"capacity": cap, "status": status, "watts": p / 1e6 if p else 0.0}
    return None


def ac_online():
    for d in glob.glob("/sys/class/power_supply/*"):
        if read(os.path.join(d, "type")) == "Mains":
            return read(os.path.join(d, "online")) == "1"
    return None


def controlled_paths():
    paths = [os.path.join(LEGION, "fancurve"), profile_path(), LEGACY_PROFILE,
             os.path.join(RAPL, "energy_uj")]
    paths += [os.path.join(base, n) for n, base, *_ in TOGGLES]
    paths += glob.glob(os.path.join(FWA, "*", "current_value"))
    paths += [os.path.join(d, "brightness") for _, _, d in leds()]
    return [p for p in paths if os.path.exists(p)]


def group_name():
    return GROUP


def user_in_group():
    try:
        g = grp.getgrnam(GROUP)
    except KeyError:
        return False
    user = pwd.getpwuid(os.getuid()).pw_name
    return user in g.gr_mem or os.getgid() == g.gr_gid or g.gr_gid in os.getgroups()
