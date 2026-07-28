"""Движок вердиктов BlockCheck — чистая логика, без сети и без Qt.

Разделение на слои:

``baseline``    что вообще работает на этой машине (контрольная группа);
``signatures``  факты одной цели → исход и сигнатура DPI;
``aggregate``   исходы всех целей → вердикт отчёта.

Логика вердиктов раньше была размазана по ``dpi_classifier``, ``runner``,
``preflight`` и ``ui/summary_content`` — четыре места с несогласованными
правилами и без единого теста.
"""

from blockcheck.verdict.aggregate import build_report_verdict
from blockcheck.verdict.baseline import probe_baseline
from blockcheck.verdict.signatures import TargetJudgement, judge_target

__all__ = [
    "TargetJudgement",
    "build_report_verdict",
    "judge_target",
    "probe_baseline",
]
