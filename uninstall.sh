#!/bin/sh
[ "$(id -u)" = 0 ] || exec pkexec "$(readlink -f "$0")" "$@"
systemctl disable --now loqd.service 2>/dev/null
rm -f /etc/systemd/system/loqd.service /usr/lib/systemd/system-sleep/loq-control
rm -f /usr/bin/loq-control /usr/share/applications/loq-control.desktop
rm -rf /usr/lib/loq-control /usr/share/loq-control
rm -f /etc/modules-load.d/loq-legion.conf
rmmod legion_laptop 2>/dev/null
dkms remove -m loq-legion -v 1.0 --all 2>/dev/null
rm -rf /usr/src/loq-legion-1.0
systemctl daemon-reload
echo "LOQ Control removed. Settings kept in /var/lib/loq-control."
