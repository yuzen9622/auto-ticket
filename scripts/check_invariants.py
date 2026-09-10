#!/usr/bin/env python
"""機械化不變式守門員（G1–G28）。

驗證那些靠人眼審查不可靠、但可以機械化證明的專案不變式：凍結模組零改動、
測試不連外、無過時 API、打包與依賴約束完整，以及每個設計決策的靜態鎖定。

每條 gate 的 scope 都刻意收窄，只看它負責的那一小塊原始碼或測試。
**嚴禁**擴大成全 repo 掃描——新檔與 `pyproject.toml` / `requirements.txt` / `uv.lock`
的變更都屬預期範圍，全域比對必然假紅。

用法：`uv run --offline python scripts/check_invariants.py`
"""

from __future__ import annotations

import ast
import re
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent

# 已交付且凍結的模組與測試：本守門員負責證明它們沒有被順手改動。
PROTECTED_PATHS = (
    "src/storage",
    "src/adapters/ticketing/base.py",
    "src/adapters/ticketing/kktix/resolver.py",
    "src/adapters/ticketing/kktix/selectors.py",
    "docs",
    "tests/conftest.py",
    "tests/fixtures/kktix_event_page.html",
    "tests/fixtures/kktix_event_page_no_jsonld.html",
    "tests/fixtures/kktix_events_feed.json",
    "tests/unit/test_domain_models.py",
    "tests/unit/test_kktix_resolver.py",
    "tests/unit/test_storage.py",
    "tests/unit/test_timezone_invariant.py",
    "tests/integration/test_resolve_to_persist.py",
)
# 本守門員負責的引擎模組與其測試。
GUARDED_SRC_DIRS = (
    "src/telemetry",
    "src/fsm",
    "src/scheduler",
    "src/browser",
    "src/strategy",
    "src/purchase",
    "src/adapters/payment",
    "src/adapters/verification",
)
# 納管的單檔（不整個目錄納管，避免把既有未清理的模組一起拉進門檻）。
GUARDED_SRC_FILES = (
    "src/adapters/ticketing/kktix/adapter.py",
    "src/adapters/ticketing/kktix/dom.py",
)
ADAPTER_PATH = "src/adapters/ticketing/kktix/adapter.py"
MOCK_PAYMENT_PATH = "src/adapters/payment/mock.py"
AUTOMATED_PAYMENT_PATH = "src/adapters/payment/automated_credit_card.py"
ORCHESTRATOR_PATH = "src/purchase/orchestrator.py"
PAYMENT_BASE_PATH = "src/adapters/payment/base.py"
LIVE_DIR = "tests/live"
GUARDED_TEST_FILES = (
    "tests/netguard.py",
    "tests/unit/test_netguard.py",
    "tests/unit/test_timeline.py",
    "tests/unit/test_fsm.py",
    "tests/unit/test_clock_sync.py",
    "tests/unit/test_scheduler.py",
    "tests/unit/test_cdp_rtt.py",
    "tests/unit/test_browser_manager.py",
    "tests/integration/test_scheduler_to_fsm.py",
    "tests/fake_page.py",
    "tests/unit/test_ticket_strategy.py",
    "tests/unit/test_seat_strategy.py",
    "tests/unit/test_verification.py",
    "tests/unit/test_payment.py",
    "tests/unit/test_kktix_adapter.py",
    "tests/unit/test_purchase_orchestrator.py",
    "tests/integration/test_purchase_flow.py",
    "tests/unit/test_login_script.py",
    "tests/unit/test_cdp_attach.py",
    "tests/unit/test_run_purchase_cli.py",
    "tests/unit/test_invariant_gates.py",
)
# 沒有測試函式的測試輔助模組：不適用「必須掛 netguard fixture」這條。
TEST_HELPERS_WITHOUT_TESTS = ("tests/netguard.py", "tests/fake_page.py")
# 白名單放行／阻擋案例本來就必須寫出真實網域字面值，改用較窄規則把關。
G2_LITERAL_EXEMPT = ("tests/unit/test_clock_sync.py",)
RESERVED_TEST_HOST_SUFFIXES = (
    ".invalid",
    ".test",
    ".example",
    "example.com",
    "example.org",
    "example.net",
    "localhost",
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
FORBIDDEN_NTP_HOST = "pool.ntp.org"

URL_LITERAL_RE = re.compile(r"\A[a-zA-Z][a-zA-Z0-9+.\-]*://")

_failures: list[str] = []


def fail(gate: str, message: str) -> None:
    _failures.append(f"{gate}: {message}")


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def parse(rel: str) -> ast.Module:
    return ast.parse(read(rel), filename=rel)


def iter_src_files() -> Iterator[str]:
    for d in GUARDED_SRC_DIRS:
        for p in sorted((REPO_ROOT / d).rglob("*.py")):
            yield str(p.relative_to(REPO_ROOT))
    yield from GUARDED_SRC_FILES


def iter_live_files() -> Iterator[str]:
    for p in sorted((REPO_ROOT / LIVE_DIR).rglob("*.py")):
        yield str(p.relative_to(REPO_ROOT))


def is_docstring(module: ast.Module, node: ast.Constant) -> bool:
    for parent in ast.walk(module):
        if isinstance(
            parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(parent, "body", [])
            if body and isinstance(body[0], ast.Expr) and body[0].value is node:
                return True
    return False


def iter_script_files() -> Iterator[str]:
    for p in sorted((REPO_ROOT / "scripts").rglob("*.py")):
        yield str(p.relative_to(REPO_ROOT))


def call_name(node: ast.Call) -> str:
    try:
        return ast.unparse(node.func)
    except Exception:  # pragma: no cover - 防禦性
        return ""


def func_named(
    tree: ast.AST, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def method_of(
    tree: ast.AST, class_name: str, method: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == class_name:
            for m in n.body:
                if (
                    isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and m.name == method
                ):
                    return m
    return None


# ---------------------------------------------------------------- G1
def g1_frozen_paths_untouched() -> None:
    paths = [p for p in PROTECTED_PATHS if (REPO_ROOT / p).exists()]
    diff = subprocess.run(
        ["git", "diff", "--exit-code", "HEAD", "--", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        fail("G1", f"凍結模組有未提交變更:\n{diff.stdout[:2000]}")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        fail("G1", f"凍結模組狀態不乾淨:\n{status.stdout}")


# ---------------------------------------------------------------- G2
def g2_no_real_hosts_in_tests() -> None:
    for rel in GUARDED_TEST_FILES:
        src = read(rel)
        if FORBIDDEN_NTP_HOST in src:
            fail("G2", f"{rel} 出現真實 NTP 主機字面值 {FORBIDDEN_NTP_HOST!r}")
        tree = ast.parse(src, filename=rel)
        if rel in G2_LITERAL_EXEMPT:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and call_name(node) == "httpx.AsyncClient"
                    and not node.args
                    and not node.keywords
                ):
                    fail("G2", f"{rel}:{node.lineno} 建立了真實 httpx.AsyncClient()")
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            value = node.value
            if not URL_LITERAL_RE.match(value):
                continue
            host = (urlsplit(value).hostname or "").lower()
            if host in LOOPBACK_HOSTS:
                continue
            if not any(
                host == s.lstrip(".") or host.endswith(s)
                for s in RESERVED_TEST_HOST_SUFFIXES
            ):
                fail(
                    "G2",
                    f"{rel}:{node.lineno} 使用非保留測試網域 {host!r}（僅允許 RFC 2606 保留網域）",
                )


# ---------------------------------------------------------------- G3
def g3_no_real_playwright_in_tests() -> None:
    banned = ("async_playwright", "sync_playwright")
    for rel in GUARDED_TEST_FILES:
        tree = parse(rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if name in banned or name.endswith(
                (".launch_persistent_context", ".connect_over_cdp")
            ):
                fail("G3", f"{rel}:{node.lineno} 呼叫真實 Playwright 入口 {name!r}")


# ---------------------------------------------------------------- G4
def g4_no_deprecated_api() -> None:
    targets = (
        list(iter_src_files()) + list(GUARDED_TEST_FILES) + list(iter_script_files())
    )
    for rel in targets:
        tree = parse(rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "current_state":
                fail("G4", f"{rel}:{node.lineno} 使用已棄用的 `current_state`")
            if isinstance(node, ast.Call) and call_name(node) == "time.sleep":
                fail("G4", f"{rel}:{node.lineno} 裸呼叫 `time.sleep()` 會阻塞事件迴圈")


# ---------------------------------------------------------------- G5
def g5_no_skipped_tests() -> None:
    banned = {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail"}
    for rel in GUARDED_TEST_FILES:
        tree = parse(rel)
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                name = ast.unparse(target)
                if name in banned:
                    fail("G5", f"{rel}:{node.lineno} 測試被 {name} 跳過")


# ---------------------------------------------------------------- G6
def g6_no_eager_heavy_imports() -> None:
    banned = ("playwright", "ntplib")
    for rel in iter_src_files():
        tree = parse(rel)
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                if any(n == b or n.startswith(f"{b}.") for b in banned):
                    fail(
                        "G6",
                        f"{rel}:{node.lineno} 頂層 eager import {n!r}（必須改為函式內 lazy import）",
                    )


# ---------------------------------------------------------------- G7
def g7_packaging_and_deps() -> None:
    src = read("pyproject.toml")
    if 'sources = ["src"]' not in src:
        fail(
            "G7",
            'pyproject.toml 缺少 [tool.hatch.build.targets.wheel].sources = ["src"]',
        )
    for pkg in (
        "src/domain",
        "src/storage",
        "src/adapters",
        "src/telemetry",
        "src/fsm",
        "src/scheduler",
        "src/browser",
        "src/strategy",
        "src/purchase",
    ):
        if f'"{pkg}"' not in src:
            fail("G7", f"pyproject.toml wheel packages 缺少 {pkg!r}")
    existing_constraints = (
        '"pydantic>=2.12"',
        '"sqlalchemy[asyncio]>=2.0.44"',
        '"aiosqlite>=0.21"',
        '"rapidfuzz>=3.14"',
        '"httpx>=0.28"',
        '"beautifulsoup4>=4.14"',
    )
    for c in existing_constraints:
        if c not in src:
            fail("G7", f"pyproject.toml 遺失或放寬了既有依賴約束 {c}")
    for c in (
        '"apscheduler>=3.10.4"',
        '"ntplib>=0.4.0"',
        '"playwright>=1.60"',
        '"python-statemachine>=3.2.0"',
        '"structlog>=24.1.0"',
    ):
        if c not in src:
            fail("G7", f"pyproject.toml 缺少引擎依賴 {c}")
    if '"ruff>=' not in src:
        fail("G7", "pyproject.toml 的 dev 依賴缺少 ruff（驗收 #20/#34 需要）")


# ---------------------------------------------------------------- G8
def g8_netguard_mounted() -> None:
    for rel in GUARDED_TEST_FILES:
        if rel in TEST_HELPERS_WITHOUT_TESTS:
            continue
        tree = parse(rel)
        imported = any(
            isinstance(n, ast.ImportFrom)
            and (n.module or "").endswith("netguard")
            and any(a.name == "netguard_autouse" for a in n.names)
            for n in ast.walk(tree)
        )
        if imported:
            continue
        own_fixture = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_autouse = any(
                isinstance(d, ast.Call)
                and ast.unparse(d.func) == "pytest.fixture"
                and any(
                    k.arg == "autouse" and getattr(k.value, "value", False) is True
                    for k in d.keywords
                )
                for d in node.decorator_list
            )
            if has_autouse and "no_network()" in (
                ast.get_source_segment(read(rel), node) or ""
            ):
                own_fixture = True
        if not own_fixture:
            fail("G8", f"{rel} 未掛載 netguard autouse fixture")


# ---------------------------------------------------------------- G9
def g9_netguard_effective() -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from tests.netguard import NetworkAccessError, no_network

    def blocked(fn: object, label: str) -> None:
        try:
            fn()  # type: ignore[operator]
        except NetworkAccessError:
            return
        except Exception as exc:  # pragma: no cover - 防禦性
            fail("G9", f"{label} 拋出非預期例外 {exc!r}")
            return
        fail("G9", f"{label} 未被攔截")

    with no_network():
        for family, addr in (
            (socket.AF_INET, ("192.0.2.1", 80)),
            (socket.AF_INET6, ("2001:db8::1", 80)),
        ):
            s = socket.socket(family, socket.SOCK_STREAM)
            try:
                blocked(
                    lambda s=s, addr=addr: s.connect(addr), f"TCP connect {family!r}"
                )
            finally:
                s.close()
            u = socket.socket(family, socket.SOCK_DGRAM)
            try:
                blocked(
                    lambda u=u, addr=addr: u.sendto(b"x", addr),
                    f"UDP sendto {family!r}",
                )
            finally:
                u.close()
        blocked(lambda: socket.getaddrinfo("db.invalid", 80), "DNS getaddrinfo")
        blocked(lambda: socket.gethostbyname("db.invalid"), "DNS gethostbyname")
        try:
            socket.getaddrinfo("localhost", 80)
        except Exception as exc:
            fail("G9", f"loopback 名稱解析被誤擋: {exc!r}")
        try:
            a, b = socket.socketpair()
            a.close()
            b.close()
        except Exception as exc:
            fail("G9", f"AF_UNIX socketpair 被誤擋: {exc!r}")


# ---------------------------------------------------------------- G10
def g10_netguard_restore_by_assign() -> None:
    rel = "tests/netguard.py"
    src = read(rel)
    tree = ast.parse(src, filename=rel)
    if "delattr" in src:
        fail("G10", "tests/netguard.py 出現 delattr（必須以重新賦值還原）")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) == "delattr":
            fail("G10", f"{rel}:{node.lineno} 呼叫 delattr")
    if "socket.socket.connect = _orig_connect" not in src:
        fail(
            "G10",
            "tests/netguard.py 缺少 `socket.socket.connect = _orig_connect` 還原賦值",
        )
    fn = func_named(tree, "no_network")
    if fn is None:
        fail("G10", "tests/netguard.py 缺少 no_network()")
        return
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Constant) and node.value.value is None:
            for t in node.targets:
                name = ast.unparse(t)
                if name.startswith("_orig_"):
                    fail(
                        "G10",
                        f"{rel}:{node.lineno} 把 {name} 清成 None（identity 斷言將無法成立）",
                    )


# ---------------------------------------------------------------- G11
def g11_cdp_rlock() -> None:
    rel = "src/browser/cdp_rtt.py"
    src = read(rel)
    if "threading.RLock()" not in src:
        fail("G11", f"{rel} 未使用 threading.RLock()")
    if "threading.Lock()" in src:
        fail("G11", f"{rel} 出現非重入的 threading.Lock()")
    fn = method_of(
        ast.parse(src, filename=rel), "CdpRttTracker", "on_request_will_be_sent"
    )
    if fn is None:
        fail("G11", f"{rel} 缺少 on_request_will_be_sent")
    elif "self.prune(" in (ast.get_source_segment(src, fn) or ""):
        fail("G11", f"{rel} on_request_will_be_sent 持鎖中呼叫 public prune()（自鎖）")
    if "_prune_locked" not in src:
        fail("G11", f"{rel} 缺少持鎖版 _prune_locked()")


# ---------------------------------------------------------------- G12 / G13
def g12_g13_scheduler_regressions() -> None:
    rel = "src/scheduler/scheduler.py"
    src = read(rel)
    if "stage != WarmupStage.TRIGGER_PURCHASE" in src:
        fail("G12", f"{rel} 把 TRIGGER_PURCHASE 排除於中止路徑（fail-open 回歸）")
    if "max(sp.planned_local_at" in src:
        fail("G13", f"{rel} 以夾擠值重掛過期 job（expired-job 回歸）")


# ---------------------------------------------------------------- G14
def g14_screenshot_counter() -> None:
    rel = "src/browser/manager.py"
    src = read(rel)
    if src.count("itertools.count(") != 1:
        fail(
            "G14",
            f"{rel} itertools.count( 出現 {src.count('itertools.count(')} 次（必須恰一次）",
        )
    tree = ast.parse(src, filename=rel)
    hosts = [
        f.name
        for f in ast.walk(tree)
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "itertools.count(" in (ast.get_source_segment(src, f) or "")
    ]
    if hosts != ["__init__"]:
        fail("G14", f"{rel} 截圖計數器不在 __init__ 內（實際位於 {hosts}）")


# ---------------------------------------------------------------- G15
def g15_frozen2_allowlist() -> None:
    rel = "src/scheduler/clock_sync.py"
    src = read(rel)
    tree = ast.parse(src, filename=rel)
    found = False
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and ast.unparse(node.target) == "DEFAULT_KKTIX_ALLOWED_HOSTS"
        ):
            found = True
            if node.value is None or ast.literal_eval(node.value) != (
                "kktix.com",
                ".kktix.cc",
            ):
                fail("G15", f"{rel} DEFAULT_KKTIX_ALLOWED_HOSTS 值遭放寬")
    if not found:
        fail("G15", f"{rel} 未宣告 DEFAULT_KKTIX_ALLOWED_HOSTS")
    fn = method_of(tree, "ServerHeaderClockSync", "sample")
    if fn is None:
        fail("G15", f"{rel} 缺少 ServerHeaderClockSync.sample")
    else:
        body = list(fn.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            body = body[1:]  # 跳過 docstring
        first = ast.unparse(body[0]) if body else ""
        if first != "self.assert_url_allowed(url)":
            fail(
                "G15", f"{rel} sample() 首句不是 assert_url_allowed（實際: {first!r}）"
            )
    if "follow_redirects=True" in src:
        fail("G15", f"{rel} 允許跟隨轉址，等同 allowlist 側門")


# ---------------------------------------------------------------- G16
def g16_per_task_clock_sync() -> None:
    rel = "src/scheduler/scheduler.py"
    src = read(rel)
    tree = ast.parse(src, filename=rel)
    if "._server_url =" in src:
        fail("G16", f"{rel} 直接寫入同步器私有屬性 _server_url")
    init = method_of(tree, "WarmupScheduler", "__init__")
    if init is None:
        fail("G16", f"{rel} 缺少 WarmupScheduler.__init__")
    else:
        args = [a.arg for a in init.args.args + init.args.kwonlyargs]
        if "clock_synchronizer_factory" not in args:
            fail(
                "G16",
                f"{rel} WarmupScheduler.__init__ 缺少 clock_synchronizer_factory 參數",
            )
    fields = _dataclass_fields(tree, "TaskSchedule")
    for required in ("clock_sync", "stage_lock", "readiness_deferred"):
        if required not in fields:
            fail("G16", f"{rel} TaskSchedule 缺少 {required} 欄位")


def _dataclass_fields(tree: ast.AST, class_name: str) -> set[str]:
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == class_name:
            return {
                ast.unparse(m.target) for m in n.body if isinstance(m, ast.AnnAssign)
            }
    return set()


# ---------------------------------------------------------------- G17
def g17_stage_serialized() -> None:
    rel = "src/scheduler/scheduler.py"
    src = read(rel)
    tree = ast.parse(src, filename=rel)
    for required in ("_run_stage_locked", "_ensure_sale_ready_locked"):
        if method_of(tree, "WarmupScheduler", required) is None:
            fail("G17", f"{rel} 缺少 {required}()")
    for name in ("_run_stage", "_ensure_sale_ready", "_spin_and_trigger"):
        fn = method_of(tree, "WarmupScheduler", name)
        if fn is None:
            fail("G17", f"{rel} 缺少 {name}()")
            continue
        has_lock = any(
            isinstance(n, ast.AsyncWith)
            and any(
                "schedule.stage_lock" in ast.unparse(item.context_expr)
                for item in n.items
            )
            for n in ast.walk(fn)
        )
        if not has_lock:
            fail("G17", f"{rel} {name}() 未持有 schedule.stage_lock")


# ---------------------------------------------------------------- G18
def g18_screenshot_hook_threadsafe() -> None:
    rel = "src/browser/manager.py"
    src = read(rel)
    fn = method_of(
        ast.parse(src, filename=rel), "PlaywrightManager", "make_screenshot_hook"
    )
    if fn is None:
        fail("G18", f"{rel} 缺少 make_screenshot_hook")
        return
    names = [call_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)]
    if "asyncio.create_task" in names:
        fail("G18", f"{rel} hook 在呼叫端執行緒直接 asyncio.create_task()")
    if not any(n.endswith("call_soon_threadsafe") for n in names):
        fail("G18", f"{rel} hook 未使用 call_soon_threadsafe 跨執行緒排程")


# ---------------------------------------------------------------- G19
def g19_readiness_single_owner() -> None:
    rel = "src/scheduler/scheduler.py"
    src = read(rel)
    tree = ast.parse(src, filename=rel)
    if "readiness_deferred" not in src:
        fail("G19", f"{rel} 缺少 readiness_deferred")
    if "PRE_SALE_STAGES" not in src:
        fail("G19", f"{rel} 缺少 PRE_SALE_STAGES")
    cls = None
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == "WarmupScheduler":
            cls = n
    if cls is None:
        fail("G19", f"{rel} 缺少 WarmupScheduler")
        return
    lits = [
        n.value
        for n in ast.walk(cls)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    for marker in ("sale_readiness_catch_up", "sale_readiness_recovered"):
        if lits.count(marker) != 1:
            fail(
                "G19",
                f"{rel} {marker!r} 發射點有 {lits.count(marker)} 個（必須恰一個 owner）",
            )


# --------------------------------------------------------------- G20
# 領域模型解凍後只准新增：既有欄位的名稱與型別必須逐字保留。
# 以「黃金清單」比對而非與 git HEAD 比對——後者在提交後會自動變成恆真。
DOMAIN_FROZEN_FIELDS = {
    "src/domain/task.py": {
        "CreditCardProfile": {
            "card_number": "str",
            "expiry_month": "str",
            "expiry_year": "str",
            "cvv": "str",
            "cardholder_name": "str",
        },
        "UserContactProfile": {"name": "str", "phone": "str", "email": "str"},
        "PurchaseTaskSpec": {
            "task_id": "str",
            "event_title": "str",
            "event_url": "str",
            "sale_start_at": "UtcDatetime",
            "ticket_preference": "TicketPreference",
            "contact_profile": "UserContactProfile",
            "payment_method": "PaymentMethod",
            "payment_profile": "CreditCardProfile | None",
            "max_retries": "int",
            "timeout_seconds": "int",
        },
        "PurchaseTaskRecord": {
            "id": "str",
            "event_id": "str | None",
            "status": "TaskStatus",
            "spec": "dict[str, Any]",
            "scheduled_at": "UtcDatetime | None",
            "started_at": "UtcDatetime | None",
            "finished_at": "UtcDatetime | None",
            "error_message": "str | None",
            "created_at": "UtcDatetime",
        },
    },
    "src/domain/preference.py": {
        "TicketPriority": {
            "price": "int",
            "ticket_name_pattern": "str | None",
            "priority": "int",
        },
        "SeatPreference": {
            "adjacent": "bool",
            "strategy": "Literal['best_available', 'same_zone', 'specific_zone']",
            "preferred_zones": "list[str]",
        },
        "TicketPreference": {
            "quantity": "int",
            "priorities": "list[TicketPriority]",
            "seat_preference": "SeatPreference",
            "fallback_to_any": "bool",
        },
    },
}


def class_fields(tree: ast.Module) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        result[node.name] = {
            stmt.target.id: ast.unparse(stmt.annotation)
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }
    return result


def g20_domain_additive_only() -> None:
    for rel, expected_classes in DOMAIN_FROZEN_FIELDS.items():
        actual = class_fields(parse(rel))
        for cls, expected in expected_classes.items():
            if cls not in actual:
                fail("G20", f"{rel} 移除了既有領域類別 {cls!r}")
                continue
            for name, annotation in expected.items():
                if name not in actual[cls]:
                    fail("G20", f"{rel}:{cls} 移除了既有欄位 {name!r}")
                elif actual[cls][name] != annotation:
                    fail(
                        "G20",
                        f"{rel}:{cls}.{name} 型別由 {annotation!r} 改為 {actual[cls][name]!r}",
                    )


# --------------------------------------------------------------- G21
def g21_netguard_scope() -> None:
    """納管的離線測試檔一律掛 netguard；tests/live 一律不掛（那是唯一允許連外處）。"""
    for rel in GUARDED_TEST_FILES:
        if rel in TEST_HELPERS_WITHOUT_TESTS:
            continue
        if "netguard_autouse" not in read(rel):
            fail("G21", f"{rel} 未掛載 netguard autouse fixture")
    for rel in iter_live_files():
        if "netguard" in read(rel):
            fail("G21", f"{rel} 掛載了 netguard；實站套件必須能連外")


# --------------------------------------------------------------- G22
SELECTOR_LITERAL_PREFIXES = (".", "#", "[")


def g22_no_selector_literals_in_adapter() -> None:
    tree = parse(ADAPTER_PATH)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if is_docstring(tree, node):
            continue
        if node.value.startswith(SELECTOR_LITERAL_PREFIXES):
            fail("G22", f"{ADAPTER_PATH}:{node.lineno} 出現選擇器字面值 {node.value!r}")
    uses_registry = any(
        isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "KKTIXSelectors"
        for n in ast.walk(tree)
    )
    if not uses_registry:
        fail("G22", f"{ADAPTER_PATH} 未經 KKTIXSelectors 取用任何選擇器")


# --------------------------------------------------------------- G23
def g23_mock_never_touches_submit_button() -> None:
    """Mock provider 連「確認付款」按鈕都不得引用，遑論點擊。"""
    tree = parse(MOCK_PAYMENT_PATH)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "BTN_CONFIRM_PAYMENT":
            fail("G23", f"{MOCK_PAYMENT_PATH}:{node.lineno} 引用了送出付款按鈕")
        if isinstance(node, ast.Constant) and node.value == "BTN_CONFIRM_PAYMENT":
            fail("G23", f"{MOCK_PAYMENT_PATH}:{node.lineno} 以字串引用送出付款按鈕")


# --------------------------------------------------------------- G24
def enum_members(rel: str, class_name: str) -> set[str]:
    for node in parse(rel).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                stmt.targets[0].id
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            }
    return set()


def g24_payment_mapping_is_a_single_table() -> None:
    tree = parse(ORCHESTRATOR_PATH)
    mapping: ast.Dict | None = None
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "PAYMENT_OUTCOME_EVENTS"):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Dict):
                mapping = child
                break
    if mapping is None:
        fail("G24", f"{ORCHESTRATOR_PATH} 找不到 PAYMENT_OUTCOME_EVENTS 的 dict 常數")
        return
    keys: set[str] = set()
    for key in mapping.keys:
        if (
            isinstance(key, ast.Attribute)
            and isinstance(key.value, ast.Name)
            and key.value.id == "PaymentOutcome"
        ):
            keys.add(key.attr)
        else:
            fail("G24", f"{ORCHESTRATOR_PATH} 對應表出現非 PaymentOutcome 的鍵")
    expected = enum_members(PAYMENT_BASE_PATH, "PaymentOutcome")
    if keys != expected:
        fail(
            "G24",
            f"付款對應表未涵蓋全部結果：缺 {sorted(expected - keys)}，多 {sorted(keys - expected)}",
        )


# --------------------------------------------------------------- G25
CARD_SECRET_ATTRS = {"card_number", "cvv"}
SINK_NAME_PARTS = ("log", "logger", "print")


def _leaks_card_secret(node: ast.AST) -> str | None:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in CARD_SECRET_ATTRS:
            return child.attr
        if isinstance(child, ast.keyword) and child.arg in CARD_SECRET_ATTRS:
            return str(child.arg)
    return None


def g25_no_card_secrets_in_sinks() -> None:
    for rel in iter_src_files():
        tree = parse(rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                leak = _leaks_card_secret(node.exc)
                if leak:
                    fail("G25", f"{rel}:{node.lineno} 例外訊息帶入卡片機密 {leak!r}")
                continue
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            is_sink = (
                name.startswith("telemetry.record")
                or ".record" in name
                or any(part in name.lower() for part in SINK_NAME_PARTS)
            )
            if not is_sink:
                continue
            for arg in list(node.args) + list(node.keywords):
                leak = _leaks_card_secret(arg)
                if leak:
                    fail(
                        "G25", f"{rel}:{node.lineno} 對 {name!r} 傳入卡片機密 {leak!r}"
                    )
    automated = read(AUTOMATED_PAYMENT_PATH)
    for switch in ("allow_real_payment", "AUTO_TICKET_ENABLE_REAL_PAYMENT"):
        if switch not in automated:
            fail("G25", f"{AUTOMATED_PAYMENT_PATH} 缺少真實刷卡開關 {switch!r}")


# --------------------------------------------------------------- G26
LIVE_FORBIDDEN_CALLS = ("submit_order", "execute_payment", "pay", "confirmOrder")
LIVE_BANNED_MARKS = {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail"}


def g26_live_suite_is_read_only() -> None:
    """實站套件預設不跑靠 ignore，不靠跳過標記；且一律不得送出。"""
    if "--ignore=tests/live" not in read("pyproject.toml"):
        fail("G26", "pyproject.toml 的 addopts 缺少 --ignore=tests/live")
    for rel in iter_live_files():
        tree = parse(rel)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for deco in node.decorator_list:
                    target = deco.func if isinstance(deco, ast.Call) else deco
                    if ast.unparse(target) in LIVE_BANNED_MARKS:
                        fail("G26", f"{rel}:{node.lineno} 用跳過標記代替 --live 旗標")
            if isinstance(node, ast.Call):
                name = call_name(node)
                if name.rsplit(".", 1)[-1] in LIVE_FORBIDDEN_CALLS:
                    fail("G26", f"{rel}:{node.lineno} 呼叫了送出動作 {name!r}")


# --------------------------------------------------------------- G27
def g27_strategy_layer_is_pure() -> None:
    """決策層不得接觸 DOM：策略只吃快照，這是它能被大量決定性測試的前提。"""
    for p in sorted((REPO_ROOT / "src/strategy").rglob("*.py")):
        rel = str(p.relative_to(REPO_ROOT))
        tree = parse(rel)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                if n == "playwright" or n.startswith("playwright."):
                    fail(
                        "G27",
                        f"{rel}:{getattr(node, 'lineno', 0)} 決策層 import 了 playwright",
                    )
            if isinstance(node, ast.Name) and node.id in {"Page", "Locator"}:
                fail("G27", f"{rel}:{node.lineno} 決策層出現 DOM 型別 {node.id!r}")


# --------------------------------------------------------------- G28
def cdp_no_defaults_violations(tree: ast.AST, rel: str) -> list[str]:
    """所有 `connect_over_cdp` 呼叫必須顯式帶 `no_defaults=True`。

    否則 Playwright 會對「借來的」預設 context 套用自己的 downloads / focus / media 覆寫。
    """
    out: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and call_name(node).endswith(".connect_over_cdp")
        ):
            continue
        ok = any(
            k.arg == "no_defaults"
            and isinstance(k.value, ast.Constant)
            and isinstance(k.value.value, bool)
            and k.value.value
            for k in node.keywords
        )
        if not ok:
            out.append(
                f"{rel}:{getattr(node, 'lineno', 0)} connect_over_cdp 未帶 no_defaults=True"
            )
    return out


def g28_cdp_no_defaults() -> None:
    for rel in list(iter_src_files()) + list(iter_script_files()):
        for msg in cdp_no_defaults_violations(parse(rel), rel):
            fail("G28", msg)


GATES = (
    g1_frozen_paths_untouched,
    g2_no_real_hosts_in_tests,
    g3_no_real_playwright_in_tests,
    g4_no_deprecated_api,
    g5_no_skipped_tests,
    g6_no_eager_heavy_imports,
    g7_packaging_and_deps,
    g8_netguard_mounted,
    g9_netguard_effective,
    g10_netguard_restore_by_assign,
    g11_cdp_rlock,
    g12_g13_scheduler_regressions,
    g14_screenshot_counter,
    g15_frozen2_allowlist,
    g16_per_task_clock_sync,
    g17_stage_serialized,
    g18_screenshot_hook_threadsafe,
    g19_readiness_single_owner,
    g20_domain_additive_only,
    g21_netguard_scope,
    g22_no_selector_literals_in_adapter,
    g23_mock_never_touches_submit_button,
    g24_payment_mapping_is_a_single_table,
    g25_no_card_secrets_in_sinks,
    g26_live_suite_is_read_only,
    g27_strategy_layer_is_pure,
    g28_cdp_no_defaults,
)


def main() -> int:
    for gate in GATES:
        try:
            gate()
        except FileNotFoundError as exc:
            fail(gate.__name__, f"缺少檔案: {exc}")
    if _failures:
        print("FAILED invariant guards:", file=sys.stderr)
        for f in _failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK: all invariant guards passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
