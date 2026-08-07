# GUI Build/Runtime Contract

Дата фиксации: 2026-04-10

Этот документ коротко фиксирует, как должен работать build/runtime слой GUI, чтобы сборка не жила на старых значениях, случайных env-переменных или остатках прошлых билдов.

## Канонический путь

- Build-настройки для GUI берутся из `private_zapretgui/build_zapret/build_local_config.py`.
- Сборщик пишет runtime-файл `public_zapretgui/src/config/_build_secrets.py`.
- Runtime GUI читает публичные runtime-поля только из `config._build_secrets`.

Итоговая цепочка такая:

`build_local_config.py` -> `build_release_gui.py` -> `_build_secrets.py` -> runtime import

## Что запрещено

- Нельзя восстанавливать старые значения из предыдущего `_build_secrets.py`.
- Нельзя возвращать repo-based `.env` как источник GUI build/runtime значений.
- Нельзя держать второй скрытый runtime-источник истины для `PREMIUM_API_BASE_URL`.
- Нельзя держать второй скрытый runtime-источник истины для `UPDATE_SERVERS`.
- Нельзя помещать Telegram Bot API токен в пользовательскую сборку. Публичный
  клиент проверяет Telegram только через общедоступную страницу канала.

## Что сейчас считается каноническим

- `PREMIUM_API_BASE_URL` -> только `config._build_secrets`.
- `UPDATE_SERVERS` -> только `config._build_secrets`.
- `PROXY_PRESETS` -> только `config._build_secrets`.
- `MTPROXY_LINK` -> только `config._build_secrets`.

## Практический смысл

- Если в исходниках поменяли build-настройку, но не пересобрали `_build_secrets.py`, runtime GUI продолжит жить на старом generated файле.
- Если в runtime оставить fallback к env или локальному файлу, появится второй источник истины, и сборка перестанет быть предсказуемой.
- Поэтому для runtime-полей важнее один жёсткий путь, чем “гибкость на всякий случай”.
