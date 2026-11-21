"""The Linux icon of Zapret: a prohibition sign ("запрет"), red for Dev builds and blue for Stable builds.

    python3 make_icons.py <dev|stable> <ico-dir> <out-dir>

- ``<ico-dir>`` (the program's ``system/ico``): ``Zapret2.ico`` (blue) and ``ZapretDevLogo4.ico`` (red), the two files the window
  and the tray pick by the build channel (``app/app_icon_resources.py``), and ``Zapret2.png`` (256 px, colour of this build).
- ``<out-dir>``: an icon theme tree ``hicolor/<size>x<size>/apps/zapretgui.png`` and ``hicolor/scalable/apps/zapretgui.svg`` in the
  colour of this build, for the .deb, the AppImage and install.sh.

The picture is drawn from numbers, not copied from a file, so every size is sharp and the colours are exact.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

COLORS = {"dev": "#E53935", "stable": "#1E88E5"}
SIZES = (16, 22, 24, 32, 48, 64, 96, 128, 256, 512)
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

# in a 256 x 256 picture: the ring is 30.7 wide, 15.4 from the edge; the slash goes from the upper left to the lower right
_MARGIN = 256 * 0.06
_THICK = 256 * 0.12
_RING_RADIUS = 128 - _MARGIN - _THICK / 2


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def render(color: str, size: int) -> Image.Image:
    """The sign on a transparent background (drawn four times larger and reduced, for smooth edges)."""
    scale = 4
    big = size * scale
    k = big / 256
    margin, thick = _MARGIN * k, _THICK * k
    outer = (margin, margin, big - margin, big - margin)
    inner = (margin + thick, margin + thick, big - margin - thick, big - margin - thick)
    ring = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(ring)
    draw.ellipse(outer, fill=255)
    draw.ellipse(inner, fill=0)
    bar = Image.new("L", (big, big), 0)
    centre, reach = big / 2, _RING_RADIUS * k * 0.7071
    ImageDraw.Draw(bar).line([(centre - reach, centre - reach), (centre + reach, centre + reach)], fill=255, width=int(thick))
    disc = Image.new("L", (big, big), 0)
    ImageDraw.Draw(disc).ellipse(outer, fill=255)
    mask = ImageChops.lighter(ring, ImageChops.multiply(bar, disc)).resize((size, size), Image.LANCZOS)
    picture = Image.new("RGBA", (size, size), (*_rgb(color), 0))
    picture.putalpha(mask)
    return picture


def svg(color: str) -> str:
    reach = _RING_RADIUS * 0.7071
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">\n'
        f'  <circle cx="128" cy="128" r="{_RING_RADIUS:.2f}" fill="none" stroke="{color}" stroke-width="{_THICK:.2f}"/>\n'
        f'  <line x1="{128 - reach:.2f}" y1="{128 - reach:.2f}" x2="{128 + reach:.2f}" y2="{128 + reach:.2f}" '
        f'stroke="{color}" stroke-width="{_THICK:.2f}"/>\n'
        "</svg>\n"
    )


def make(channel: str, ico_dir: Path, out_dir: Path) -> None:
    color = COLORS["dev" if channel == "dev" else "stable"]
    ico_dir.mkdir(parents=True, exist_ok=True)
    render(COLORS["stable"], 256).save(ico_dir / "Zapret2.ico", format="ICO", sizes=ICO_SIZES)
    render(COLORS["dev"], 256).save(ico_dir / "ZapretDevLogo4.ico", format="ICO", sizes=ICO_SIZES)
    render(color, 256).save(ico_dir / "Zapret2.png")
    for size in SIZES:
        target = out_dir / "hicolor" / f"{size}x{size}" / "apps"
        target.mkdir(parents=True, exist_ok=True)
        render(color, size).save(target / "zapretgui.png")
    scalable = out_dir / "hicolor" / "scalable" / "apps"
    scalable.mkdir(parents=True, exist_ok=True)
    (scalable / "zapretgui.svg").write_text(svg(color), encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in COLORS:
        print(__doc__, file=sys.stderr)
        return 2
    make(argv[0], Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
