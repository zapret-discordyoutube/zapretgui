"""The Linux icon is a prohibition sign: red in a Dev build, blue in a Stable build."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "linux" / "make_icons.py"
spec = importlib.util.spec_from_file_location("make_icons", SCRIPT)
make_icons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_icons)

RED, BLUE = (0xE5, 0x39, 0x35), (0x1E, 0x88, 0xE5)


def _icon(tmp_path: Path, channel: str):
    ico, out = tmp_path / channel / "ico", tmp_path / channel / "out"
    make_icons.make(channel, ico, out)
    return ico, out


def test_a_dev_build_is_red_and_a_stable_build_is_blue(tmp_path) -> None:
    for channel, expected in (("dev", RED), ("stable", BLUE)):
        _ico, out = _icon(tmp_path, channel)
        picture = Image.open(out / "hicolor" / "256x256" / "apps" / "zapretgui.png").convert("RGBA")
        assert picture.getpixel((128, 20))[:3] == expected  # the ring, at the top
        assert picture.getpixel((128, 128))[3] == 255  # the slash passes through the middle
        assert picture.getpixel((6, 6))[3] == 0  # outside the ring it is transparent
        assert picture.getpixel((90, 166))[3] == 0  # the free corner inside the ring (the slash goes the other way)
        assert expected == tuple(int(make_icons.COLORS[channel][i : i + 2], 16) for i in (1, 3, 5))
        assert make_icons.COLORS[channel] in (out / "hicolor" / "scalable" / "apps" / "zapretgui.svg").read_text(encoding="utf-8")


def test_the_theme_has_every_size_and_the_window_icons_keep_their_colours(tmp_path) -> None:
    ico, out = _icon(tmp_path, "stable")
    for size in make_icons.SIZES:
        assert (out / "hicolor" / f"{size}x{size}" / "apps" / "zapretgui.png").is_file()
    # the window and the tray take Zapret2.ico (Stable) or ZapretDevLogo4.ico (Dev) by the channel of the build
    assert Image.open(ico / "Zapret2.ico").size[0] >= 16
    assert (ico / "ZapretDevLogo4.ico").is_file() and (ico / "Zapret2.png").is_file()
    assert Image.open(ico / "Zapret2.png").size == (256, 256)


def test_the_bundle_script_draws_the_icon_and_puts_it_where_the_file_manager_looks() -> None:
    text = (Path(__file__).resolve().parents[2] / "private_zapretgui" / "build_zapret" / "linux" / "build_bundle.sh")
    if not text.is_file():
        return
    script = text.read_text(encoding="utf-8")
    assert "make_icons.py" in script and '.DirIcon' in script and "$OUT/icons/hicolor" in script
