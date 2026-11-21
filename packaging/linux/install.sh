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
[ -z "$WHO" ] && [ -n "${PKEXEC_UID:-}" ] && WHO="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
if [ "${1:-}" = "--user" ]; then WHO="${2:?name}"; fi

# Everything the output of this script says also goes to a log the AppImage start shows when something fails: pkexec has no terminal.
mkdir -p /var/log && exec > >(tee -a /var/log/zapretgui-install.log) 2>&1
echo "== $(date '+%F %T') install.sh $*"

# The .deb pulls these in by itself; an AppImage does not, and a lean desktop (XFCE, LXQt) may lack some of them: without the
# window-system libraries the window never opens and nothing says why. The list is the Depends line of deb/control.in
# (a test keeps them equal).
DEPENDENCIES="nftables libluajit-5.1-2 zlib1g libnetfilter-queue1 libnfnetlink0 libmnl0 libcap2 libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 libfontconfig1 libdbus-1-3 libx11-6 libx11-xcb1 libxcb1 libxcb-glx0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render0 libxcb-render-util0 libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-util1 libxcb-xfixes0 libxcb-xkb1 libxkbcommon0 libwayland-client0 libwayland-cursor0 libglib2.0-0t64"
install_dependencies() {
  command -v apt-get >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1 || return 0
  missing=""
  for pkg in $DEPENDENCIES; do dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"; done
  [ -n "$missing" ] || return 0
  echo "Installing the libraries and tools Zapret needs:$missing"
  export DEBIAN_FRONTEND=noninteractive
  # shellcheck disable=SC2086
  apt-get install -y --no-install-recommends $missing || { apt-get update -qq && apt-get install -y --no-install-recommends $missing; } \
    || echo "warning: apt-get could not install:$missing (no network?)"
}
install_dependencies

for tool in nft systemctl; do command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }; done

mkdir -p "$TARGET"
# the program folder is replaced as a whole: a half-updated tree must not stay
rm -rf "$TARGET.new" && mkdir "$TARGET.new"
for item in VERSION bin lua lists system presets windivert.filter _internal exe; do
  [ -e "$HERE/$item" ] && cp -a "$HERE/$item" "$TARGET.new/"
done
rm -rf "$TARGET.old"; [ -d "$TARGET" ] && mv "$TARGET" "$TARGET.old"
mv "$TARGET.new" "$TARGET"; rm -rf "$TARGET.old"
chmod -R go-w "$TARGET"; chmod 755 "$TARGET/bin/"* 2>/dev/null || true
# the way back stays with the program: sudo /opt/zapretgui/uninstall.sh
[ -f "$HERE/uninstall.sh" ] && install -m 0755 "$HERE/uninstall.sh" "$TARGET/uninstall.sh"

getent group zapretgui >/dev/null || groupadd --system zapretgui
[ -n "$WHO" ] && id "$WHO" >/dev/null 2>&1 && usermod -aG zapretgui "$WHO" && echo "added $WHO to the group zapretgui (log in again for it to apply)"

install -m 0644 "$HERE/packaging/linux/systemd/zapretgui-helper.service" /usr/lib/systemd/system/zapretgui-helper.service
install -d /usr/share/polkit-1/rules.d && install -m 0644 "$HERE/packaging/linux/polkit/50-zapretgui.rules" /usr/share/polkit-1/rules.d/50-zapretgui.rules
install -d /usr/share/applications && install -m 0644 "$HERE/packaging/linux/zapretgui.desktop" /usr/share/applications/zapretgui.desktop
[ -d "$TARGET/system/hicolor" ] && cp -a "$TARGET/system/hicolor/." /usr/share/icons/hicolor/
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true

systemctl daemon-reload
systemctl enable --now zapretgui-helper.service
echo "Zapret is installed. Start it from the application menu."
