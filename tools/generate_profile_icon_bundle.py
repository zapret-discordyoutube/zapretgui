"""Генератор бандла simple-иконок для профилей и сервисов Hosts.

Извлекает из пакета simplepycons ТОЛЬКО те SVG, которые реально используются
каталогом иконок профилей (profile/icons.py), готовым каталогом Hosts
(private_zapretgui/resources/system/hosts_catalog.sqlite3, столбец
services.icon_name) и списком DNS-серверов (dns/dns_providers.py), и
записывает их в сгенерированный модуль src/profile/ui/simple_icons_bundle.py.

Зачем: импорт simplepycons тянет ~3400 модулей (~2.6с и десятки МБ памяти),
поэтому в рантайме приложения он не используется вообще. simplepycons нужен
только на машине разработчика для регенерации бандла.

Запуск (из корня репозитория):
    PYTHONPATH=src python tools/generate_profile_icon_bundle.py

После добавления нового сервиса с иконкой "simple:<slug>:<fallback>" в
profile/icons.py, в каталог Hosts или в dns/dns_providers.py — перезапустить
генератор. Тесты tests/test_profile_icon_bundle.py,
tests/test_hosts_catalog_sqlite.py и tests/test_dns_provider_icons.py упадут,
если бандл не покрывает каталоги.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_SRC = PROJECT_ROOT / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

BUNDLE_PATH = PROJECT_SRC / "profile" / "ui" / "simple_icons_bundle.py"
HOSTS_CATALOG_PATH = (
    PROJECT_ROOT.parent / "private_zapretgui" / "resources" / "system" / "hosts_catalog.sqlite3"
)

_HEADER = '''"""Бандл simple-иконок профилей и сервисов Hosts. СГЕНЕРИРОВАНО — НЕ редактировать вручную.

Источник: пакет simplepycons (Simple Icons, CC0). Здесь лежат только SVG,
которые реально используются каталогом profile/icons.py и каталогом Hosts —
благодаря этому рантайм не импортирует simplepycons (~3400 модулей, ~2.6с).

Регенерация: PYTHONPATH=src python tools/generate_profile_icon_bundle.py
"""
from __future__ import annotations


# slug -> (primary_color_hex, raw_svg)
SIMPLE_ICON_SVGS: dict[str, tuple[str, str]] = {
'''

_FOOTER = '''}


__all__ = ["SIMPLE_ICON_SVGS"]
'''


def _simple_slug(icon_name: str) -> str:
    if not icon_name.startswith("simple:"):
        return ""
    slug = icon_name.removeprefix("simple:").partition(":")[0]
    return slug.strip().lower().replace("-", "")


def collect_hosts_catalog_slugs() -> set[str]:
    """Собирает simple-слаги сервисов из готового каталога Hosts."""
    if not HOSTS_CATALOG_PATH.is_file():
        raise FileNotFoundError(f"Не найден каталог Hosts: {HOSTS_CATALOG_PATH}")
    connection = sqlite3.connect(HOSTS_CATALOG_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT DISTINCT icon_name FROM services").fetchall()
    finally:
        connection.close()
    return {slug for row in rows if (slug := _simple_slug(str(row[0] or "")))}


def collect_dns_provider_slugs() -> set[str]:
    """Собирает simple-слаги DNS-серверов со страницы «Настройка DNS»."""
    from dns.dns_providers import DNS_PROVIDERS

    return {
        slug
        for providers in DNS_PROVIDERS.values()
        for data in providers.values()
        if (slug := _simple_slug(str(data.get("icon", "") or "")))
    }


def collect_catalog_slugs() -> list[str]:
    """Собирает уникальные simple-слаги из каталогов иконок профилей, Hosts и DNS."""
    import profile.icons as profile_icons

    slugs: set[str] = collect_hosts_catalog_slugs() | collect_dns_provider_slugs()
    for attr_name in dir(profile_icons):
        attr = getattr(profile_icons, attr_name)
        if not isinstance(attr, dict):
            continue
        for value in attr.values():
            slug = _simple_slug(str(getattr(value, "icon_name", "") or ""))
            if slug:
                slugs.add(slug)
    return sorted(slugs)


def extract_icon(all_icons, slug: str) -> tuple[str, str] | None:
    try:
        icon = all_icons[slug]
    except Exception:
        getter = getattr(all_icons, f"get_{slug}_icon", None)
        if not callable(getter):
            return None
        try:
            icon = getter()
        except Exception:
            return None
    raw_svg = str(getattr(icon, "raw_svg", "") or "").strip()
    if not raw_svg:
        return None
    primary_color = str(getattr(icon, "primary_color", "") or "").lstrip("#").strip()
    return (f"#{primary_color}" if primary_color else "", raw_svg)


def main() -> int:
    slugs = collect_catalog_slugs()
    if not slugs:
        print("Каталог иконок не дал ни одного simple-слага — ничего не делаю.", file=sys.stderr)
        return 1

    from simplepycons import all_icons

    lines: list[str] = [_HEADER]
    missing: list[str] = []
    for slug in slugs:
        entry = extract_icon(all_icons, slug)
        if entry is None:
            missing.append(slug)
            continue
        primary_color, raw_svg = entry
        lines.append(f"    {slug!r}: ({primary_color!r}, {raw_svg!r}),\n")
    lines.append(_FOOTER)

    if missing:
        print(f"ОШИБКА: в simplepycons не найдены слаги: {', '.join(missing)}", file=sys.stderr)
        return 1

    BUNDLE_PATH.write_text("".join(lines), encoding="utf-8")
    print(f"Записан {BUNDLE_PATH.relative_to(PROJECT_ROOT)}: {len(slugs)} иконок.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
