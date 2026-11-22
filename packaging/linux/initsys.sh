# The init system of the machine, found by itself: the helper service (the small root program that owns the firewall table and
# the engine) has to be started at boot by whatever runs the machine. Sourced (POSIX sh) by install.sh, uninstall.sh and the
# .deb scripts; it changes nothing until a zg_service_* function is called.
#
#   systemd   a unit (/usr/lib/systemd/system/zapretgui-helper.service), with the sandbox of the unit
#   openrc    /etc/init.d/zapretgui-helper (an openrc-run script): Devuan with OpenRC, Alpine, Gentoo, Artix
#   sysv      /etc/init.d/zapretgui-helper (an LSB script): Devuan and Debian with sysvinit
#   none      runit, s6, dinit, a container without an init: nothing is installed; the person starts the helper by hand
#
# ZG_INIT forces the choice (systemd | openrc | sysv | none), ZG_SRC is the folder with init/ (and systemd/) to install from,
# ZG_INIT_DIR is /etc/init.d (a test points it elsewhere).

ZG_PREFIX="${ZG_PREFIX:-/opt/zapretgui}"
ZG_SRC="${ZG_SRC:-/opt/zapretgui}"
ZG_INIT_DIR="${ZG_INIT_DIR:-/etc/init.d}"
ZG_UNIT_DIR="${ZG_UNIT_DIR:-/usr/lib/systemd/system}"
ZG_SERVICE=zapretgui-helper

zg_init_kind() {
  if [ -n "${ZG_INIT:-}" ]; then echo "$ZG_INIT"; return 0; fi
  # a running systemd creates this folder; a chroot or a container image that merely contains systemctl does not
  if [ -d /run/systemd/system ]; then echo systemd; return 0; fi
  init_name="$(cat /proc/1/comm 2>/dev/null)"
  case "$init_name" in
    runit|runit-init|s6-svscan|s6-linux-init|dinit|finit) echo none; return 0 ;;
    openrc-init) echo openrc; return 0 ;;
  esac
  # OpenRC started by sysvinit (Devuan's "openrc" choice and Alpine) leaves its state folder behind
  if [ -e /run/openrc/softlevel ] && command -v rc-service >/dev/null 2>&1; then echo openrc; return 0; fi
  if [ -d "$ZG_INIT_DIR" ] && command -v start-stop-daemon >/dev/null 2>&1; then echo sysv; return 0; fi
  echo none
}

zg_say() { echo "zapretgui: $*"; }

# Puts the service description where the init system reads it. The unit of systemd is a package file in the .deb (ZG_SRC has no
# systemd/ there), and a file copy of install.sh and the AppImage.
zg_service_install() {
  kind="$(zg_init_kind)"
  case "$kind" in
    systemd)
      if [ -f "$ZG_SRC/systemd/$ZG_SERVICE.service" ]; then
        install -d "$ZG_UNIT_DIR" && install -m 0644 "$ZG_SRC/systemd/$ZG_SERVICE.service" "$ZG_UNIT_DIR/$ZG_SERVICE.service"
      fi
      systemctl daemon-reload || true ;;
    openrc)
      install -d "$ZG_INIT_DIR" && install -m 0755 "$ZG_SRC/init/$ZG_SERVICE.openrc" "$ZG_INIT_DIR/$ZG_SERVICE" ;;
    sysv)
      install -d "$ZG_INIT_DIR" && install -m 0755 "$ZG_SRC/init/$ZG_SERVICE.sysv" "$ZG_INIT_DIR/$ZG_SERVICE" ;;
    *)
      zg_say "no supported init system was found (systemd, OpenRC, sysvinit): start the helper by hand with  sudo /opt/zapretgui/bin/zapretgui-helper" ;;
  esac
}

# Enables the service for the next boots and (re)starts it now.
zg_service_start() {
  case "$(zg_init_kind)" in
    systemd) systemctl enable --now zapretgui-helper.service && systemctl restart zapretgui-helper.service ;;
    openrc)
      rc-update add "$ZG_SERVICE" default >/dev/null 2>&1 || true
      rc-service "$ZG_SERVICE" restart ;;
    sysv)
      update-rc.d "$ZG_SERVICE" defaults >/dev/null 2>&1 || true
      "$ZG_INIT_DIR/$ZG_SERVICE" restart ;;
    *)
      # runit, s6, dinit, a container: nothing starts it at boot, so it is started now, for this session. After a reboot the
      # AppImage start (or the same command) starts it again.
      if [ ! -S "${ZG_SOCKET:-/run/zapretgui/helper.sock}" ] && [ -x "$ZG_PREFIX/bin/zapretgui-helper" ]; then
        (setsid "$ZG_PREFIX/bin/zapretgui-helper" >>/var/log/zapretgui-helper.log 2>&1 &)
        zg_say "no supported init system: the helper runs for this session only; after a reboot start it with  sudo $ZG_PREFIX/bin/zapretgui-helper"
      fi ;;
  esac
}

# Stops it now; with "disable" it also stays off at the next boots.
zg_service_stop() {
  case "$(zg_init_kind)" in
    systemd)
      systemctl stop zapretgui-helper.service 2>/dev/null || true
      [ "${1:-}" = disable ] && { systemctl disable zapretgui-helper.service 2>/dev/null || true; } ;;
    openrc)
      rc-service "$ZG_SERVICE" stop 2>/dev/null || true
      [ "${1:-}" = disable ] && { rc-update del "$ZG_SERVICE" default >/dev/null 2>&1 || true; } ;;
    sysv)
      [ -x "$ZG_INIT_DIR/$ZG_SERVICE" ] && { "$ZG_INIT_DIR/$ZG_SERVICE" stop 2>/dev/null || true; }
      [ "${1:-}" = disable ] && { update-rc.d -f "$ZG_SERVICE" remove >/dev/null 2>&1 || true; } ;;
    *) pkill -f -- "--zapret-helper" 2>/dev/null || true ;;
  esac
  return 0
}

# Takes the service description away again (the init scripts; a systemd unit is removed by the package or by uninstall.sh).
zg_service_remove() {
  zg_service_stop disable
  rm -f "$ZG_INIT_DIR/$ZG_SERVICE"
  return 0
}

# One command a person can type when the helper is down, in the words of the init system found here.
zg_start_hint() {
  case "$(zg_init_kind)" in
    systemd) echo "sudo systemctl start zapretgui-helper" ;;
    openrc) echo "sudo rc-service zapretgui-helper start" ;;
    sysv) echo "sudo service zapretgui-helper start" ;;
    *) echo "sudo /opt/zapretgui/bin/zapretgui-helper" ;;
  esac
}
