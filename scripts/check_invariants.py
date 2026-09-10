#!/usr/bin/env python
"""機械化不變式守門員（G1–G19）。

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
    "src/domain",
    "src/storage",
    "src/adapters",
    "docs",
    "tests/conftest.py",
    "tests/fixtures",
    "tests/unit/test_domain_models.py",
    "tests/unit/test_kktix_resolver.py",
    "tests/unit/test_storage.py",
    "tests/unit/test_timezone_invariant.py",
    "tests/integration/test_resolve_to_persist.py",
)
# 本守門員負責的引擎模組與其測試。
GUARDED_SRC_DIRS = ("src/telemetry", "src/fsm", "src/scheduler", "src/browser")
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
)
# [FROZEN-2] 白名單放行／阻擋案例本來就必須寫出真實網域字面值，改用較窄規則把關。
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


def iter_script_files() -> Iterator[str]:
    for p in sorted((REPO_ROOT / "scripts").rglob("*.py")):
        yield str(p.relative_to(REPO_ROOT))


def call_name(node: ast.Call) -> str:
    try:
        return ast.unparse(node.func)
    except Exception:  # pragma: no cover - 防禦性
        return ""


def func_named(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def method_of(tree: ast.AST, class_name: str, method: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == class_name:
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == method:
                    return m
    return None


# ---------------------------------------------------------------- G1
def g1_frozen_paths_untouched() -> None:
    paths = [p for p in PROTECTED_PATHS if (REPO_ROOT / p).exists()]
    diff = subprocess.run(
        ["git", "diff", "--exit-code", "HEAD", "--", *paths],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if diff.returncode != 0:
        fail("G1", f"凍結模組有未提交變更:\n{diff.stdout[:2000]}")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        cwd=REPO_ROOT, capture_output=True, text=True,
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
            if not any(host == s.lstrip(".") or host.endswith(s) for s in RESERVED_TEST_HOST_SUFFIXES):
                fail("G2", f"{rel}:{node.lineno} 使用非保留測試網域 {host!r}（僅允許 RFC 2606 保留網域）")


# ---------------------------------------------------------------- G3
def g3_no_real_playwright_in_tests() -> None:
    banned = ("async_playwright", "sync_playwright")
    for rel in GUARDED_TEST_FILES:
        tree = parse(rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if name in banned or name.endswith(".launch_persistent_context"):
                fail("G3", f"{rel}:{node.lineno} 呼叫真實 Playwright 入口 {name!r}")


# ---------------------------------------------------------------- G4
def g4_no_deprecated_api() -> None:
    targets = list(iter_src_files()) + list(GUARDED_TEST_FILES) + list(iter_script_files())
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
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
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
                    fail("G6", f"{rel}:{node.lineno} 頂層 eager import {n!r}（必須改為函式內 lazy import）")


# ---------------------------------------------------------------- G7
def g7_packaging_and_deps() -> None:
    src = read("pyproject.toml")
    if 'sources = ["src"]' not in src:
        fail("G7", "pyproject.toml 缺少 [tool.hatch.build.targets.wheel].sources = [\"src\"]")
    for pkg in ("src/domain", "src/storage", "src/adapters", *GUARDED_SRC_DIRS):
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
    for c in ('"apscheduler>=3.10.4"', '"ntplib>=0.4.0"', '"playwright>=1.40.0"',
              '"python-statemachine>=3.2.0"', '"structlog>=24.1.0"'):
        if c not in src:
            fail("G7", f"pyproject.toml 缺少引擎依賴 {c}")
    if '"ruff>=' not in src:
        fail("G7", "pyproject.toml 的 dev 依賴缺少 ruff（驗收 #20/#34 需要）")


# ---------------------------------------------------------------- G8
def g8_netguard_mounted() -> None:
    for rel in GUARDED_TEST_FILES:
        if rel == "tests/netguard.py":
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
                and any(k.arg == "autouse" and getattr(k.value, "value", False) is True for k in d.keywords)
                for d in node.decorator_list
            )
            if has_autouse and "no_network()" in (ast.get_source_segment(read(rel), node) or ""):
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
        for family, addr in ((socket.AF_INET, ("192.0.2.1", 80)), (socket.AF_INET6, ("2001:db8::1", 80))):
            s = socket.socket(family, socket.SOCK_STREAM)
            try:
                blocked(lambda s=s, addr=addr: s.connect(addr), f"TCP connect {family!r}")
            finally:
                s.close()
            u = socket.socket(family, socket.SOCK_DGRAM)
            try:
                blocked(lambda u=u, addr=addr: u.sendto(b"x", addr), f"UDP sendto {family!r}")
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
        fail("G10", "tests/netguard.py 缺少 `socket.socket.connect = _orig_connect` 還原賦值")
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
                    fail("G10", f"{rel}:{node.lineno} 把 {name} 清成 None（identity 斷言將無法成立）")


# ---------------------------------------------------------------- G11
def g11_cdp_rlock() -> None:
    rel = "src/browser/cdp_rtt.py"
    src = read(rel)
    if "threading.RLock()" not in src:
        fail("G11", f"{rel} 未使用 threading.RLock()")
    if "threading.Lock()" in src:
        fail("G11", f"{rel} 出現非重入的 threading.Lock()")
    fn = method_of(ast.parse(src, filename=rel), "CdpRttTracker", "on_request_will_be_sent")
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
        fail("G14", f"{rel} itertools.count( 出現 {src.count('itertools.count(')} 次（必須恰一次）")
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
        if isinstance(node, ast.AnnAssign) and ast.unparse(node.target) == "DEFAULT_KKTIX_ALLOWED_HOSTS":
            found = True
            if node.value is None or ast.literal_eval(node.value) != ("kktix.com", ".kktix.cc"):
                fail("G15", f"{rel} DEFAULT_KKTIX_ALLOWED_HOSTS 值遭放寬")
    if not found:
        fail("G15", f"{rel} 未宣告 DEFAULT_KKTIX_ALLOWED_HOSTS")
    fn = method_of(tree, "ServerHeaderClockSync", "sample")
    if fn is None:
        fail("G15", f"{rel} 缺少 ServerHeaderClockSync.sample")
    else:
        body = list(fn.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]  # 跳過 docstring
        first = ast.unparse(body[0]) if body else ""
        if first != "self.assert_url_allowed(url)":
            fail("G15", f"{rel} sample() 首句不是 assert_url_allowed（實際: {first!r}）")
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
            fail("G16", f"{rel} WarmupScheduler.__init__ 缺少 clock_synchronizer_factory 參數")
    fields = _dataclass_fields(tree, "TaskSchedule")
    for required in ("clock_sync", "stage_lock", "readiness_deferred"):
        if required not in fields:
            fail("G16", f"{rel} TaskSchedule 缺少 {required} 欄位")


def _dataclass_fields(tree: ast.AST, class_name: str) -> set[str]:
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == class_name:
            return {
                ast.unparse(m.target)
                for m in n.body
                if isinstance(m, ast.AnnAssign)
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
            and any("schedule.stage_lock" in ast.unparse(item.context_expr) for item in n.items)
            for n in ast.walk(fn)
        )
        if not has_lock:
            fail("G17", f"{rel} {name}() 未持有 schedule.stage_lock")


# ---------------------------------------------------------------- G18
def g18_screenshot_hook_threadsafe() -> None:
    rel = "src/browser/manager.py"
    src = read(rel)
    fn = method_of(ast.parse(src, filename=rel), "PlaywrightManager", "make_screenshot_hook")
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
    lits = [n.value for n in ast.walk(cls) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    for marker in ("sale_readiness_catch_up", "sale_readiness_recovered"):
        if lits.count(marker) != 1:
            fail("G19", f"{rel} {marker!r} 發射點有 {lits.count(marker)} 個（必須恰一個 owner）")


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
