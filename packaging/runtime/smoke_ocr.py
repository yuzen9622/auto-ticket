#!/usr/bin/env python3
"""OCR 實機驗收：確認這台機器上的 onnxruntime + ddddocr 真的跑得起來。

`uv pip install` 成功不代表 OCR 可用——onnxruntime 是 native extension，
wheel 裝得進去、`import` 卻炸掉（缺 libomp、ABI 對不上、CPU provider 沒註冊）
是這條鏈最常見的失敗形態，而且只在真的推論一次的時候才會現形。

三個目標平台的 release build 都必須跑過這支腳本，任一台失敗就中止該版發佈。

用法：
    python smoke_ocr.py --image tests/fixtures/captcha_sample.png

預期輸出（exit 0）：
    onnxruntime=1.23.2
    providers=['CPUExecutionProvider']
    ddddocr=ok classification_len>0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _force_utf8_output() -> None:
    """Windows 主控台預設是 cp1252，編不了中文，一行 log 就能讓建置整個倒下。

    `PYTHONUTF8` 得在直譯器啟動前就設好才有用，在 `__main__` 裡設已經太遲，
    所以直接改串流本身。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_output()

EXPECTED_ORT = "1.23.2"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument(
        "--expect-onnxruntime",
        default=EXPECTED_ORT,
        help=f"要求的 onnxruntime 版本（預設 {EXPECTED_ORT}）",
    )
    args = parser.parse_args(argv)

    if not args.image.is_file():
        print(f"驗收圖不存在: {args.image}", file=sys.stderr)
        return 1

    import onnxruntime

    print(f"onnxruntime={onnxruntime.__version__}")
    if onnxruntime.__version__ != args.expect_onnxruntime:
        print(
            f"onnxruntime 版本不符：預期 {args.expect_onnxruntime}，"
            f"實際 {onnxruntime.__version__}",
            file=sys.stderr,
        )
        return 1

    providers = onnxruntime.get_available_providers()
    print(f"providers={providers}")
    if "CPUExecutionProvider" not in providers:
        print("缺少 CPUExecutionProvider，推論無法在這台機器上執行", file=sys.stderr)
        return 1

    import ddddocr

    ocr = ddddocr.DdddOcr(show_ad=False)
    result = ocr.classification(args.image.read_bytes())
    if not result:
        print("ddddocr 對驗收圖回傳空字串，模型未實際推論", file=sys.stderr)
        return 1

    print("ddddocr=ok classification_len>0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
