"""Весёлые живые элементы для экранов проверок (BlockCheck, подбор, DNS).

Все элементы подчиняются переключателю «живых анимаций»
(``ui.animation_policy.are_live_animations_enabled``) и в покое не тратят
процессор: анимация идёт только пока что-то происходит.

- ``Mascot`` — талисман-выдра со значка программы, у него есть настроение.
- ``burst_confetti`` — короткий салют поверх карточки.
- ``FunTicker`` — строка с меняющимися весёлыми фразами, пока идёт работа.
- ``StepList`` — шаги с галочками, которые рисуются штрихом.
- ``CounterBadge`` — счётчик, который подпрыгивает при изменении.
"""

from ui.widgets.fun.confetti import burst_confetti
from ui.widgets.fun.counter import CounterBadge
from ui.widgets.fun.mascot import Mascot
from ui.widgets.fun.steps import StepList
from ui.widgets.fun.ticker import FunTicker

__all__ = ["CounterBadge", "FunTicker", "Mascot", "StepList", "burst_confetti"]
