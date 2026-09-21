"""卡片渲染：把模型状态/告警画成一张深色监控面板图，以图片形式发给用户。

设计取向（和「萌萌资料卡」的区别，刻意不照抄）：

- 资料卡是浅色渐变 + 圆角 + 爱心星星的「人设卡」；这里是深色底 + 左侧状态色条 +
  右对齐数值列 + 迷你延迟条的「监控面板」，信息密度优先，符合模型监测这件事的语义。
- 萌系只保留在配色上（粉→紫的强调色渐变），不再往内容里塞装饰。

技术前提与约束：

- **不引入新依赖**：AstrBot 核心自身已依赖 ``pillow>=11.2.1``，所以这里可以直接用。
- **不用 emoji、不用 fontTools**：那两者不是核心依赖（只有资料卡这类插件自己声明）。
  卡片里的状态一律用绘制的色条/图形表达，不依赖 emoji 字形。
- 中文字体只从系统里找（候选见 ``_font_candidates``），**不往插件包里塞几十 MB 字体**；
  找不到可用字体时返回 ``None``，由调用方降级发纯文本 —— 宁可丑，不可空白图。
- 渲染是 CPU 密集的，**必须用 ``asyncio.to_thread`` 包起来调用**，别在事件循环里直接跑。
"""

from __future__ import annotations

import io
import os
import sys
from typing import Any, Optional

from astrbot.api import logger

# ---------------- 画布与配色 ----------------
CARD_W = 880
SHADOW_PAD = 26
PAD_X = 34

BG = (21, 23, 38)
BG_ROW = (27, 30, 48)
BG_ROW_ALT = (24, 27, 43)
FG = (232, 235, 247)
FG_DIM = (154, 163, 192)
FG_FAINT = (108, 118, 146)
LINE = (46, 51, 76)
ACCENT_A = (236, 72, 153)  # 粉
ACCENT_B = (139, 92, 246)  # 紫

# 状态色条：这是面板最主要的信息通道，所以颜色语义要一眼可读
STATE_COLORS = {
    "healthy": (74, 222, 128),
    "degraded": (251, 191, 36),
    "down": (248, 113, 113),
    "unknown": (148, 163, 184),
    "muted": (100, 116, 139),
    "skipped": (100, 116, 139),
}

HEADER_H = 92
STATS_H = 74
COLHEAD_H = 30
ROW_H = 60
FOOT_H = 24

# 字号 → 已加载字体，按字号缓存避免每次重开字体文件
_font_cache: dict[int, Any] = {}
_font_path: Optional[str] = ""  # 空串=未探测；None=探测过且不可用


def _font_candidates(configured: str = "") -> list[str]:
    """中文字体候选。configured 允许用户在插件配置里指定路径，优先级最高。"""
    out: list[str] = []
    if configured:
        out.append(configured)
    if sys.platform.startswith("win"):
        root = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        out += [
            os.path.join(root, n)
            for n in (
                "msyh.ttc", "msyhbd.ttc", "Noto Sans SC (TrueType).otf",
                "simhei.ttf", "Deng.ttf", "SourceHanSansCN-Regular.otf",
            )
        ]
    elif sys.platform == "darwin":
        out += [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        ]
    else:
        out += [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
        ]
    return out


def resolve_font(configured: str = "", force: bool = False) -> Optional[str]:
    """探测一个可用的中文字体路径，结果缓存。找不到返回 None。"""
    global _font_path, _font_cache
    if _font_path != "" and not force:
        return _font_path
    _font_cache.clear()
    picked = None
    for path in _font_candidates(configured):
        if not os.path.isfile(path):
            continue
        try:
            from PIL import ImageFont

            ImageFont.truetype(path, 20)
        except Exception:
            continue
        picked = path
        break
    _font_path = picked
    if picked is None:
        logger.warning("[ModelPanel] 未找到可用中文字体，卡片将降级为纯文本")
    return picked


def _font(size: int):
    from PIL import ImageFont

    f = _font_cache.get(size)
    if f is None:
        path = _font_path or ""
        f = ImageFont.truetype(path, size)
        _font_cache[size] = f
    return f


def _mix(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _text_w(draw, text: str, font) -> float:
    try:
        return draw.textlength(text, font=font)
    except Exception:
        return len(text) * 12.0


def _fit(draw, text: str, font, max_w: float) -> str:
    """按像素宽度截断，超出补省略号。中文不能按字符数截。"""
    text = str(text if text is not None else "")
    if not text or _text_w(draw, text, font) <= max_w:
        return text
    ell = "…"
    while text and _text_w(draw, text + ell, font) > max_w:
        text = text[:-1]
    return (text + ell) if text else ell


def _signal_mark(draw, x: int, y: int, color: tuple) -> None:
    """自绘的三格信号标，替代 emoji 当标题图标。"""
    for i, h in enumerate((10, 17, 24)):
        bx = x + i * 9
        draw.rounded_rectangle([bx, y + (24 - h), bx + 6, y + 24], radius=3, fill=_mix(color, FG, i * 0.16))


def _state_mark(draw, x: int, y: int, h: int, color: tuple) -> None:
    """左侧状态色条 —— 面板的主要视觉线索。"""
    draw.rounded_rectangle([x, y + 9, x + 6, y + h - 9], radius=3, fill=color)


def _column_anchors(draw, rows: list[dict], columns: list[str]) -> list[tuple[int, int]]:
    """算出每个数值列的右对齐锚点 (left, right)。

    列宽取「表头与各单元格文本像素宽的最大值」，这样同一列在不同卡片里都能对齐，
    数字也不会因为中英文宽度差异错位。
    """
    widths = [int(_text_w(draw, c, _font(19))) for c in columns]
    for r in rows:
        cells = list(r.get("cells") or [])
        for i in range(len(columns)):
            txt = str(cells[i]) if i < len(cells) else ""
            widths[i] = max(widths[i], int(_text_w(draw, txt, _font(21))))
    out, right = [], CARD_W - PAD_X - SHADOW_PAD
    for w in reversed(widths):
        out.append((right - w, right))
        right -= w + 22
    return list(reversed(out))


def render_card(
    title: str,
    badge: str,
    stats: list[dict],
    columns: list[str],
    rows: list[dict],
    footnotes: list[str],
    font_path: str = "",
) -> Optional[bytes]:
    """画一张监控面板卡片，返回 PNG bytes；字体不可用时返回 None 表示「请降级成文本」。

    Args:
        title: 顶部标题。
        badge: 标题右侧的小标签文本，例如「实时」「告警」。
        stats: ``[{"label": str, "value": str, "state": str}]``，顶部汇总胶囊。
        columns: 右对齐数值列表头，例如 ``["首字", "整轮", "成功率", "样本"]``。
        rows: ``[{"state", "name", "sub", "cells"}]``。``cells`` 与 ``columns`` 一一对应，
            缺的补 ``-``；``sub`` 可为空。状态色是唯一的行内视觉通道，
            不再并行画延迟条——行高放不下，且「相对本卡最大值」的条长容易被误读。
        footnotes: 底部口径说明。**必须写明延迟是探测还是真实对话**，
            两种延迟不可混读（见 docs/模型监测与配置规划.md 第五节）。
    """
    if not resolve_font(font_path):
        return None

    from PIL import Image, ImageDraw, ImageFilter

    rows = list(rows or [])
    stats = list(stats or [])
    footnotes = [str(x) for x in (footnotes or [])]
    cols = list(columns or [])

    # ---------- 先量后画 ----------
    body_h = len(rows) * ROW_H
    head_h = HEADER_H + (STATS_H if stats else 0) + (COLHEAD_H if columns and rows else 0)
    foot_h = (FOOT_H * len(footnotes) + 14) if footnotes else 0
    card_h = head_h + body_h + foot_h + 10
    w, h = CARD_W, card_h + SHADOW_PAD * 2

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # 投影：单独一层高斯模糊，比直接画灰边自然
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [SHADOW_PAD, SHADOW_PAD + 12, w - SHADOW_PAD, h - SHADOW_PAD + 6], radius=30, fill=(0, 0, 0, 120)
    )
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(14)))
    draw = ImageDraw.Draw(img)

    x0, y0 = SHADOW_PAD, SHADOW_PAD
    x1, y1 = w - SHADOW_PAD, h - SHADOW_PAD
    draw.rounded_rectangle([x0, y0, x1, y1], radius=28, fill=BG)
    # 顶部一条粉→紫渐变带，是本面板唯一的装饰性渐变
    band_l, band_r = x0 + 28, x1 - 28
    for i in range(max(0, band_r - band_l)):
        t = i / max(1, band_r - band_l - 1)
        draw.line([(band_l + i, y0 + 2), (band_l + i, y0 + 6)], fill=_mix(ACCENT_A, ACCENT_B, t))

    # ---------- 标题 ----------
    _signal_mark(draw, x0 + PAD_X, y0 + 26, ACCENT_B)
    draw.text((x0 + PAD_X + 34, y0 + 22), _fit(draw, title, _font(30), 430), font=_font(30), fill=FG)
    if badge:
        # 徽章也要截断，否则长徽章会顶到标题
        badge = _fit(draw, badge, _font(17), 220)
        bw = int(_text_w(draw, badge, _font(17))) + 22
        bx = x1 - PAD_X - bw
        draw.rounded_rectangle([bx, y0 + 28, bx + bw, y0 + 54], radius=13, fill=_mix(ACCENT_A, ACCENT_B, 0.5))
        draw.text((bx + 11, y0 + 31), badge, font=_font(17), fill=(255, 255, 255))

    cursor = y0 + HEADER_H

    # ---------- 汇总胶囊 ----------
    if stats:
        cx = x0 + PAD_X
        for s in stats:
            color = STATE_COLORS.get(str(s.get("state") or "unknown"), STATE_COLORS["unknown"])
            label = str(s.get("label") or "")
            value = str(s.get("value") or "")
            lw = int(_text_w(draw, label, _font(17)))
            vw = int(_text_w(draw, value, _font(24)))
            box_w = max(96, lw + vw + 42)
            draw.rounded_rectangle([cx, cursor + 8, cx + box_w, cursor + STATS_H - 6], radius=14, fill=BG_ROW)
            draw.ellipse([cx + 14, cursor + 30, cx + 22, cursor + 38], fill=color)
            draw.text((cx + 30, cursor + 28), label, font=_font(17), fill=FG_DIM)
            draw.text((cx + 32 + lw + 8, cursor + 21), value, font=_font(24), fill=color)
            cx += box_w + 12
        cursor += STATS_H

    # ---------- 表头 ----------
    anchors = _column_anchors(draw, rows, cols) if cols and rows else []
    if anchors:
        for i, c in enumerate(cols):
            _, right = anchors[i]
            draw.text((right - _text_w(draw, c, _font(19)), cursor + 7), c, font=_font(19), fill=FG_FAINT)
        draw.line([(x0 + PAD_X, cursor + COLHEAD_H - 2), (x1 - PAD_X, cursor + COLHEAD_H - 2)], fill=LINE)
        cursor += COLHEAD_H

    # ---------- 行 ----------
    name_font, sub_font, cell_font = _font(22), _font(17), _font(21)
    name_x = x0 + PAD_X + 14
    # 名称列占到第一个数值列之前；没有数值列时占满整行宽度
    name_max = max(120, ((anchors[0][0] - 18) if anchors else (x1 - PAD_X)) - name_x)
    for idx, r in enumerate(rows):
        state = str(r.get("state") or "unknown")
        color = STATE_COLORS.get(state, STATE_COLORS["unknown"])
        ry = cursor + idx * ROW_H
        if idx % 2 == 1:
            draw.rounded_rectangle([x0 + PAD_X - 10, ry, x1 - PAD_X + 10, ry + ROW_H - 6], radius=10, fill=BG_ROW_ALT)
        _state_mark(draw, x0 + PAD_X - 4, ry, ROW_H - 6, color)
        draw.text((name_x, ry + 9), _fit(draw, str(r.get("name") or ""), name_font, name_max), font=name_font, fill=FG)
        sub = str(r.get("sub") or "")
        if sub:
            draw.text((name_x, ry + 34), _fit(draw, sub, sub_font, name_max), font=sub_font, fill=FG_FAINT)
        vals = list(r.get("cells") or [])
        for i, (_, right) in enumerate(anchors):
            txt = str(vals[i]) if i < len(vals) else "-"
            draw.text((right - _text_w(draw, txt, cell_font), ry + 16), txt, font=cell_font, fill=FG if i == 0 else FG_DIM)
        if idx < len(rows) - 1:
            draw.line([(x0 + PAD_X, ry + ROW_H - 3), (x1 - PAD_X, ry + ROW_H - 3)], fill=LINE)

    cursor += body_h

    # ---------- 脚注（口径说明就落在这里） ----------
    if footnotes:
        cursor += 12
        for line in footnotes:
            draw.text((x0 + PAD_X, cursor), _fit(draw, str(line), _font(17), CARD_W - 2 * PAD_X), font=_font(17), fill=FG_FAINT)
            cursor += FOOT_H

    # ---------- 圆角裁切 ----------
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([x0, y0, x1, y1], radius=28, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
