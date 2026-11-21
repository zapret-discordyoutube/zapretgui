#!/bin/bash
# Removes what install.sh put on the system, the firewall table included. The people's own data
# (~/.config/zapretgui, ~/.local/share/zapretgui, ...) stays unless --purge-user-data NAME is given.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run it with sudo" >&2; exit 1; }

systemctl disable --now zapretgui-helper.service 2>/dev/null || true
# the helper removes its table when it stops; this is the second net
nft delete table inet zapretgui 2>/dev/null || true
pkill -x nfqws2 2>/dev/null || true

rm -f /usr/lib/systemd/system/zapretgui-helper.service /usr/share/polkit-1/rules.d/50-zapretgui.rules \
      /usr/share/applications/zapretgui.desktop
find /usr/share/icons/hicolor -name 'zapretgui.*' -delete 2>/dev/null || true
rm -rf /opt/zapretgui /run/zapretgui
systemctl daemon-reload
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true

getent group zapretgui >/dev/null && groupdel zapretgui 2>/dev/null || true
# the copy of /etc/hosts from before the first change made by the program: removed with the program, unless /etc/hosts
# still differs from it (then it is the only way back, and it stays)
if [ -f /var/lib/zapretgui/hosts.original ] && cmp -s /var/lib/zapretgui/hosts.original /etc/hosts; then
  rm -rf /var/lib/zapretgui
fi

if [ "${1:-}" = "--purge-user-data" ]; then
  home="$(getent passwd "${2:?user name}" | cut -d: -f6)"
  rm -rf "$home/.config/zapretgui" "$home/.local/share/zapretgui" "$home/.local/state/zapretgui" "$home/.cache/zapretgui" \
         "$home/.config/autostart/zapretgui.desktop"
  echo "the data of ${2} was removed"
fi
echo "Zapret is removed."
