#!/bin/bash
# Installs the files of a Linux build (the folder this script lives in is the unpacked package: bin/ lua/ lists/ ...).
#   sudo ./install.sh [--user NAME]     NAME gets into the group zapretgui (default: the user who ran sudo)
# Everything lands in /opt/zapretgui (program, read-only) and a few system files; the person's own data is created by the
# program in ~/.config, ~/.local/share, ~/.local/state, ~/.cache. uninstall.sh removes it all again.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run it with sudo" >&2; exit 1; }
# sudo and pkexec keep the umask of the person: with `umask 077` in their .bashrc /opt/zapretgui came out as rwx------, and the
# engine (it continues as that person, not as root) then could not even enter the folder ("exited at once with code 127").
umask 022
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
# The same libraries under the names of Arch (pacman) and Fedora (dnf). Arch: the xcb-util-* packages hold the libxcb-* helper
# libraries Qt's xcb plugin needs; Fedora names its libraries after the upstream project. A name that does not exist (a release
# that renamed it) is skipped by dnf (strict=0) and shows up in the log instead of stopping everything.
ARCH_PACKAGES="nftables luajit zlib libnetfilter_queue libnfnetlink libmnl libcap xcb-util-cursor libxkbcommon-x11 libglvnd fontconfig dbus libx11 libxcb xcb-util xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm libxkbcommon wayland glib2"
# Void Linux (xbps): LuaJIT is spelled in capitals there; libglvnd holds libEGL and libGL; wayland holds both wayland libraries;
# the names were looked up in the Void repository.
XBPS_PACKAGES="nftables LuaJIT zlib libnetfilter_queue libnfnetlink libmnl libcap xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm libxkbcommon libxkbcommon-x11 libglvnd wayland dbus fontconfig glib libX11 libxcb"
FEDORA_PACKAGES="nftables luajit zlib-ng-compat libnetfilter_queue libnfnetlink libmnl libcap xcb-util-cursor libxkbcommon-x11 libglvnd-egl libglvnd-glx fontconfig dbus-libs libX11 libX11-xcb libxcb xcb-util xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm libxkbcommon libwayland-client libwayland-cursor glib2"

# The package manager of this system (a test names one in ZG_PKG).
package_manager() {
  if [ -n "${ZG_PKG:-}" ]; then echo "$ZG_PKG"; return; fi
  if command -v apt-get >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1; then echo apt
  elif command -v pacman >/dev/null 2>&1; then echo pacman
  elif command -v dnf >/dev/null 2>&1; then echo dnf
  elif command -v xbps-install >/dev/null 2>&1; then echo xbps
  else echo none
  fi
}

install_dependencies() {
  manager="$(package_manager)"
  echo "package manager: $manager"
  missing=""
  case "$manager" in
    apt)
      for pkg in $DEPENDENCIES; do dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"; done
      [ -n "$missing" ] || return 0
      echo "Installing the libraries and tools Zapret needs:$missing"
      export DEBIAN_FRONTEND=noninteractive
      # shellcheck disable=SC2086
      apt-get install -y --no-install-recommends $missing || { apt-get update -qq && apt-get install -y --no-install-recommends $missing; } \
        || echo "warning: apt-get could not install:$missing (no network?)" ;;
    pacman)
      for pkg in $ARCH_PACKAGES; do pacman -Qq "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"; done
      [ -n "$missing" ] || return 0
      echo "Installing the libraries and tools Zapret needs:$missing"
      # --needed: nothing is reinstalled; no -y/-u: a partial upgrade (new libraries on an old system) is how Arch breaks
      # shellcheck disable=SC2086
      pacman -S --noconfirm --needed $missing || echo "warning: pacman could not install:$missing (run  sudo pacman -Syu  first, then start Zapret again)" ;;
    dnf)
      for pkg in $FEDORA_PACKAGES; do rpm -q "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"; done
      [ -n "$missing" ] || return 0
      echo "Installing the libraries and tools Zapret needs:$missing"
      # strict=0: a name this release of Fedora does not have is skipped (the log says which) instead of failing the lot
      # shellcheck disable=SC2086
      dnf install -y --setopt=strict=0 $missing || echo "warning: dnf could not install:$missing (no network?)" ;;
    xbps)
      for pkg in $XBPS_PACKAGES; do xbps-query "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"; done
      [ -n "$missing" ] || return 0
      echo "Installing the libraries and tools Zapret needs:$missing"
      # the package index may not be fetched yet on a fresh system: try without it first, then with it
      # shellcheck disable=SC2086
      xbps-install -y $missing || xbps-install -Sy $missing || echo "warning: xbps-install could not install:$missing (run  sudo xbps-install -Su  first, then start Zapret again)" ;;
    *)
      echo "note: no known package manager (apt, pacman, dnf, xbps): install the libraries of Zapret yourself if the window does not open" ;;
  esac
}
install_dependencies

command -v nft >/dev/null || { echo "missing: nft" >&2; exit 1; }
# The init system is found by itself (systemd, OpenRC, sysvinit); initsys.sh says what each one gets.
ZG_SRC="$HERE/packaging/linux"
# shellcheck disable=SC1091
. "$ZG_SRC/initsys.sh"
echo "init system: $(zg_init_kind)"

mkdir -p "$TARGET"
# the program folder is replaced as a whole: a half-updated tree must not stay
rm -rf "$TARGET.new" && mkdir "$TARGET.new"
for item in VERSION bin lua lists system presets windivert.filter _internal exe; do
  [ -e "$HERE/$item" ] && cp -a "$HERE/$item" "$TARGET.new/"
done
# uninstall.sh and the package scripts use the same init-system code and the init scripts from here
install -m 0644 "$HERE/packaging/linux/initsys.sh" "$TARGET.new/initsys.sh"
cp -a "$HERE/packaging/linux/init" "$TARGET.new/init"
rm -rf "$TARGET.old"; [ -d "$TARGET" ] && mv "$TARGET" "$TARGET.old"
mv "$TARGET.new" "$TARGET"; rm -rf "$TARGET.old"
# readable and enterable by everybody, writable by root only, whatever the source files or the umask were
# root owns every file: the AppImage unpacks and copies the program as the PERSON, and cp -a keeps that owner. The helper runs
# _internal/zapretgui as root, so a program folder that its user may rewrite would hand that user (or anything running as
# them) root.
chown -R root:root "$TARGET"
chmod -R go+rX,go-w "$TARGET"; chmod 755 "$TARGET" "$TARGET/bin/"* 2>/dev/null || true
# the way back stays with the program: sudo /opt/zapretgui/uninstall.sh
[ -f "$HERE/uninstall.sh" ] && install -m 0755 "$HERE/uninstall.sh" "$TARGET/uninstall.sh"

getent group zapretgui >/dev/null || groupadd --system zapretgui
[ -n "$WHO" ] && id "$WHO" >/dev/null 2>&1 && usermod -aG zapretgui "$WHO" && echo "added $WHO to the group zapretgui (log in again for it to apply)"

install -d /usr/share/polkit-1/rules.d && install -m 0644 "$HERE/packaging/linux/polkit/50-zapretgui.rules" /usr/share/polkit-1/rules.d/50-zapretgui.rules
install -d /usr/share/applications && install -m 0644 "$HERE/packaging/linux/zapretgui.desktop" /usr/share/applications/zapretgui.desktop
# icons are cosmetic: a minimal system (Void, a container) may not even have the folder, and that must not stop the install
# before the helper service is set up
if [ -d "$TARGET/system/hicolor" ]; then
  { install -d /usr/share/icons/hicolor && cp -a "$TARGET/system/hicolor/." /usr/share/icons/hicolor/; } || echo "warning: the icons were not installed"
fi
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q /usr/share/icons/hicolor || true

# the helper service in the words of the init system found here (/usr/lib/systemd/system/zapretgui-helper.service for systemd)
zg_service_install
zg_service_start
echo "Zapret is installed. Start it from the application menu."
