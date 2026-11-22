# Zapret for Fedora (and other RPM systems with systemd): the Linux release repackaged as an .rpm. The release carries the
# files for Debian-like systems; this unpacks the .deb of the same release and installs its files, so every system file
# (service, polkit rule, menu entry, icons) is the same as everywhere.
#
#   cp zapretgui_21.1.7.305_amd64.deb ~/rpmbuild/SOURCES/        # the .deb of the release, same version as below
#   rpmbuild -bb packaging/linux/rpm/zapretgui.spec
#   sudo dnf install ~/rpmbuild/RPMS/x86_64/zapretgui-*.rpm
#
# It is a prebuilt compiled bundle: the stripping, byte-compiling and dependency scanning of rpmbuild are switched off.
%global debug_package %{nil}
%global __os_install_post %{nil}
%global __requires_exclude_from ^/opt/zapretgui/.*$
%global __provides_exclude_from ^/opt/zapretgui/.*$
AutoReqProv: no

Name:           zapretgui
Version:        21.1.7.305
Release:        1%{?dist}
Summary:        Zapret: bypass of network blocks with a window (nfqws2 + nftables)
License:        Proprietary
URL:            https://wiki.zapret.moe/
Source0:        zapretgui_%{version}_amd64.deb
ExclusiveArch:  x86_64
BuildRequires:  binutils
BuildRequires:  tar
BuildRequires:  xz
BuildRequires:  systemd-rpm-macros
Requires:       glibc >= 2.38
Requires:       nftables
Requires:       luajit
Requires:       libnetfilter_queue
Requires:       libnfnetlink
Requires:       libmnl
Requires:       libcap
Requires:       xcb-util-cursor
Requires:       libxkbcommon-x11
Requires:       libglvnd-egl
Requires:       libglvnd-glx
Requires:       fontconfig
Requires:       dbus-libs
Requires:       libX11
Requires:       libX11-xcb
Requires:       libxcb
Requires:       xcb-util
Requires:       xcb-util-image
Requires:       xcb-util-keysyms
Requires:       xcb-util-renderutil
Requires:       xcb-util-wm
Requires:       libxkbcommon
Requires:       libwayland-client
Requires:       libwayland-cursor
Requires:       glib2
Requires:       shadow-utils
Recommends:     polkit
Recommends:     libnotify
Recommends:     xdg-utils
%{?systemd_requires}

%description
Zapret with a window: presets, strategies, a tray icon, DNS settings and checks on top of zapret2 (nfqws2). The window runs as
the normal user; a small root service (zapretgui-helper) owns the firewall table "inet zapretgui" and the engine.

%prep
# nothing to unpack here: the .deb is unpacked in %%install

%build

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}
cd %{_builddir}
ar x %{SOURCE0} data.tar.xz
tar -xf data.tar.xz -C %{buildroot}
rm -f data.tar.xz

%pre
getent group zapretgui >/dev/null || groupadd -r zapretgui
exit 0

%post
%systemd_post zapretgui-helper.service
echo "Zapret: start the helper and add yourself to its group:"
echo "    sudo systemctl enable --now zapretgui-helper.service"
echo "    sudo usermod -aG zapretgui \$USER"
exit 0

%preun
%systemd_preun zapretgui-helper.service
if [ "$1" = 0 ]; then
  # the helper removes its table when it stops; this is the second net, so no rule outlives the program
  command -v nft >/dev/null 2>&1 && nft delete table inet zapretgui 2>/dev/null
fi
exit 0

%postun
%systemd_postun_with_restart zapretgui-helper.service
if [ "$1" = 0 ]; then getent group zapretgui >/dev/null && groupdel zapretgui 2>/dev/null; fi
exit 0

%files
/opt/zapretgui
/usr/bin/zapretgui
/usr/lib/systemd/system/zapretgui-helper.service
/usr/share/polkit-1/rules.d/50-zapretgui.rules
/usr/share/applications/zapretgui.desktop
/usr/share/icons/hicolor/*/apps/zapretgui.*

%changelog
* Sun Nov 22 2026 Zapret <noreply@zapret.moe> - 21.1.7.305-1
- the Linux release repackaged as an rpm
