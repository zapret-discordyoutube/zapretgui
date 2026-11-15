"""The hosts file on Windows: its place, attributes, access rights and the write that survives a full disk.

Moved from ``hosts/hosts.py`` as it was; the program-side logic (what to write) stays there.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from log.log import log


def default_hosts_path() -> Path:
    """Через переменные окружения, чтобы корректно работать, когда Windows установлена не на C:."""
    sys_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if sys_root:
        return Path(sys_root, "System32", "drivers", "etc", "hosts")
    return Path(r"C:\Windows\System32\drivers\etc\hosts")


def _run_cmd(args, description):
    """Выполняет команду и логирует результат"""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode == 0:
            log(f"✅ {description}: успешно")
            return True
        else:
            # Проверяем stderr или stdout на наличие ошибки
            error = result.stderr.strip() or result.stdout.strip()
            log(f"⚠ {description}: {error}", "⚠ WARNING")
            return False
    except Exception as e:
        log(f"❌ {description}: {e}", "❌ ERROR")
        return False


def _get_current_username():
    """Получает имя текущего пользователя"""
    try:
        import getpass
        return getpass.getuser()
    except:
        return None


def restore_hosts_permissions(hosts_file: Path):
    """
    Восстанавливает стандартные права доступа к файлу hosts.
    Устанавливает: Administrators:F, SYSTEM:F, Users:R (через SID для любой локализации).

    Вызывается ТОЛЬКО явно по кнопке пользователя в UI — не автоматически.
    Это НЕ вредоносная активность: пользователь сам нажал «Восстановить права доступа»,
    чтобы вернуть стандартные ACL на файл hosts после блокировки антивирусом.

    Returns:
        tuple: (success: bool, message: str)
    """
    hosts_path = str(hosts_file)

    # Действие инициировано пользователем через кнопку в интерфейсе приложения.
    # Восстанавливаем стандартные Windows ACL: Administrators:F, SYSTEM:F, Users:R.
    log("Пользователь запросил восстановление стандартных прав доступа к файлу hosts")

    # Well-known SIDs (работают на любой локализации Windows)
    SID_ADMINISTRATORS = "*S-1-5-32-544"  # Administrators
    SID_USERS = "*S-1-5-32-545"           # Users
    SID_SYSTEM = "*S-1-5-18"              # SYSTEM

    try:
        # Снимаем атрибут "только для чтения"
        _run_cmd(['attrib', '-R', hosts_path], "attrib -R")

        # Забираем владение для группы администраторов
        _run_cmd(['takeown', '/F', hosts_path, '/A'], "takeown /A")

        # Сбрасываем ACL и выставляем стандартные права через SID
        _run_cmd(['icacls', hosts_path, '/reset'], "icacls /reset")
        _run_cmd(['icacls', hosts_path, '/grant', f'{SID_ADMINISTRATORS}:F'],
                 "icacls /grant Administrators")
        _run_cmd(['icacls', hosts_path, '/grant', f'{SID_SYSTEM}:F'],
                 "icacls /grant SYSTEM")
        _run_cmd(['icacls', hosts_path, '/grant', f'{SID_USERS}:R'],
                 "icacls /grant Users (read)")

        # Проверяем результат
        try:
            hosts_file.read_text(encoding='utf-8')
            log("Права восстановлены, файл hosts доступен для чтения")

            try:
                with hosts_file.open('a', encoding='utf-8-sig'):
                    pass
                log("Файл hosts доступен для записи")
                return True, "Права доступа к файлу hosts восстановлены"
            except PermissionError:
                return False, (
                    "Файл hosts доступен для чтения, но запись запрещена.\n"
                    "Возможные причины: защита антивируса/Defender.\n"
                    "Добавьте исключение для hosts или временно отключите защиту."
                )

        except PermissionError:
            return False, (
                "Не удалось восстановить права доступа.\n"
                "Попробуйте:\n"
                "1. Запустить программу от имени администратора\n"
                "2. Временно отключить антивирус\n"
                "3. Добавить исключение для файла hosts"
            )

        except Exception as e:
            return False, f"Ошибка при проверке прав: {e}"

    except FileNotFoundError as e:
        return False, f"Системная команда не найдена: {e}"
    except Exception as e:
        return False, f"Ошибка: {e}"


def check_hosts_file_name(hosts_file: Path):
    """Проверяет правильность написания имени файла hosts"""
    hosts_dir = hosts_file.parent

    # Проверка только читает: каталог etc не создаём.
    if not hosts_dir.exists():
        return True, None

    # Сначала проверяем правильный файл hosts
    hosts_lower = hosts_dir / "hosts"
    if hosts_lower.exists():
        # Кодировку здесь не проверяем: safe_read_hosts_file читает и UTF-8, и
        # cp1251/cp866 (комментарий по-русски в «ANSI»), а запись идёт в UTF-8.
        return True, None
    
    # Если правильного файла нет, проверяем есть ли неправильный HOSTS
    hosts_upper = hosts_dir / "HOSTS"
    if hosts_upper.exists():
        log("Обнаружен файл HOSTS (с большими буквами) - это неправильно!", level="⚠ WARNING")
        return False, "Файл должен называться 'hosts' (с маленькими буквами), а не 'HOSTS'"
    
    # Файла нет вообще — это нормально, его создаст явная запись.
    return True, None


def is_file_readonly(filepath):
    """Проверяет, установлен ли атрибут 'только для чтения' у файла"""
    try:
        file_stat = os.stat(filepath)
        return not (file_stat.st_mode & stat.S_IWRITE)
    except Exception as e:
        log(f"Ошибка при проверке атрибутов файла: {e}")
        return False


def write_in_place(hosts_file: Path, data: bytes) -> None:
    """Пишет на месте, а не подменой: у системного hosts свои права доступа, подмена их бы потеряла."""
    previous: bytes | None = None
    if hosts_file.exists():
        previous = hosts_file.read_bytes()
    try:
        with open(hosts_file, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except PermissionError:
        raise
    except Exception:
        # Запись оборвалась после обрезки файла (например, нет места):
        # возвращаем прежнее содержимое, чтобы не оставить hosts пустым.
        if previous is not None:
            try:
                with open(hosts_file, "wb") as handle:
                    handle.write(previous)
            except Exception as restore_error:
                log(f"Не удалось вернуть прежний hosts: {restore_error}", "❌ ERROR")
        raise


def can_append(hosts_file: Path) -> None:
    """Проверяет право записи, не меняя содержимое. Бросает PermissionError / FileNotFoundError."""
    with hosts_file.open("a", encoding="utf-8-sig"):
        pass


class WindowsHostsFile:
    """``Platform.hosts`` on Windows."""

    encoding = "utf-8-sig"  # BOM: the old Windows tools read a file without it as ANSI

    def default_path(self) -> Path:
        return default_hosts_path()

    def is_read_only(self, path: Path) -> bool:
        return is_file_readonly(path)

    def write(self, path: Path, data: bytes) -> None:
        write_in_place(path, data)

    def check_name(self, path: Path):
        return check_hosts_file_name(path)

    def can_write(self, path: Path) -> None:
        can_append(path)

    def restore_access(self, path: Path):
        return restore_hosts_permissions(path)
