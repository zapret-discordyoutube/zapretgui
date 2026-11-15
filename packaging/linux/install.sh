#!/bin/bash
# Installs the files of a Linux build (the folder this script lives in is the unpacked package: bin/ lua/ lists/ ...).
#   sudo ./install.sh [--user NAME]     NAME gets into the group zapretgui (default: the user who ran sudo)
# Everything lands in /opt/zapretgui (program, read-only) and a few system files; the person's own data is created by the
# program in ~/.config, ~/.local/share, ~/.local/state, ~/.cache. uninstall.sh removes it all again.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run it with sudo" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET=/opt/zapretgui
WHO="${SUDO_USER:-}"
if [ "${1:-}" = "--user" ]; then WHO="${2:?name}"; fi

for tool in nft systemctl; do command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }; done

mkdir -p "$TARGET"
# the program folder is replaced as a whole: a half-updated tree must not stay
rm -rf "$TARGET.new" && mkdir "$TARGET.new"
for item in bin lua lists system presets windivert.filter _internal exe; do
  [ -e "$HERE/$item" ] && cp -a "$HERE/$item" "$TARGET.new/"
done
rm -rf "$TARGET.old"; [ -d "$TARGET" ] && mv "$TARGET" "$TARGET.old"
mv "$TARGET.new" "$TARGET"; rm -rf "$TARGET.old"
chmod -R go-w "$TARGET"; chmod 755 "$TARGET/bin/"* 2>/dev/null || true

getent group zapretgui >/dev/null || groupadd --system zapretgui
[ -n "$WHO" ] && id "$WHO" >/dev/null 2>&1 && usermod -aG zapretgui "$WHO" && echo "added $WHO to the group zapretgui (log in again for it to apply)"

install -m 0644 "$HERE/packaging/linux/systemd/zapretgui-helper.service" /usr/lib/systemd/system/zapretgui-helper.service
install -d /usr/share/polkit-1/rules.d && install -m 0644 "$HERE/packaging/linux/polkit/50-zapretgui.rules" /usr/share/polkit-1/rules.d/50-zapretgui.rules
install -d /usr/share/applications && install -m 0644 "$HERE/packaging/linux/zapretgui.desktop" /usr/share/applications/zapretgui.desktop
install -d /usr/share/mime/packages && install -m 0644 "$HERE/packaging/linux/zapretgui-preset.xml" /usr/share/mime/packages/zapretgui-preset.xml
[ -f "$TARGET/system/ico/Zapret2.png" ] && install -D -m 0644 "$TARGET/system/ico/Zapret2.png" /usr/share/icons/hicolor/256x256/apps/zapretgui.png
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v update-mime-database >/dev/null && update-mime-database /usr/share/mime || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true

systemctl daemon-reload
systemctl enable --now zapretgui-helper.service
echo "Zapret is installed. Start it from the application menu."
