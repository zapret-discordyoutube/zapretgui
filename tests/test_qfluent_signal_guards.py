"""Тесты центральной защиты от висячих theme-подписок qfluentwidgets.

Контракт (см. ui/qfluent_signal_guards.py):

* создание CardWidget / BodyLabel не добавляет per-widget подписок на
  qconfig.themeChanged — все обновления идут через единый диспетчер;
* виджет с удалённым C++-объектом (Nuitka-сценарий: PyQt не разорвал
  соединение сам) не роняет смену темы и вычищается из диспетчера;
* живые виджеты продолжают получать theme-обновления;
* ThemeRefreshBinding отписывается от qconfig при destroyed цели;
* sentinel: новые qconfig-подписки внутри qfluentwidgets (после апгрейда)
  и прямые qconfig-подписки в src/ вне allowlist ломают тест.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QWidget

from ui.qfluent_signal_guards import install_qfluent_theme_signal_guards


def _theme_receivers(qconfig) -> int:
    return int(qconfig.receivers(qconfig.themeChanged))


def _emit_theme_changed() -> None:
    from qfluentwidgets.common.config import qconfig

    qconfig.themeChanged.emit(qconfig.theme)


class QFluentSignalGuardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])
        install_qfluent_theme_signal_guards()

    def test_install_is_idempotent(self) -> None:
        from qfluentwidgets.common.config import qconfig

        before = _theme_receivers(qconfig)
        install_qfluent_theme_signal_guards()
        install_qfluent_theme_signal_guards()
        self.assertEqual(_theme_receivers(qconfig), before)

    def test_card_widget_does_not_add_per_widget_subscription(self) -> None:
        from qfluentwidgets import CardWidget
        from qfluentwidgets.common.config import qconfig

        before = _theme_receivers(qconfig)
        cards = [CardWidget() for _ in range(5)]
        self.assertEqual(_theme_receivers(qconfig), before)
        for card in cards:
            sip.delete(card)

    def test_label_does_not_add_per_widget_subscription(self) -> None:
        from qfluentwidgets import BodyLabel
        from qfluentwidgets.common.config import qconfig

        before = _theme_receivers(qconfig)
        labels = [BodyLabel("текст") for _ in range(5)]
        self.assertEqual(_theme_receivers(qconfig), before)
        for label in labels:
            sip.delete(label)

    def test_dead_card_does_not_break_theme_change(self) -> None:
        from qfluentwidgets import CardWidget

        card = CardWidget()
        sip.delete(card)  # обёртка жива, C++ мёртв — Nuitka-сценарий
        self.assertTrue(sip.isdeleted(card))
        _emit_theme_changed()  # не должно бросать RuntimeError

    def test_dead_label_does_not_break_theme_change(self) -> None:
        from qfluentwidgets import CaptionLabel

        label = CaptionLabel("текст")
        sip.delete(label)
        self.assertTrue(sip.isdeleted(label))
        _emit_theme_changed()

    def test_dead_widgets_are_pruned_from_registry(self) -> None:
        import ui.qfluent_signal_guards as guards
        from qfluentwidgets import CardWidget

        card = CardWidget()
        registry = guards._card_registry
        self.assertIsNotNone(registry)
        sip.delete(card)
        _emit_theme_changed()
        self.assertTrue(
            all(ref() is not card for ref in registry._refs),
            "мёртвый виджет должен вычищаться из диспетчера",
        )

    def test_live_card_still_receives_theme_updates(self) -> None:
        from qfluentwidgets import CardWidget

        card = CardWidget()
        self.addCleanup(lambda: sip.delete(card))
        calls: list[bool] = []
        card._updateBackgroundColor = lambda: calls.append(True)
        _emit_theme_changed()
        self.assertEqual(len(calls), 1, "живой виджет должен получить ровно один вызов")

    def test_live_label_still_receives_theme_updates(self) -> None:
        from qfluentwidgets import BodyLabel

        label = BodyLabel("текст")
        self.addCleanup(lambda: sip.delete(label))
        calls: list[tuple] = []
        label.setTextColor = lambda light, dark: calls.append((light, dark))
        _emit_theme_changed()
        self.assertEqual(len(calls), 1)

    def test_theme_refresh_binding_cleans_up_on_target_destroy(self) -> None:
        from ui.theme_refresh import ThemeRefreshBinding

        target = QWidget()
        binding = ThemeRefreshBinding(target, lambda: None)
        self.assertFalse(binding._cleanup_in_progress)
        sip.delete(target)
        self.assertTrue(
            binding._cleanup_in_progress,
            "destroyed цели должен снимать qconfig-подписки binding'а",
        )
        _emit_theme_changed()  # не должно бросать

    def test_theme_refresh_binding_guard_when_destroyed_missed(self) -> None:
        """Даже если destroyed не дошёл, сигнал по мёртвой цели чистится молча."""
        from ui.theme_refresh import ThemeRefreshBinding

        target = QWidget()
        binding = ThemeRefreshBinding(target, lambda: None)
        # Эмулируем пропуск destroyed: чистим флаг и зовём слот напрямую.
        sip.delete(target)
        binding._cleanup_in_progress = False
        binding._on_theme_signal()
        self.assertTrue(binding._cleanup_in_progress)


class QConfigSubscriptionSentinelTests(unittest.TestCase):
    """Ломается, если появились новые точки подписки на qconfig-сигналы.

    Каждая новая точка — потенциальный «wrapped C/C++ object has been
    deleted» в Nuitka-сборке. Добавляя строку в allowlist, убедитесь, что
    подписчик живёт весь процесс либо гарантированно отписывается
    (диспетчер из ui/qfluent_signal_guards.py, ThemeRefreshBinding).
    """

    _CONNECT_RE = re.compile(
        r"qconfig\s*\.\s*(themeChanged|themeColorChanged|themeChangedFinished)\s*\.\s*connect\s*\("
    )

    def _scan(self, root: Path) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in self._CONNECT_RE.finditer(text):
                found.add((path.name, match.group(1)))
        return found

    def test_qfluentwidgets_subscription_points_are_known(self) -> None:
        import qfluentwidgets

        root = Path(qfluentwidgets.__file__).resolve().parent
        expected = {
            ("animation.py", "themeChanged"),        # закрыт guard'ом (карточки)
            ("label.py", "themeChanged"),            # закрыт guard'ом (лейблы)
            ("fluent_window.py", "themeChangedFinished"),  # окна живут весь процесс
        }
        self.assertEqual(
            self._scan(root),
            expected,
            "Новая qconfig-подписка в qfluentwidgets: проверьте её жизненный цикл "
            "и добавьте guard в ui/qfluent_signal_guards.py при необходимости.",
        )

    def test_project_code_does_not_subscribe_widgets_directly(self) -> None:
        src_root = Path(__file__).resolve().parents[1] / "src"
        allowlist = {
            "theme_refresh.py",       # ThemeRefreshBinding: авто-cleanup по destroyed
            "qfluent_signal_guards.py",  # сам диспетчер
            "qt_runtime.py",          # модульная функция, живёт весь процесс
            "entry.py",               # главное окно, живёт весь процесс
            "theme.py",               # модульная функция, живёт весь процесс
        }
        offenders = {
            (name, signal)
            for name, signal in self._scan(src_root)
            if name not in allowlist
        }
        self.assertEqual(
            offenders,
            set(),
            "Прямые подписки виджетов на qconfig-сигналы запрещены: используйте "
            "ThemeRefreshBinding (ui/theme_refresh.py) — иначе в Nuitka-сборке "
            "соединение переживёт виджет и смена темы упадёт с RuntimeError.",
        )


if __name__ == "__main__":
    unittest.main()
