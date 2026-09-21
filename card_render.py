"""卡片渲染：把模型状态/告警画成一张浅色卡片图，以图片形式发给用户。

设计取向（借鉴「萌萌资料卡」的观感，不照抄它的代码与内容）：

- 走浅色：白卡体 + 粉→紫强调色 + 带色柔光投影 + 品牌描边。深色截图塞进 QQ 气泡
  会发灰、对比被吃掉。资料卡是纯粉，这里做成粉→紫双调以示区分。
- **一行一条，不折栏**。曾经按行数自动折两栏，结果窄栏里模型名被截得看不出差别，
  而且两栏读起来要来回扫，比长列表更累。长列表靠行高和留白控制节奏就够了。
- 数据行**不用底色药丸**。药丸适合「标签：值」的资料条目，不适合右对齐的数字表——
  一行一个药丸叠上去就是一堵斑马墙，看着乱。这里改成发丝分隔线 + 状态色点。

技术前提与约束：

- **不引入新依赖**：AstrBot 核心自身已依赖 ``pillow>=11.2.1``，所以这里可以直接用。
- **不用 emoji、不用 fontTools**：那两者不是核心依赖（只有资料卡这类插件自己声明）。
  卡片里的状态一律用自绘的色点表达，不依赖 emoji 字形。
- **往 RGBA 图上画带 alpha 的颜色是替换像素、不是叠加**：在白卡上画 ``(*color, 46)``
  会得到一个近乎透明的窟窿。要淡色就先用 ``_mix`` 和白底混成不透明色（见 ``_state_dot``）。
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
CARD_W = 760
SHADOW_PAD = 28
PAD_X = 34

CARD_BG = (255, 255, 255)
BADGE_BG = (250, 236, 245)
BORDER = (243, 221, 235)
SHADOW = (196, 110, 150)

FG = (64, 56, 78)
FG_DIM = (122, 112, 138)
FG_FAINT = (163, 155, 176)
LABEL = (172, 106, 142)
LINE = (240, 234, 245)
ACCENT_A = (232, 82, 148)  # 粉
ACCENT_B = (134, 96, 224)  # 紫 —— 资料卡是纯粉，这里做成双调以区分

# 状态色：浅色底上要压得住，所以全部走深一档
STATE_COLORS = {
    "healthy": (30, 150, 102),
    "degraded": (206, 128, 12),
    "down": (212, 60, 84),
    "unknown": (140, 132, 158),
    "muted": (150, 145, 165),
    "skipped": (150, 145, 165),
}

HEAD_H = 62        # 标题行，下方一条渐变细线收口
STATS_H = 44       # 汇总点行
COLHEAD_H = 32
ROW_H = 48         # 单行行高
ROW_H_SUB = 72     # 带副标题的行
FOOT_H = 23
DOT_R = 5          # 状态点直径
COL_GAP = 28       # 数值列间距
NAME_MIN = 260     # 名称列至少留这么宽，否则模型名会被数字列挤成「deepseek-v3 …」
CARD_W_MAX = 1080  # 自适应上限：再宽发出去就不像一张卡了

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


def _signal_mark(draw, x: int, y: int, size: int) -> None:
    """自绘的三格信号标，替代 emoji 当标题图标。"""
    for i, frac in enumerate((0.42, 0.68, 1.0)):
        h = int(size * frac)
        bx = x + int(i * size * 0.30)
        draw.rounded_rectangle(
            [bx, y + (size - h), bx + size * 0.20, y + size],
            radius=size * 0.10,
            fill=_mix(ACCENT_A, ACCENT_B, i / 2),
        )


def _gradient_rule(draw, x0: int, x1: int, y: int, height: int = 3) -> None:
    """一条粉→紫细线：整张卡唯一的装饰性渐变，用来代替大色块标题底。"""
    span = max(1, x1 - x0)
    for i in range(span):
        draw.line([(x0 + i, y), (x0 + i, y + height)], fill=_mix(ACCENT_A, ACCENT_B, i / span))


def _state_dot(draw, cx: int, cy: int, color: tuple) -> None:
    """状态点外圈一层淡色晕：单个小点在浅底上偏弱，晕一圈才有「这行有问题」的注意力。

    不能直接用带 alpha 的颜色 —— ImageDraw 在 RGBA 图上是替换像素而不是混合，
    白底上会挖出个半透明窟窿。所以先把状态色和白底混成不透明淡色。
    """
    halo = DOT_R + 4
    draw.ellipse([cx - halo, cy - halo, cx + halo, cy + halo], fill=_mix(color, CARD_BG, 0.72))
    draw.ellipse([cx - DOT_R, cy - DOT_R, cx + DOT_R, cy + DOT_R], fill=color)


def _wrap_lines(draw, text: str, font, max_w: float) -> list[str]:
    """按像素宽度折行。脚注是口径说明，截断就等于把纪律弄丢了，只能折。"""
    text = str(text if text is not None else "")
    if not text or _text_w(draw, text, font) <= max_w:
        return [text]
    lines: list[str] = []
    cur = ""
    for ch in text:
        if cur and _text_w(draw, cur + ch, font) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _row_height(row: dict) -> int:
    return ROW_H_SUB if str(row.get("sub") or "") else ROW_H


def _column_widths(draw, rows: list[dict], columns: list[str]) -> list[int]:
    """每个数值列需要的像素宽：表头与各单元格文本宽的最大值。

    这样同一列在不同卡片里都能对齐，数字也不会因为中英文宽度差异错位。
    """
    widths = [int(_text_w(draw, c, _font(15))) for c in columns]
    for r in rows:
        cells = list(r.get("cells") or [])
        for i in range(len(columns)):
            txt = str(cells[i]) if i < len(cells) else ""
            widths[i] = max(widths[i], int(_text_w(draw, txt, _font(19))))
    return widths


def _anchors_from_widths(widths: list[int], right: int) -> list[tuple[int, int]]:
    """从最右侧往回铺，得到每列的 (l, r) 右对齐锚点。"""
    out, x = [], right
    for w in reversed(widths):
        out.append((x - w, x))
        x -= w + COL_GAP
    return out[::-1]


def render_card(
    title: str,
    badge: str,
    stats: list[dict],
    columns: list[str],
    rows: list[dict],
    footnotes: list[str],
    font_path: str = "",
) -> Optional[bytes]:
    """画一张浅色卡片，返回 PNG bytes；字体不可用时返回 None 表示「请降级成文本」。

    Args:
        title: 顶部标题。
        badge: 标题右侧的小标签文本，例如「实时」「告警」。
        stats: ``[{"label": str, "value": str, "state": str}]``，标题下的汇总点。
        columns: 右对齐数值列表头，例如 ``["延迟", "成功率"]``。
        rows: ``[{"state", "name", "sub", "cells"}]``。``sub`` 为空即单行矮行；
            只有真正要解释的话才填 sub，否则长列表会被拉得更高。
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
    scratch = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    col_widths = _column_widths(scratch, rows, cols) if cols else []
    numbers_w = (sum(col_widths) + COL_GAP * (len(col_widths) - 1)) if col_widths else 0
    # 宽度跟着内容走：明细卡有五个数值列，固定 760 会把模型名挤到只剩省略号
    card_w = min(CARD_W_MAX, max(CARD_W, 2 * SHADOW_PAD + 2 * PAD_X + NAME_MIN + numbers_w + 24))
    inner_l, inner_r = SHADOW_PAD + PAD_X, card_w - SHADOW_PAD - PAD_X
    body_h = sum(_row_height(r) for r in rows) + (COLHEAD_H if cols and rows else 0)
    foot_lines = [ln for note in footnotes for ln in _wrap_lines(scratch, note, _font(14), inner_r - inner_l)]
    card_h = (HEAD_H + (STATS_H if stats else 0) + body_h
              + (FOOT_H * len(foot_lines) + 18 if foot_lines else 12))
    w, h = card_w, card_h + SHADOW_PAD * 2

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # 投影：单独一层高斯模糊，且带粉调 —— 纯灰投影在浅色卡上会发脏
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [SHADOW_PAD, SHADOW_PAD + 12, w - SHADOW_PAD, h - SHADOW_PAD + 6],
        radius=28, fill=(*SHADOW, 90),
    )
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(16)))
    draw = ImageDraw.Draw(img)

    x0, y0 = SHADOW_PAD, SHADOW_PAD
    x1, y1 = w - SHADOW_PAD, h - SHADOW_PAD
    draw.rounded_rectangle([x0, y0, x1, y1], radius=26, fill=CARD_BG)

    # ---------- 标题 ----------
    title_font = _font(24)
    _signal_mark(draw, inner_l, y0 + 19, 22)
    badge = _fit(draw, str(badge or ""), _font(15), 200)
    badge_w = int(_text_w(draw, badge, _font(15))) + 24 if badge else 0
    draw.text((inner_l + 34, y0 + 17),
              _fit(draw, title, title_font, inner_r - (inner_l + 34) - badge_w - 18),
              font=title_font, fill=FG)
    if badge:
        bx = inner_r - badge_w
        draw.rounded_rectangle([bx, y0 + 19, bx + badge_w, y0 + 43], radius=12, fill=BADGE_BG)
        draw.text((bx + 12, y0 + 23), badge, font=_font(15), fill=_mix(ACCENT_A, ACCENT_B, 0.45))
    _gradient_rule(draw, inner_l, inner_r, y0 + HEAD_H - 10)
    cursor = y0 + HEAD_H

    # ---------- 汇总点 ----------
    if stats:
        cx = inner_l
        for s in stats:
            color = STATE_COLORS.get(str(s.get("state") or "unknown"), STATE_COLORS["unknown"])
            label = str(s.get("label") or "")
            value = str(s.get("value") or "")
            lw = int(_text_w(draw, label, _font(16)))
            vw = int(_text_w(draw, value, _font(20)))
            draw.ellipse([cx, cursor + 17, cx + 9, cursor + 26], fill=color)
            draw.text((cx + 17, cursor + 12), label, font=_font(16), fill=LABEL)
            draw.text((cx + 21 + lw, cursor + 9), value, font=_font(20), fill=color)
            cx += 21 + lw + vw + 30
        cursor += STATS_H
        draw.line([(inner_l, cursor), (inner_r, cursor)], fill=LINE)

    # ---------- 表头 ----------
    anchors = _anchors_from_widths(col_widths, inner_r) if col_widths and rows else []
    if anchors:
        for i, c in enumerate(cols):
            _, right = anchors[i]
            draw.text((right - _text_w(draw, c, _font(15)), cursor + 8), c, font=_font(15), fill=FG_FAINT)
        draw.line([(inner_l, cursor + COLHEAD_H - 1), (inner_r, cursor + COLHEAD_H - 1)], fill=LINE)
        cursor += COLHEAD_H

    # ---------- 行 ----------
    name_font, sub_font, cell_font = _font(19), _font(14), _font(19)
    name_x = inner_l + 28
    name_max = max(120, ((anchors[0][0] - 24) if anchors else inner_r) - name_x)
    for idx, r in enumerate(rows):
        state = str(r.get("state") or "unknown")
        color = STATE_COLORS.get(state, STATE_COLORS["unknown"])
        sub = str(r.get("sub") or "")
        rh = _row_height(r)
        _state_dot(draw, inner_l + 9, cursor + (25 if sub else rh // 2), color)
        draw.text((name_x, cursor + 14),
                  _fit(draw, str(r.get("name") or ""), name_font, name_max), font=name_font, fill=FG)
        if sub:
            draw.text((name_x, cursor + 41), _fit(draw, sub, sub_font, name_max), font=sub_font, fill=FG_FAINT)
        vals = list(r.get("cells") or [])
        for i, (_, right) in enumerate(anchors):
            txt = str(vals[i]) if i < len(vals) else "-"
            draw.text((right - _text_w(draw, txt, cell_font), cursor + 14), txt,
                      font=cell_font, fill=FG if i == 0 else FG_DIM)
        cursor += rh
        if idx < len(rows) - 1:
            draw.line([(inner_l, cursor - 1), (inner_r, cursor - 1)], fill=LINE)

    # ---------- 脚注（口径说明就落在这里） ----------
    if foot_lines:
        cursor += 16
        for line in foot_lines:
            draw.text((inner_l, cursor), line, font=_font(14), fill=FG_FAINT)
            cursor += FOOT_H

    # ---------- 品牌描边 + 圆角裁切 ----------
    draw.rounded_rectangle([x0, y0, x1, y1], radius=26, outline=BORDER, width=2)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([x0, y0, x1, y1], radius=26, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
