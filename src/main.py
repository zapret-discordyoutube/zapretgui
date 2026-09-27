"""Точка входа Zapret.exe.

Сначала проверка запуска (main.launch_gate): пока она не пройдена, из
программы не импортируется ничего. Затем сама программа; если она упадёт
ещё до своего окна, пользователь увидит понятное окно, а не тишину.
"""

from main.launch_gate import pass_launch_gate, run_guarded

pass_launch_gate()

import main.process_start_time  # noqa: E402,F401  # первым после проверки запуска


def _run() -> None:
    from main.prelaunch import prepare_prelaunch

    prepare_prelaunch()
    from main.entry import main as run_main

    run_main()


if __name__ == "__main__":
    run_guarded(_run)
