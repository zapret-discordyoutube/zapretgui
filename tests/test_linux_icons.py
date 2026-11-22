"""The Linux icon is the Windows mascot: reddish in a Dev build, blue in a Stable build; the .ico files stay untouched."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
from PIL import Image

PUBLIC = Path(__file__).resolve().parents[1]
SCRIPT = PUBLIC / "packaging" / "linux" / "make_icons.py"
PRIVATE_ICO = PUBLIC.parent / "private_zapretgui" / "dist" / "ico"
spec = importlib.util.spec_from_file_location("make_icons", SCRIPT)
make_icons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_icons)

pytestmark = pytest.mark.skipif(not (PRIVATE_ICO / "Zapret2.ico").is_file(), reason="the private repository is not next to this one")


def _icon(tmp_path: Path, channel: str):
    ico, out = tmp_path / channel / "ico", tmp_path / channel / "out"
    ico.mkdir(parents=True)
    for name in make_icons.SOURCE.values():
        shutil.copy2(PRIVATE_ICO / name, ico / name)
    make_icons.make(channel, ico, out)
    return ico, out


def _mean_rgb(picture: Image.Image) -> tuple[float, float, float]:
    pixels = [p[:3] for p in picture.convert("RGBA").getdata() if p[3] > 200]
    return tuple(sum(p[i] for p in pixels) / len(pixels) for i in range(3))


def test_a_dev_build_is_reddish_and_a_stable_build_is_blue(tmp_path) -> None:
    dev_red, _g, dev_blue = _mean_rgb(Image.open(_icon(tmp_path, "dev")[1] / "hicolor" / "256x256" / "apps" / "zapretgui.png"))
    stable_red, _g, stable_blue = _mean_rgb(Image.open(_icon(tmp_path, "stable")[1] / "hicolor" / "256x256" / "apps" / "zapretgui.png"))
    assert dev_red > dev_blue and stable_blue > stable_red


def test_the_theme_has_every_size_and_the_window_icons_are_not_touched(tmp_path) -> None:
    ico, out = _icon(tmp_path, "stable")
    for size in make_icons.SIZES:
        picture = Image.open(out / "hicolor" / f"{size}x{size}" / "apps" / "zapretgui.png")
        assert picture.size == (size, size)
    for name in make_icons.SOURCE.values():
        assert (ico / name).read_bytes() == (PRIVATE_ICO / name).read_bytes()  # the window and the tray read the Windows files
    assert Image.open(ico / "Zapret2.png").size == (256, 256)


def test_the_bundle_script_makes_the_theme_and_puts_it_where_the_file_manager_looks() -> None:
    text = PUBLIC.parent / "private_zapretgui" / "build_zapret" / "linux" / "build_bundle.sh"
    if not text.is_file():
        return
    script = text.read_text(encoding="utf-8")
    assert "make_icons.py" in script and ".DirIcon" in script and "$OUT/icons/hicolor" in script
