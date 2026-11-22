# Linux packaging files

Files the Linux package installs (see the multi-platform task file, phases 3 and 5):

| File | Goes to | What it does |
|---|---|---|
| `systemd/zapretgui-helper.service` | `/usr/lib/systemd/system/` | the root helper (firewall table `inet zapretgui` + `nfqws2`) |
| `init/zapretgui-helper.sysv`, `init/zapretgui-helper.openrc` | `/etc/init.d/zapretgui-helper` | the same helper for sysvinit (Devuan, Debian without systemd) and for OpenRC |
| `initsys.sh` | `/opt/zapretgui/` | finds the init system by itself (systemd, OpenRC, sysvinit, else "none": start the helper by hand) and installs, starts, stops, removes the service for it; used by `install.sh`, `uninstall.sh` and the .deb scripts |
| `zapretgui.desktop` | `/usr/share/applications/` | menu entry |
| `install.sh`, `uninstall.sh` | package root | install into `/opt/zapretgui` + system files, enable the helper; remove all of it (user data only with `--purge-user-data NAME`) |
| `polkit/50-zapretgui.rules` | `/usr/share/polkit-1/rules.d/` | group `zapretgui` may start/stop the helper unit without a password |

The package also creates the group `zapretgui` and adds the installing user to it; the window runs as that normal
user and talks to `/run/zapretgui/helper.sock` (`root:zapretgui 0660`). The helper removes its table on every way out;
the two `nft delete table` lines in the unit are the second net.

Running the helper from source on a development machine (nothing here is installed):

```
sudo ZAPRET_HELPER_PROGRAM_ROOT=/path/to/prepared/root PYTHONPATH=src python3 -m platforms.linux.helper
```

`install.sh` and `uninstall.sh` live here in the repository but sit at the root of the built package (they expect
`bin/ lua/ lists/ ... packaging/linux/...` next to them); the `.deb` does the same jobs in its maintainer scripts.

## Building the packages (Debian 13 build machine)

`private_zapretgui/build_zapret/linux/build_bundle.sh --dist <Nuitka main.dist> --engine <nfqws2> --out DIR` makes three files:

- `zapretgui_<version>_amd64.deb` (main package; xz, about 70 MB): `sudo apt install ./zapretgui_*.deb`. The helper service is started by the package, the person who ran sudo joins the group `zapretgui` (log in again).
- `zapretgui-<version>-linux-x86_64.tar.gz`: unpack, `sudo ./install.sh`.
- `zapretgui-<version>-x86_64.AppImage` (needs `DIR/appimagetool`, see `--appimagetool`): one file. The first start asks for the password once and installs the system part (`/opt/zapretgui`, helper service, group) with `install.sh`, then runs the installed program; a new AppImage version updates it the same way. `AppImage --install-system-part` does only the installation. Remove everything with `sudo /opt/zapretgui/uninstall.sh`.

The build machine decides the oldest system the result runs on (glibc of the build machine): a Debian 13 build does not start on Debian 12 or Ubuntu 24.04. Without FUSE (containers) run an AppImage with `APPIMAGE_EXTRACT_AND_RUN=1`.

## Other distributions

`install.sh` (and so the AppImage, which calls it) finds the package manager itself and installs the libraries with the names of that system: `apt` (Debian, Ubuntu, Devuan), `pacman` (Arch, Manjaro, EndeavourOS) and `dnf` (Fedora and relatives). The three name lists are `DEPENDENCIES` (the same as `Depends:` of the .deb), `ARCH_PACKAGES` and `FEDORA_PACKAGES`: add a library to all three. Tested in chroots of Arch and Fedora 45 (dependencies, package build, install, removal); SELinux on Fedora is not tested.

- `arch/PKGBUILD` + `arch/zapretgui.install`: a "-bin" package that unpacks the .deb of the same release. `cd packaging/linux/arch && updpkgsums && makepkg -si`. Set `pkgver` to the release.
- `rpm/zapretgui.spec`: the same for Fedora. Put the .deb into `~/rpmbuild/SOURCES/`, `rpmbuild -bb packaging/linux/rpm/zapretgui.spec`, `sudo dnf install ~/rpmbuild/RPMS/x86_64/zapretgui-*.rpm`.

Both packages carry the same files as the .deb, create the group `zapretgui` and need systemd (the OpenRC and sysvinit scripts are only used by the .deb and `install.sh`).
