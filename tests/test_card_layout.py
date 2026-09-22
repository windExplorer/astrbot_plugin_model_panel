"""卡片版面自检：表头与数值列必须在**同一条竖线**上（像素级）。

用法：
    python tests/test_card_layout.py

为什么值得单独测：v1.3.10 的卡片丑了一个版本 —— 数值列用了又锚点又手动减宽度的写法，
右移量被减了两遍，于是「延迟 / 成功率」两个表头在 x=847，而下面的数值在 x=778，
整整偏了一个字宽（69px）。这类错**编译通过、运行不报错、功能全对**，
只是图难看；而图是发到聊天里的，人眼一看就知道不对，机器不量就永远发现不了。

依赖：Pillow + 一个中文字体。两者缺一就打印「跳过」并返回 0（不是失败）：
本仓库的其它自检都能在裸环境里跑，不该因为它装不上而集体变红。
"""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  MISS ") + label)
    if not cond:
        _failures.append(label)


def load_card_render():
    """把 card_render 抠出来单独加载（main.py 依赖整个 astrbot 运行时，本机 import 不了）。"""
    import importlib.util

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = types.SimpleNamespace(
        warning=lambda *a, **k: None, debug=lambda *a, **k: None, info=lambda *a, **k: None
    )
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    spec = importlib.util.spec_from_file_location("card_render", ROOT / "card_render.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["card_render"] = mod
    spec.loader.exec_module(mod)
    return mod


def ink_bands(img, threshold: int = 600):
    """逐行扫深色墨迹，返回 [(y0, y1, left, right)]。

    阈值 600 把所有背景都挡在门外：渐变头（约 727）、行底（约 747）、描边（约 682）
    都亮于它，只有文字（正文/表头/序号）会留下墨迹。
    """
    px = img.load()
    w, h = img.size
    bands, cur = [], None
    for y in range(h):
        xs = [x for x in range(w) if sum(px[x, y][:3]) < threshold]
        if xs:
            if cur is None:
                cur = [y, y, min(xs), max(xs)]
            else:
                cur[1] = y
                cur[2] = min(cur[2], min(xs))
                cur[3] = max(cur[3], max(xs))
        elif cur is not None:
            bands.append(tuple(cur))
            cur = None
    if cur is not None:
        bands.append(tuple(cur))
    return bands


def main() -> int:
    try:
        from PIL import Image
    except Exception as e:  # pragma: no cover - 没装 Pillow 的环境
        print(f"  skip  未安装 Pillow（{e}），跳过版面自检")
        return 0

    cr = load_card_render()
    if not cr.resolve_font(""):
        print("  skip  没找到可用中文字体，卡片本来就会退化成纯文本，跳过版面自检")
        return 0

    rows = [
        {"kind": "group", "index": 1, "label": "OpenAI", "note": "3 个模型"},
        {"index": 2, "label": "gpt-4o", "state": "healthy", "cells": ["812ms", "99.2%"]},
        {"index": 3, "label": "gpt-4o-mini", "state": "degraded", "cells": ["1分45秒", "88.0%"]},
        {"index": 4, "label": "o3", "state": "down", "cells": ["-", "0.0%"]},
    ]
    for theme in ("rose", "graphite"):
        # 刻意不给标题 / 角标 / 汇总点 / 大字：头部一个墨迹都不留，
        # 这样「第一条右侧墨迹带」必定是表头带 —— 否则标题和右上角小标签会混进来，
        # 它们本来就在别的位置上，一比就把这条断言比成假警报。
        png = cr.render_card("", "", [], ["延迟", "成功率"], rows, [], "",
                             numbered=True, theme=theme)
        check(bool(png), f"[{theme}] 卡片渲染得出来")
        if not png:
            continue
        img = Image.open(io.BytesIO(png)).convert("RGB")
        # 只看右半边的墨迹带：分组行没有数值列（它的墨迹在左边），会被这一步滤掉
        bands = [b for b in ink_bands(img) if b[3] > img.size[0] * 0.6]
        check(len(bands) == 4, f"[{theme}] 表头 + 三个数值行（实得 {len(bands)} 段）")
        if len(bands) != 4:
            continue
        header, data = bands[0], bands[1:]
        spread = max(b[3] for b in bands) - min(b[3] for b in bands)
        check(spread <= 2,
              f"[{theme}] 表头与数值列右边缘对齐（最大偏差 {spread}px，表头 {header[3]}，"
              f"各数值行 {[b[3] for b in data]}）")
        # 数值列不能顶到卡片外沿：右侧至少留出 PAD（否则像被裁掉了）
        check(header[3] <= img.size[0] - cr.SHADOW_PAD - cr.PAD,
              f"[{theme}] 数值列没有顶到卡片边缘（{header[3]} <= "
              f"{img.size[0] - cr.SHADOW_PAD - cr.PAD}）")

    print()
    if _failures:
        print(f"失败 {len(_failures)} 项：")
        for f in _failures:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
