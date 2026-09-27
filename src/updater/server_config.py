"""Адреса зеркал обновлений и общие сетевые тайм-ауты.

Список зеркал генерируется при сборке в ``config/_build_secrets.py``. Порядок
в списке — порядок, в котором зеркала показываются и пробуются для
скачивания. Тайм-ауты также используют загрузка пресетов по ссылке.
"""

from config._build_secrets import UPDATE_SERVERS as VPS_SERVERS

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 15

# Сертификаты зеркал самоподписанные.
VERIFY_SSL = False


def should_verify_ssl() -> bool:
    return VERIFY_SSL


__all__ = ["CONNECT_TIMEOUT", "READ_TIMEOUT", "VERIFY_SSL", "VPS_SERVERS", "should_verify_ssl"]
