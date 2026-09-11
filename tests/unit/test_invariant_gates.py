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


# ------------------------------------------------------------------ G29
CREDENTIAL_LEAKS = (
    "print(secret_token)",
    'logger.info(f"{secret_token}")',
    'telemetry.record("mark", username=u)',
    'telemetry.record("mark", password=p)',
    'Path("leak.txt").write_text(secret_token)',
    'Path("leak.bin").write_bytes(secret_token)',
    "page.screenshot(path=secret_token)",
    "page.save_screenshot(secret_token)",
    "raise ValueError(secret_token)",
)


@pytest.mark.parametrize("source", CREDENTIAL_LEAKS)
def test_g29_flags_every_credential_sink(source: str) -> None:
    assert check_invariants.credential_leak_violations(ast.parse(source), "x.py")


def test_g29_reports_the_offending_line() -> None:
    (message,) = check_invariants.credential_leak_violations(
        ast.parse("print(secret_token)"), "x.py"
    )
    assert message.startswith("x.py:1 ")


@pytest.mark.parametrize(
    "source",
    [
        "print(order_id)",
        'logger.info("auto_login_started")',
        'telemetry.record("mark", attempt=1)',
        'Path("out.txt").write_text(summary)',
        "page.screenshot(path=shot_name)",
        "raise ValueError(reason)",
    ],
)
def test_g29_does_not_flag_clean_sinks(source: str) -> None:
    assert check_invariants.credential_leak_violations(ast.parse(source), "x.py") == []


def test_g29_does_not_mistake_login_for_a_logging_sink() -> None:
    """`adapter.login(page, username, secret_token)` 是合法呼叫，不是日誌外洩。"""
    source = "await self.adapter.login(page, username, secret_token)"
    assert check_invariants.credential_leak_violations(ast.parse(source), "x.py") == []


def test_g29_scans_both_src_and_scripts() -> None:
    scanned = set(check_invariants.iter_all_project_py_files())
    assert "src/purchase/orchestrator.py" in scanned
    assert "scripts/run_purchase.py" in scanned


def test_g29_is_registered() -> None:
    assert check_invariants.g29_no_credentials_in_sinks in check_invariants.GATES


def test_g29_rejects_a_credential_field_on_the_domain_model() -> None:
    tree = ast.parse("class PurchaseTaskSpec:\n    user_password: str\n")
    fields = check_invariants.class_fields(tree)
    assert any(
        check_invariants.is_credential_name(name)
        for name in fields["PurchaseTaskSpec"]
    )


def test_the_real_project_has_no_credential_sinks() -> None:
    for rel in check_invariants.iter_all_project_py_files():
        assert check_invariants.credential_leak_violations(
            check_invariants.parse(rel), rel
        ) == []
