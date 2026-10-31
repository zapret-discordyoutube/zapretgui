# telegram_proxy/telegram_hosts.py
"""Записи сайтов Telegram в файле hosts Windows.

Блок строк «149.154.167.220 домен» нужен только браузеру: с ним открываются
web.telegram.org, t.me, telegram.org и загрузки desktop.telegram.org.
Самому Telegram Proxy он не нужен — прокси подключается к серверам по IP.

Файл hosts меняется только по явной кнопке на странице Telegram Proxy
(«Прописать» / «Убрать»). Открытие страницы лишь читает файл и показывает,
сколько записей уже прописано.

Модуль делится на две части:
- чистые функции над текстом hosts (``*_text``) — их легко проверять тестами;
- обёртки с чтением и записью файла через ``hosts.public``.
"""

from __future__ import annotations

from dataclasses import dataclass

from log.log import log


TELEGRAM_RELAY_IP = "149.154.167.220"

# Not used but known Telegram domains (do NOT add to hosts):
# kws1.web.telegram.org, kws1-1.web.telegram.org
# kws5.web.telegram.org, kws5-1.web.telegram.org
# zws1.web.telegram.org, zws1-1.web.telegram.org
# zws5.web.telegram.org, zws5-1.web.telegram.org
# pluto.web.telegram.org, pluto-1.web.telegram.org
# flora.web.telegram.org

TELEGRAM_DOMAINS: list[str] = [
    "zws4.web.telegram.org",
    "vesta.web.telegram.org",
    "core.telegram.org",
    "my.telegram.org",
    "oauth.telegram.org",
    "vesta-1.web.telegram.org",
    "venus-1.web.telegram.org",
    "telegram.me",
    "telegram.dog",
    "telegram.space",
    "telesco.pe",
    "cdn.telesco.pe",
    "cdn1.telesco.pe",
    "cdn2.telesco.pe",
    "cdn3.telesco.pe",
    "cdn4.telesco.pe",
    "cdn5.telesco.pe",
    "cdn6.telesco.pe",
    "tg.dev",
    "oauth.tg.dev",
    "telegram.org",
    "desktop.telegram.org",
    "macos.telegram.org",
    "t.me",
    "api.telegram.org",
    "td.telegram.org",
    "venus.web.telegram.org",
    "web.telegram.org",
    "kws2-1.web.telegram.org",
    "kws2.web.telegram.org",
    "kws4-1.web.telegram.org",
    "kws4.web.telegram.org",
    "zws2-1.web.telegram.org",
    "zws2.web.telegram.org",
    "zws4-1.web.telegram.org",
]

_TELEGRAM_DOMAINS_LOWER: set[str] = {d.lower() for d in TELEGRAM_DOMAINS}

TELEGRAM_HOSTS_MARKER = "# --- Telegram Proxy (auto-managed by Zapret 2 GUI) ---"


class TelegramHostsError(Exception):
    """Файл hosts не удалось прочитать или записать. Текст — для пользователя."""


@dataclass(frozen=True, slots=True)
class TelegramHostsStatus:
    """Сколько доменов Telegram сейчас ведут на relay IP в файле hosts."""

    present: int
    total: int
    block_present: bool

    @property
    def is_complete(self) -> bool:
        return self.total > 0 and self.present >= self.total

    @property
    def is_empty(self) -> bool:
        return self.present <= 0


def _parse_entry(line: str) -> tuple[str, list[str]] | None:
    """Возвращает (ip, домены в нижнем регистре) для строки записи или None.

    В одной строке hosts может быть несколько имён («0.0.0.0 a.com b.com»)
    и комментарий в конце — учитываются все имена до «#».
    """
    stripped = line.split("#", 1)[0].strip()
    if not stripped:
        return None
    parts = stripped.split()
    if len(parts) < 2:
        return None
    return parts[0], [part.lower() for part in parts[1:]]


def _line_without_domains(line: str, ip: str, domains: list[str], drop: set[str]) -> str | None:
    """Строка hosts без имён из ``drop``. None — других имён в строке не осталось.

    Если в строке есть имена не Telegram, они остаются на месте с прежним IP
    и комментарием: чужие записи пользователя не теряются.
    """
    kept = [domain for domain in domains if domain not in drop]
    if not kept:
        return None
    if len(kept) == len(domains):
        return line
    _body, sep, comment = line.partition("#")
    newline = "\n" if line.endswith("\n") else ""
    tail = f" #{comment.rstrip(chr(10)).rstrip(chr(13))}" if sep else ""
    indent = line[: len(line) - len(line.lstrip())]
    return f"{indent}{ip} {' '.join(kept)}{tail}{newline}"


def telegram_hosts_status_from_text(content: str) -> TelegramHostsStatus:
    """Считает домены Telegram, которые по файлу hosts ведут на relay IP.

    Windows берёт первую подходящую строку, поэтому для каждого домена
    учитывается только первая запись.
    """
    first_ip: dict[str, str] = {}
    block_present = False
    for line in str(content or "").splitlines():
        if line.strip() == TELEGRAM_HOSTS_MARKER:
            block_present = True
            continue
        entry = _parse_entry(line)
        if entry is None:
            continue
        ip, domains = entry
        for domain in domains:
            first_ip.setdefault(domain, ip)
    present = sum(
        1 for domain in _TELEGRAM_DOMAINS_LOWER if first_ip.get(domain) == TELEGRAM_RELAY_IP
    )
    return TelegramHostsStatus(
        present=present,
        total=len(_TELEGRAM_DOMAINS_LOWER),
        block_present=block_present,
    )


def _trim_trailing_blank_lines(lines: list[str]) -> None:
    while lines and lines[-1].strip() == "":
        lines.pop()


def add_telegram_hosts_to_text(content: str) -> str:
    """Убирает старые строки доменов Telegram и дописывает блок в конец.

    Имена Telegram на любой IP убираются из своих строк (остальные имена этих
    строк остаются), иначе Windows может взять старую запись. Повторный вызов
    даёт тот же текст.
    """
    new_lines: list[str] = []
    for line in str(content or "").splitlines(keepends=True):
        if line.strip() == TELEGRAM_HOSTS_MARKER:
            continue
        entry = _parse_entry(line)
        if entry is not None:
            ip, domains = entry
            rest = _line_without_domains(line, ip, domains, _TELEGRAM_DOMAINS_LOWER)
            if rest is None:
                continue
            line = rest
        new_lines.append(line)

    _trim_trailing_blank_lines(new_lines)
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] = new_lines[-1] + "\n"
    if new_lines:
        new_lines.append("\n")
    new_lines.append(f"{TELEGRAM_HOSTS_MARKER}\n")
    for domain in TELEGRAM_DOMAINS:
        new_lines.append(f"{TELEGRAM_RELAY_IP} {domain}\n")
    return "".join(new_lines)


def remove_telegram_hosts_from_text(content: str) -> str:
    """Убирает строку-маркер и имена Telegram из строк с relay IP.

    Остальные строки, в том числе домены Telegram на другом IP и чужие имена в
    одной строке с relay IP, остаются. Если убирать нечего, возвращается
    исходный текст без изменений.
    """
    text = str(content or "")
    new_lines: list[str] = []
    removed = False
    for line in text.splitlines(keepends=True):
        if line.strip() == TELEGRAM_HOSTS_MARKER:
            removed = True
            continue
        entry = _parse_entry(line)
        if entry is not None and entry[0] == TELEGRAM_RELAY_IP:
            ip, domains = entry
            rest = _line_without_domains(line, ip, domains, _TELEGRAM_DOMAINS_LOWER)
            if rest != line:
                removed = True
            if rest is None:
                continue
            line = rest
        new_lines.append(line)

    if not removed:
        return text
    # Пустая строка, которую добавлял блок перед маркером, не должна копиться.
    _trim_trailing_blank_lines(new_lines)
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] = new_lines[-1] + "\n"
    return "".join(new_lines)


_WRITE_ERROR_TEXT = (
    "Не удалось записать файл hosts: он защищён от записи (атрибут «только для чтения») "
    "или у программы нет прав администратора. Снять защиту можно кнопкой "
    "«Восстановить права доступа» на странице Hosts."
)


def _read_hosts_text() -> str:
    from hosts.public import read_hosts_file

    try:
        content = read_hosts_file()
    except Exception as exc:
        raise TelegramHostsError(f"Не удалось прочитать файл hosts: {exc}") from exc
    if content is None:
        raise TelegramHostsError("Не удалось прочитать файл hosts")
    return str(content)


def _write_hosts_text(content: str) -> None:
    from hosts.public import write_hosts_file

    try:
        written = write_hosts_file(content)
    except Exception as exc:
        raise TelegramHostsError(f"{_WRITE_ERROR_TEXT}\n{exc}") from exc
    if not written:
        raise TelegramHostsError(_WRITE_ERROR_TEXT)


def get_telegram_hosts_status() -> TelegramHostsStatus:
    """Только читает файл hosts и считает записи Telegram."""
    return telegram_hosts_status_from_text(_read_hosts_text())


def add_telegram_hosts() -> tuple[bool, str]:
    """Прописывает блок Telegram в hosts. Возвращает ``(изменён ли файл, сообщение)``.

    При ошибке чтения или записи бросает ``TelegramHostsError``.
    """
    content = _read_hosts_text()
    new_content = add_telegram_hosts_to_text(content)
    total = len(TELEGRAM_DOMAINS)
    if new_content == content:
        return False, f"Все {total} записей Telegram уже прописаны в hosts"
    _write_hosts_text(new_content)
    msg = f"Записи Telegram прописаны в hosts: {total}"
    log(f"Telegram hosts: {msg}")
    return True, msg


def remove_telegram_hosts() -> tuple[bool, str]:
    """Убирает блок Telegram из hosts. Возвращает ``(изменён ли файл, сообщение)``.

    Если убирать нечего, файл не записывается. При ошибке чтения или записи
    бросает ``TelegramHostsError``.
    """
    content = _read_hosts_text()
    new_content = remove_telegram_hosts_from_text(content)
    if new_content == content:
        return False, "Записей Telegram в hosts нет"
    _write_hosts_text(new_content)
    msg = "Записи Telegram убраны из hosts"
    log(f"Telegram hosts: {msg}")
    return True, msg
