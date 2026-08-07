"""
updater/update_cache.py
────────────────────────────────────────────────────────────────
Кэширование результатов проверки обновлений для снижения нагрузки на сервер
"""
import time
from typing import Optional, Dict, Any

from log.log import log
from settings import store as settings_store

from .channel_utils import normalize_update_channel

CACHE_DURATION = 3600  # 1 час (3600 секунд)

class UpdateCache:
    """Кэш для результатов проверки обновлений"""
    
    @staticmethod
    def get_cached_release(channel: str) -> Optional[Dict[str, Any]]:
        """
        Возвращает закэшированный релиз если он актуален
        
        Args:
            channel: "stable" или "dev"
            
        Returns:
            Dict с информацией о релизе или None
        """
        try:
            channel = normalize_update_channel(channel)
            cache = settings_store.get_updater_settings().get("release_cache", {})
            
            if channel not in cache:
                return None
            
            entry = cache[channel]
            
            # Проверяем срок годности
            cached_time = entry.get('cached_at', 0)
            age = time.time() - cached_time
            
            if age > CACHE_DURATION:
                log(f"⏰ Кэш обновлений устарел ({age/60:.0f} мин)", "🔄 CACHE")
                return None
            
            log(f"✅ Используем кэш обновлений ({(CACHE_DURATION-age)/60:.0f} мин до истечения)", "🔄 CACHE")
            return entry.get('release_info')
            
        except Exception as e:
            log(f"⚠️ Ошибка чтения кэша: {e}", "🔄 CACHE")
            return None
    
    @staticmethod
    def cache_release(channel: str, release_info: Dict[str, Any]):
        """
        Сохраняет информацию о релизе в кэш
        
        Args:
            channel: "stable" или "dev"
            release_info: Информация о релизе
        """
        try:
            channel = normalize_update_channel(channel)
            cache = settings_store.get_updater_settings().get("release_cache", {})
            if not isinstance(cache, dict):
                cache = {}
            
            # Добавляем новую запись
            cache[channel] = {
                'release_info': release_info,
                'cached_at': time.time()
            }
            
            settings_store.set_updater_settings({"release_cache": cache})
            
            log(f"💾 Кэш обновлений сохранен (TTL: {CACHE_DURATION/60:.0f} мин)", "🔄 CACHE")
            
        except Exception as e:
            log(f"⚠️ Ошибка сохранения кэша: {e}", "🔄 CACHE")
    
    @staticmethod
    def invalidate(channel: Optional[str] = None):
        """
        Очищает кэш обновлений
        
        Args:
            channel: Конкретный канал или None для очистки всего
        """
        try:
            cache = settings_store.get_updater_settings().get("release_cache", {})
            if not isinstance(cache, dict):
                cache = {}
            if channel is None:
                settings_store.set_updater_settings({"release_cache": {}})
                log("🗑️ Весь кэш обновлений очищен", "🔄 CACHE")
            else:
                channel = normalize_update_channel(channel)
                if channel in cache:
                    del cache[channel]
                    settings_store.set_updater_settings({"release_cache": cache})
                    log(f"🗑️ Кэш для канала {channel} очищен", "🔄 CACHE")
                        
        except Exception as e:
            log(f"⚠️ Ошибка очистки кэша: {e}", "🔄 CACHE")
    
    @staticmethod
    def get_cache_age(channel: str) -> Optional[int]:
        """
        Возвращает возраст кэша в секундах
        
        Args:
            channel: "stable" или "dev"
            
        Returns:
            Возраст в секундах или None если кэша нет
        """
        try:
            channel = normalize_update_channel(channel)
            cache = settings_store.get_updater_settings().get("release_cache", {})
            
            if channel not in cache:
                return None
            
            cached_time = cache[channel].get('cached_at', 0)
            return int(time.time() - cached_time)
            
        except:
            return None

    @staticmethod
    def get_cache_info(channel: str) -> Optional[Dict[str, Any]]:
        """
        Возвращает подробную информацию о кэше
        
        Args:
            channel: "stable" или "dev"
            
        Returns:
            Dict с информацией о кэше или None
        """
        try:
            channel = normalize_update_channel(channel)
            cache = settings_store.get_updater_settings().get("release_cache", {})
            
            if channel not in cache:
                return None
            
            entry = cache[channel]
            cached_time = entry.get('cached_at', 0)
            age_seconds = time.time() - cached_time
            age_minutes = int(age_seconds / 60)
            age_hours = age_seconds / 3600
            is_valid = age_seconds < CACHE_DURATION
            
            release_info = entry.get('release_info', {})
            
            return {
                'version': release_info.get('version'),
                'source': release_info.get('source'),
                'cached_at': cached_time,
                'age_seconds': int(age_seconds),
                'age_minutes': age_minutes,
                'age_hours': age_hours,
                'is_valid': is_valid,
                'ttl_remaining': int(CACHE_DURATION - age_seconds) if is_valid else 0
            }
            
        except Exception as e:
            log(f"Ошибка получения информации о кэше: {e}", "🔄 CACHE")
            return None
