"""No hard-coded language tests outside the language profiles (docs/GENERIC_LANGUAGES.md, 3 and 6).

Code asks a language profile ("is this script Han?", "which quotes?") instead
of testing for English or Chinese directly. Phase 1 moved every such test into
book_agent/languages.py; this keeps new ones from creeping back in.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "book_agent"

# Direction enum members, language enum members, comparisons with a language
# code, and Han ranges, whether written as \u escapes or literal characters.
PATTERN = re.compile(
    r"EN_TO_ZH|ZH_TO_EN|Language\.(?:CHINESE|ENGLISH)"
    r"|[=!]=\s*[\"'](?:zh|en)[\"']"
    r"|startswith\(\s*[\"'](?:zh|en)[\"']"
    r"|\\u(?:4e00|3400|9fff|4dbf|f900|faff)"
    "|[\u3400\u4e00\uf900]-[\u4dbf\u9fff\ufaff]"
)

# Where language knowledge is supposed to live.
EXEMPT = {"languages.py"}


def _sites() -> dict[str, int]:
    found = {}
    for path in sorted(ROOT.rglob("*.py")):
        if path.name in EXEMPT:
            continue
        count = len(PATTERN.findall(path.read_text(encoding="utf-8")))
        if count:
            found[path.relative_to(ROOT).as_posix()] = count
    return found


def test_no_hard_coded_language_tests():
    sites = _sites()
    assert not sites, (
        f"hard-coded language tests (file: count): {sites}. "
        "Ask the language profile instead (docs/GENERIC_LANGUAGES.md, 2.2)."
    )


def test_the_scanner_still_sees_each_form():
    samples = [
        "if direction is TranslationDirection.EN_TO_ZH:",
        "if language is Language.CHINESE:",
        'if target != "zh":',
        'report["direction"].startswith("en")',
        're.compile(r"[@u4e00-@u9fff]")'.replace("@u", "\\u"),
        "re.compile('[" + chr(0x4e00) + "-" + chr(0x9fff) + "]')",
    ]
    assert [bool(PATTERN.search(sample)) for sample in samples] == [True] * len(samples)
