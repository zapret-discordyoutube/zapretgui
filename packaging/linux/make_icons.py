"""The Linux icon of Zapret: the same mascot picture the Windows program uses, reddish for Dev builds and blue for Stable builds.

    python3 make_icons.py <dev|stable> <ico-dir> <out-dir>

- ``<ico-dir>`` (the program's ``system/ico``): holds ``Zapret2.ico`` (blue, Stable) and ``ZapretDevLogo4.ico`` (reddish, Dev), the
  two files the window and the tray pick by the build channel (``app/app_icon_resources.py``). They are only READ here, so the
  window and the tray show exactly what Windows shows. ``Zapret2.png`` (256 px, picture of this build's channel) is written next to
  them for the AppImage.
- ``<out-dir>``: an icon theme tree ``hicolor/<size>x<size>/apps/zapretgui.png`` made from the same picture, for the menu, the
  desktop shortcut, the .deb, the AppImage and install.sh.

History: Dev 285-302 shipped a red/blue prohibition sign drawn here; the owner wanted the Windows mascot (2026-11-22).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SOURCE = {"dev": "ZapretDevLogo4.ico", "stable": "Zapret2.ico"}
SIZES = (16, 22, 24, 32, 48, 64, 96, 128, 256)  # the picture has 256 px: no larger size is made up from it


def load(ico_dir: Path, channel: str) -> Image.Image:
    """The largest frame of the channel's .ico, as RGBA."""
    path = ico_dir / SOURCE["dev" if channel == "dev" else "stable"]
    picture = Image.open(path)
    picture.size = max(picture.info.get("sizes") or {picture.size})
    picture.load()
    return picture.convert("RGBA")


def make(channel: str, ico_dir: Path, out_dir: Path) -> None:
    source = load(ico_dir, channel)
    source.resize((256, 256), Image.LANCZOS).save(ico_dir / "Zapret2.png")
    for size in SIZES:
        target = out_dir / "hicolor" / f"{size}x{size}" / "apps"
        target.mkdir(parents=True, exist_ok=True)
        source.resize((size, size), Image.LANCZOS).save(target / "zapretgui.png")


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in SOURCE:
        print(__doc__, file=sys.stderr)
        return 2
    make(argv[0], Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
