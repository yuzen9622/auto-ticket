"""G28 的 mutation 證明：以 in-memory AST fixture 驗證 gate 不是形式主義。

只解析字串常數，不觸碰 working tree、不執行 git、不寫任何 repo 內檔案。
"""

from __future__ import annotations

import ast
import sys
from importlib import import_module
from pathlib import Path

import pytest

from tests.netguard import netguard_autouse  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

check_invariants = import_module("check_invariants")

BAD = "await pw.chromium.connect_over_cdp(ws)"
GOOD = "await pw.chromium.connect_over_cdp(ws, timeout=1, no_defaults=True)"
FALSY = "await pw.chromium.connect_over_cdp(ws, no_defaults=False)"
TRUTHY_NOT_TRUE = "await pw.chromium.connect_over_cdp(ws, no_defaults=1)"


def test_gate_flags_missing_no_defaults() -> None:
    assert check_invariants.cdp_no_defaults_violations(ast.parse(BAD), "x.py")


def test_gate_accepts_explicit_true() -> None:
    assert check_invariants.cdp_no_defaults_violations(ast.parse(GOOD), "x.py") == []


@pytest.mark.parametrize("source", [FALSY, TRUTHY_NOT_TRUE])
def test_gate_rejects_anything_but_literal_true(source: str) -> None:
    assert check_invariants.cdp_no_defaults_violations(ast.parse(source), "x.py")


def test_gate_reports_the_offending_line() -> None:
    (message,) = check_invariants.cdp_no_defaults_violations(ast.parse(BAD), "x.py")
    assert message.startswith("x.py:1 ")


def test_gate_is_registered() -> None:
    assert check_invariants.g28_cdp_no_defaults in check_invariants.GATES


def test_the_real_attach_call_site_satisfies_the_gate() -> None:
    rel = "src/browser/manager.py"
    tree = ast.parse(
        (Path(check_invariants.REPO_ROOT) / rel).read_text(encoding="utf-8")
    )
    assert check_invariants.cdp_no_defaults_violations(tree, rel) == []
