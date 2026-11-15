# Linux packaging files

Files the Linux package installs (see the multi-platform task file, phases 3 and 5):

| File | Goes to | What it does |
|---|---|---|
| `systemd/zapretgui-helper.service` | `/usr/lib/systemd/system/` | the root helper (firewall table `inet zapretgui` + `nfqws2`) |
| `polkit/50-zapretgui.rules` | `/usr/share/polkit-1/rules.d/` | group `zapretgui` may start/stop the helper unit without a password |

The package also creates the group `zapretgui` and adds the installing user to it; the window runs as that normal
user and talks to `/run/zapretgui/helper.sock` (`root:zapretgui 0660`). The helper removes its table on every way out;
the two `nft delete table` lines in the unit are the second net.

Running the helper from source on a development machine (nothing here is installed):

```
sudo ZAPRET_HELPER_PROGRAM_ROOT=/path/to/prepared/root PYTHONPATH=src python3 -m platforms.linux.helper
```
