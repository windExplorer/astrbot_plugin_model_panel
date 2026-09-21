"""卡片渲染：把模型状态/告警画成一张浅色粉彩面板图，以图片形式发给用户。

设计取向（借鉴「萌萌资料卡」的观感，不照抄它的代码与内容）：

- 资料卡的可用之处是**浅色底 + 粉彩渐变头 + 行内圆角药丸 + 带色柔光投影**，
  这套在 QQ 里比深色面板耐看，深色截图在聊天气泡里发灰、对比也被吃掉。
  所以这里也走浅色，但配色是「粉→紫」双调（资料卡是纯粉），
  装饰只留一个自绘的信号条标记，不放爱心星星——它是监控面板，不是人设卡。
- 内容仍然按监控面板组织：左侧状态色条 + 右对齐数值列 + 顶部汇总胶囊。
- 行数多时自动折成两栏。实时状态要列**全部**模型，单栏会长到两三千像素，
  在手机上根本没法扫读；两栏是这类总览卡的常规排法。

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
SHADOW_PAD = 30
PAD_X = 30

CARD_BG = (255, 255, 255)
ROW_BG = (248, 245, 252)
ROW_BG_ALT = (252, 249, 255)
HEADER_TOP = (255, 227, 242)   # 粉
HEADER_BOTTOM = (219, 200, 255)  # 紫 —— 资料卡是纯粉，这里做成双调以区分
FOOTER_BG = (252, 241, 248)
BORDER = (242, 218, 233)
SHADOW = (196, 110, 150)

FG = (70, 60, 84)
FG_DIM = (126, 116, 142)
FG_FAINT = (163, 154, 178)
LABEL = (178, 110, 148)
LINE = (238, 230, 244)
ACCENT_A = (232, 82, 148)  # 粉
ACCENT_B = (134, 96, 224)  # 紫
ON_HEADER = (92, 56, 88)   # 渐变头之上的深色文字

# 状态色：浅色底上要压得住，所以全部走深一档
STATE_COLORS = {
    "healthy": (34, 158, 108),
    "degraded": (208, 132, 16),
    "down": (214, 66, 88),
    "unknown": (140, 132, 158),
    "muted": (150, 145, 165),
    "skipped": (150, 145, 165),
}

HEADER_H = 88
STATS_H = 66
COLHEAD_H = 26
ROW_H = 44          # 单行行高：列全部模型时靠它压住总高
ROW_H_SUB = 66      # 带副标题的行
ROW_GAP = 8
FOOT_H = 22
TWO_COL_MIN = 15    # 行数超过这个值就折两栏

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
        draw.rounded_rectangle([bx, y + (24 - h), bx + 6, y + 24], radius=3, fill=_mix(color, (255, 255, 255), i * 0.16))


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


def _column_anchors(draw, rows: list[dict], columns: list[str], left: int, right: int) -> list[tuple[int, int]]:
    """在 [left, right] 区间内算出每个数值列的右对齐锚点 (l, r)。

    列宽取「表头与各单元格文本像素宽的最大值」，这样同一列在不同卡片里都能对齐，
    数字也不会因为中英文宽度差异错位。分栏时每栏各自算，窄栏不会被宽栏撑出来。
    """
    body_size = 20 if len(columns) > 2 else 19
    widths = [int(_text_w(draw, c, _font(16))) for c in columns]
    for r in rows:
        cells = list(r.get("cells") or [])
        for i in range(len(columns)):
            txt = str(cells[i]) if i < len(cells) else ""
            widths[i] = max(widths[i], int(_text_w(draw, txt, _font(body_size))))
    gap = 16 if len(columns) > 3 else 22
    out, x = [], right
    for w in reversed(widths):
        out.append((x - w, x))
        x -= w + gap
    return list(reversed(out))


def _paint_rows(draw, rows: list[dict], cols: list[str], lx: int, rx: int, top: int) -> int:
    """在 [lx, rx] 区间内画一组行，返回底边 y。"""
    if not rows:
        return top
    anchors = _column_anchors(draw, rows, cols, lx, rx) if cols else []
    name_x = lx + 16
    name_max = max(110, ((anchors[0][0] - 16) if anchors else rx) - name_x)
    name_font, sub_font, cell_font = _font(20), _font(15), _font(20 if len(cols) > 2 else 19)
    head_font = _font(15)
    y = top
    if anchors:
        for i, c in enumerate(cols):
            _, right = anchors[i]
            draw.text((right - _text_w(draw, c, head_font), y + 4), c, font=head_font, fill=FG_FAINT)
        y += COLHEAD_H
    for idx, r in enumerate(rows):
        state = str(r.get("state") or "unknown")
        color = STATE_COLORS.get(state, STATE_COLORS["unknown"])
        sub = str(r.get("sub") or "")
        h = _row_height(r)
        draw.rounded_rectangle([lx, y, rx, y + h - ROW_GAP], radius=12,
                               fill=ROW_BG if idx % 2 == 0 else ROW_BG_ALT)
        bar_y0 = y + 8
        bar_y1 = y + h - ROW_GAP - 8
        draw.rounded_rectangle([lx + 4, bar_y0, lx + 9, bar_y1], radius=2, fill=color)
        ty = y + (11 if sub else 9)
        draw.text((name_x, ty), _fit(draw, str(r.get("name") or ""), name_font, name_max), font=name_font, fill=FG)
        if sub:
            draw.text((name_x, ty + 25), _fit(draw, sub, sub_font, name_max), font=sub_font, fill=FG_FAINT)
        vals = list(r.get("cells") or [])
        for i, (_, right) in enumerate(anchors):
            txt = str(vals[i]) if i < len(vals) else "-"
            fill = FG if i == 0 else FG_DIM
            draw.text((right - _text_w(draw, txt, cell_font), ty + 1), txt, font=cell_font, fill=fill)
        y += h
    return y


def render_card(
    title: str,
    badge: str,
    stats: list[dict],
    columns: list[str],
    rows: list[dict],
    footnotes: list[str],
    font_path: str = "",
) -> Optional[bytes]:
    """画一张浅色粉彩面板卡片，返回 PNG bytes；字体不可用时返回 None 表示「请降级成文本」。

    Args:
        title: 顶部渐变标题条上的标题。
        badge: 标题右侧的小标签文本，例如「实时」「告警」。
        stats: ``[{"label": str, "value": str, "state": str}]``，顶部汇总胶囊。
        columns: 右对齐数值列表头，例如 ``["延迟", "成功率"]``。
        rows: ``[{"state", "name", "sub", "cells"}]``。``sub`` 为空即单行矮行；
            只有真正要解释的话才填 sub，否则「列全部模型」会把卡片拉得过高。
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
    two_col = len(rows) > TWO_COL_MIN
    if two_col:
        half = (len(rows) + 1) // 2
        blocks = [rows[:half], rows[half:]]
    else:
        blocks = [rows]

    # 表头行随栏内是否有行而定，两栏时共用同一段高度
    head_extra = COLHEAD_H if (cols and rows) else 0
    body_h = max(sum(_row_height(r) for r in b) + head_extra for b in blocks) if blocks else 0
    header_h = HEADER_H if rows or stats else HEADER_H - 26
    # 脚注先按最终宽度折好行，再据此定高：口径说明被截断就等于没写
    scratch = Image.new("RGBA", (8, 8))
    mdraw = ImageDraw.Draw(scratch)
    note_w = CARD_W - 2 * SHADOW_PAD - 2 * PAD_X
    foot_lines = [ln for note in footnotes for ln in _wrap_lines(mdraw, note, _font(15), note_w)]
    foot_h = (FOOT_H * len(foot_lines) + 12) if foot_lines else 0
    stats_h = STATS_H if stats else 0
    card_h = header_h + stats_h + body_h + foot_h + 26
    w, h = CARD_W, card_h + SHADOW_PAD * 2

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # 投影：单独一层高斯模糊，且带粉调 —— 纯灰投影在浅色卡上会发脏
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [SHADOW_PAD, SHADOW_PAD + 12, w - SHADOW_PAD, h - SHADOW_PAD + 6],
        radius=30, fill=(*SHADOW, 95),
    )
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(16)))
    x0, y0 = SHADOW_PAD, SHADOW_PAD
    x1, y1 = w - SHADOW_PAD, h - SHADOW_PAD
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=26, fill=CARD_BG)

    # ---------- 渐变标题条（上圆角，下方直接接内容） ----------
    header = Image.new("RGBA", (x1 - x0, header_h), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(header)
    for i in range(header_h):
        hdraw.line([(0, i), (header.width, i)], fill=(*_mix(HEADER_TOP, HEADER_BOTTOM, i / max(1, header_h - 1)), 255))
    hmask = Image.new("L", header.size, 0)
    ImageDraw.Draw(hmask).rounded_rectangle([0, 0, header.width - 1, header.height - 1], radius=26, fill=255)
    ImageDraw.Draw(hmask).rectangle([0, 26, header.width, header.height], fill=255)
    img.paste(header, (x0, y0), hmask)
    draw = ImageDraw.Draw(img)

    _signal_mark(draw, x0 + PAD_X, y0 + 24, ACCENT_B)
    draw.text((x0 + PAD_X + 34, y0 + 20), _fit(draw, title, _font(28), 420), font=_font(28), fill=ON_HEADER)
    if badge:
        # 徽章也要截断，否则长徽章会顶到标题
        badge = _fit(draw, badge, _font(16), 200)
        bw = int(_text_w(draw, badge, _font(16))) + 24
        bx = x1 - PAD_X - bw
        draw.rounded_rectangle([bx, y0 + 26, bx + bw, y0 + 50], radius=12, fill=(255, 255, 255, 235))
        draw.text((bx + 12, y0 + 29), badge, font=_font(16), fill=_mix(ACCENT_A, ACCENT_B, 0.5))

    cursor = y0 + header_h

    # ---------- 汇总胶囊 ----------
    if stats:
        cx = x0 + PAD_X
        for s in stats:
            color = STATE_COLORS.get(str(s.get("state") or "unknown"), STATE_COLORS["unknown"])
            label = str(s.get("label") or "")
            value = str(s.get("value") or "")
            lw = int(_text_w(draw, label, _font(16)))
            vw = int(_text_w(draw, value, _font(23)))
            box_w = max(92, lw + vw + 42)
            draw.rounded_rectangle([cx, cursor + 8, cx + box_w, cursor + STATS_H - 8], radius=14, fill=ROW_BG)
            draw.rounded_rectangle([cx, cursor + 8, cx + 4, cursor + STATS_H - 8], radius=2, fill=color)
            draw.text((cx + 16, cursor + 26), label, font=_font(16), fill=LABEL)
            draw.text((cx + 20 + lw, cursor + 18), value, font=_font(23), fill=color)
            cx += box_w + 10
        cursor += STATS_H
    cursor += 6

    # ---------- 行（单栏或两栏） ----------
    inner_l, inner_r = x0 + PAD_X, x1 - PAD_X
    if two_col:
        gutter = 14
        col_w = (inner_r - inner_l - gutter) // 2
        bottoms = []
        for i, block in enumerate(blocks):
            lx = inner_l + i * (col_w + gutter)
            bottoms.append(_paint_rows(draw, block, cols, lx, lx + col_w, cursor))
        cursor = max(bottoms)
    else:
        cursor = _paint_rows(draw, rows, cols, inner_l, inner_r, cursor)

    # ---------- 脚注（口径说明就落在这里） ----------
    if foot_lines:
        cursor += 10
        for line in foot_lines:
            draw.text((inner_l, cursor), line, font=_font(15), fill=FG_FAINT)
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
