"""Диагностическая проверка версии на публичной странице Telegram-канала."""

import re
import requests
from typing import Optional, Dict, Any
from log.log import log

from .network_hints import maybe_log_disable_dpi_for_update
from .proxy_bypass import session_bypass_proxy


def _no_proxy_get(url: str, **kwargs) -> requests.Response:
    """GET-запрос без прокси — защита от утечки прокси-настроек build-окружения."""
    s = session_bypass_proxy()
    try:
        return s.get(url, **kwargs)
    finally:
        s.close()

# Каналы для разных веток (username без @)
TELEGRAM_CHANNELS = {
    'stable': 'zapretnetdiscordyoutube',
    'dev': 'zapretguidev',
}

# Telegram не участвует в выборе выпуска, поэтому диагностика не должна долго
# занимать фоновый слот при блокировке t.me.
TELEGRAM_TIMEOUT = 5


def _parse_telegram_web(channel: str) -> Optional[Dict[str, Any]]:
    """
    Парсит публичную страницу канала через t.me
    Работает без авторизации.
    
    Приоритет: версия из имени файла (Zapret2Setup*.exe) > текст постов.
    """
    channel_name = TELEGRAM_CHANNELS.get(channel, TELEGRAM_CHANNELS['stable'])
    url = f"https://t.me/s/{channel_name}"
    
    try:
        response = _no_proxy_get(url, timeout=TELEGRAM_TIMEOUT, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        if response.status_code != 200:
            return None
        
        html = response.text
        
        # ✅ ПРИОРИТЕТ 1: Ищем имя файла установщика и извлекаем версию из него
        file_name_pattern = r'(Zapret2Setup[^"<>\s]*\.exe)'
        file_names = re.findall(file_name_pattern, html)
        
        if file_names:
            # Берём последний (самый новый) файл
            file_name = file_names[-1]
            version = _extract_version_from_filename(file_name)
            
            if version:
                log(f"✅ Web: версия {version} из имени файла {file_name}", "📱 TG")
                return {
                    'version': version,
                    'file_name': file_name,
                    'source': f'Telegram @{channel_name} (web)',
                    'channel': channel_name,
                }
        
        # ✅ ПРИОРИТЕТ 2 (fallback): Ищем версию в тексте сообщений
        version_pattern = r'(\d+\.\d+\.\d+\.\d+)'
        version_matches = re.findall(version_pattern, html)
        
        if version_matches:
            version = version_matches[-1]
            file_name = file_names[-1] if file_names else f"Zapret2Setup_{version}.exe"
            
            log(f"⚠️ Web: версия {version} из текста (fallback)", "📱 TG")
            return {
                'version': version,
                'file_name': file_name,
                'source': f'Telegram @{channel_name} (web)',
                'channel': channel_name,
            }
        
        return None
        
    except Exception as e:
        log(f"❌ Ошибка парсинга t.me: {e}", "📱 TG")
        maybe_log_disable_dpi_for_update(e, scope="update_check", level="📱 TG")
        return None


def get_telegram_version_info(channel: str = 'stable') -> Optional[Dict[str, Any]]:
    """Читает версию с публичной страницы канала без токена и авторизации."""
    channel_name = TELEGRAM_CHANNELS.get(channel, TELEGRAM_CHANNELS['stable'])

    try:
        log(f"🔍 Telegram: парсинг t.me/s/{channel_name}...", "📱 TG")
        result = _parse_telegram_web(channel)
        if result:
            log(f"✅ Telegram: найдена версия {result['version']} (web)", "📱 TG")
            return result
    except Exception as e:
        log(f"⚠️ Web парсинг ошибка: {e}", "📱 TG")
    
    log(f"⚠️ Telegram: версия не найдена в публичной странице @{channel_name}", "📱 TG")
    return None


def _extract_version_from_filename(file_name: str) -> Optional[str]:
    """
    Извлекает версию из имени файла установщика.
    
    Поддерживает оба формата:
    - Zapret2Setup_DEV_20_3_17_14.exe   → 20.3.17.14  (подчёркивания)
    - Zapret2Setup_DEV_20.3.17.14.exe   → 20.3.17.14  (точки)
    - Zapret2Setup_20_3_17_14.exe       → 20.3.17.14  (без DEV)
    """
    if not file_name:
        return None
    
    # Паттерн 1: подчёркивания в имени файла
    # Zapret2Setup[_DEV]_XX_X_XX_XX.exe
    # Берём всё после последнего "Setup" или "TEST", до ".exe"
    m = re.search(
        r'Zapret2Setup(?:_DEV)?_(\d+(?:_\d+)+)\.exe',
        file_name,
        re.IGNORECASE,
    )
    if m:
        version = m.group(1).replace('_', '.')
        # Проверяем что это похоже на версию (минимум 3 части)
        parts = version.split('.')
        if len(parts) >= 3:
            return version
    
    # Паттерн 2: точки в имени файла (старый формат)
    # Zapret2Setup_20.3.17.14.exe
    m = re.search(
        r'Zapret2Setup(?:_DEV)?[_.]?(\d+\.\d+\.\d+(?:\.\d+)?)\.exe',
        file_name,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    
    # Паттерн 3: любой 3-4 part version в имени файла (generic fallback)
    dot_patterns = [
        r'v?(\d+\.\d+\.\d+\.\d+)',  # 20.3.17.14
        r'v?(\d+\.\d+\.\d+)',        # 20.3.17
    ]
    for pattern in dot_patterns:
        match = re.search(pattern, file_name)
        if match:
            return match.group(1)
    
    return None
