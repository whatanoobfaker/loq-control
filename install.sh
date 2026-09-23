#!/bin/sh
set -e
[ "$(id -u)" = 0 ] || exec pkexec "$(readlink -f "$0")" "$@"
cd "$(dirname "$(readlink -f "$0")")"
V=1.1

for old in $(dkms status 2>/dev/null | awk -F'[/,:]' '/^lenovolegionlinux\//{print $2}' | sort -u); do
	dkms remove -m lenovolegionlinux -v "$old" --all || true
done
for o in $(dkms status 2>/dev/null | awk -F"[/,:]" "/^loq-legion\//{print \$2}" | sort -u); do dkms remove -m loq-legion -v "$o" --all; done >/dev/null 2>&1 || true
rm -rf /usr/src/loq-legion-$V
install -d /usr/src/loq-legion-$V
install -m644 driver/legion-laptop.c driver/Makefile driver/dkms.conf /usr/src/loq-legion-$V/
dkms add -m loq-legion -v $V
dkms install -m loq-legion -v $V -k "$(uname -r)"
install -Dm644 system/loq-legion.conf /etc/modules-load.d/loq-legion.conf

install -d /usr/lib/loq-control /usr/share/loq-control
install -m755 src/loqd src/loq-control /usr/lib/loq-control/
install -m644 src/loqcommon.py /usr/lib/loq-control/
ln -sf /usr/lib/loq-control/loq-control /usr/bin/loq-control
install -Dm644 system/loq-control.desktop /usr/share/applications/loq-control.desktop
install -Dm644 system/loq-control-autostart.desktop /usr/share/loq-control/loq-control-autostart.desktop
install -Dm644 system/loqd.service /etc/systemd/system/loqd.service
install -Dm755 system/loq-control.sleep /usr/lib/systemd/system-sleep/loq-control

rmmod legion_laptop 2>/dev/null || true
modprobe legion_laptop
systemctl daemon-reload
systemctl enable loqd.service
systemctl restart loqd.service
echo "LOQ Control installed."
