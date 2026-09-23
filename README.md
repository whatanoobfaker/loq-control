# LOQ Control

Fan curve, power limit and device control for the **Lenovo LOQ 15IRX9** (BIOS `NECN`) on Linux, in one app.

- **Dashboard**: CPU/GPU temperature, fan speeds, CPU package power and limits, clocks, battery, live charts.
- **Power**: firmware power modes (Quiet, Balanced, Performance, Extreme, Custom) and Custom-mode limits:
  CPU temperature target, PL1/PL2, burst duration, cross-load limit, GPU cTGP / Dynamic Boost / temperature limit.
- **Fans**: per-mode fan curve editor (drag the points or edit the table) with presets. The firmware resets the
  curve on every mode change; `loqd` puts yours back.
- **Device**: battery conservation, rapid charge, Fn lock, Windows key, touchpad, display overdrive, G-Sync,
  camera, keyboard backlight and lighting.
- **Tray icon** with live CPU temperature, quick mode switching and a maximum-fans toggle.

## Components

| Path | What |
|---|---|
| `driver/` | `legion_laptop` kernel module, installed through DKMS |
| `src/loqd` | root service: grants the `wheel` group access to the controls, restores settings at boot and resume, re-applies the fan curve after mode changes |
| `src/loq-control` | PySide6 app |
| `src/loqcommon.py` | shared hardware layer |
| `system/` | systemd unit, sleep hook, module autoload, desktop entries |

Power modes and limits go through the kernel's `lenovo-wmi-gamezone` / `lenovo-wmi-other` drivers
(Linux 6.17+). Fans, EC sensors and the device toggles go through `legion_laptop`.

## Install

Requires `dkms`, kernel headers, `python-pyside6`, and membership in the `wheel` group.

```sh
./install.sh
```

`./uninstall.sh` removes everything except the saved settings in `/var/lib/loq-control`.

## Driver changes from LenovoLegionLinux

The driver is based on [LenovoLegionLinux](https://github.com/johnfanv2/LenovoLegionLinux) by johnfanv2 and contributors.

- Builds on Linux 7.1 (new `platform_profile` and `platform_driver` APIs). Its own platform-profile support is
  dropped in favour of the in-kernel Lenovo drivers.
- Creates its own platform device bound to the EC's ACPI node, because `acpi-ec` now owns `PNP0C09:00`.
- New `fancurve` sysfs file: reads and writes the whole 10-point curve atomically
  (`cpu_temp gpu_temp fan1_rpm fan2_rpm` per line), validated before it reaches the EC.
- Fan-curve writes return real errors instead of reporting success, and are verified by read-back.

## License

GPL-2.0-or-later, see `LICENSE`. The driver is derived from LenovoLegionLinux (GPL-2.0-or-later).
