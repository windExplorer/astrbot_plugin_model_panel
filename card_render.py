"""卡片渲染：把模型状态 / 选单 / 告警画成一张浅色卡片图，以图片形式发给用户。

版面（v1.3.10 重画，与 `astrbot_plugin_user_gateway` 的「切换模型」卡片同款骨架）：
**头 + 身体 + 脚**三段拼接。

    ╭────────────────────────────────────────────────╮  ← 头：主题色**纵向渐变**
    │ 模型状态                                  ╭──╮│     上两角圆角、**下沿是直的**
    │ 12 个模型 · 正常 9 / 故障 2               │实时││     大字是「主角」，右上角是小标签
    │ ● 正常 9   ● 降级 1   ● 故障 2   ● 无数据 │    ││     汇总点用状态色，一眼看出量级
    ├────────────────────────────────────────────────┤  ← 直边对接（不带圆角）
    │                            延迟        成功率  │  ← 列头（右对齐，与小格对齐）
    │ ╭────────────────────────────────────────────╮ │  ← 身体：白底，行是浅灰圆角块
    │ │ ① OpenAI · 3 个模型                        │ │  ← 分组行：描边序号 + 组名 + 计数
    │ │ ╭────────────────────────────────────────╮ │ │
    │ │ │ ② ● gpt-4o     [默认]     812ms   99.2%│ │ │  ← 实心序号 + 状态色点 + 名称 + 数字
    │ │ ╰────────────────────────────────────────╯ │ │
    │ ╰────────────────────────────────────────────╯ │
    ├────────────────────────────────────────────────┤  ← 直边对接
    │ ● 延迟为最近一次调用耗时 · 09-22 12:30  模型控制台│  ← 脚：浅色底，下两角圆角
    ╰────────────────────────────────────────────────╯

为什么改成这个版面（改回去之前先读）：

- **序号必须和文字一样显眼**：这些卡片的主要用法是「看一眼 → 回一个数字」。
  旧版把序号写进名字里（``"3. gpt-4o"``），模型名一长就被截掉，用户根本找不到 3；
  现在序号是独立的方块，绝不会被截断。分组也一样带序号（回分组序号 = 选整组）。
- **序号由调用方算好**，渲染器**不自己编号** —— 发卡片的人和收数字的人是同一份选项表，
  编号必须一处生成。模型行与分组行**共用同一个数字空间**
  （① OpenAI、② gpt-4o、③ gpt-4o-mini、④ 硅基流动…），用户不必先判断这个数字是组还是模型。
- **一行一个块，不折栏**：窄栏里模型名会被截得看不出差别，而且两栏读起来要来回扫。
- **状态色只在两处出现**：行首的状态点、头部的汇总点。一行塞三个彩色胶囊就会显得廉价。
- 分组行用**描边**序号、模型行用**实心**序号：不靠颜色也能区分「这一行是组」。
- 三段之间必须是**直边**：头的下沿、脚的上沿都是直角，只有最外侧那圈的上下四角是圆角。
  整卡一个圆角矩形再往里头贴色块，接缝处会出现「两段圆弧互啃」的缺口。

技术前提与约束：

- **不引入新依赖**：AstrBot 核心自身已依赖 ``pillow>=11.2.1``，所以这里可以直接用。
- **不用 emoji、不用 fontTools**：那两者不是核心依赖（只有资料卡这类插件自己声明）。
  卡片里的状态一律用自绘的色点表达，不依赖 emoji 字形。
- **往 RGBA 图上画带 alpha 的颜色是替换像素、不是叠加**：在白卡上画 ``(*color, 46)``
  会得到一个近乎透明的窟窿。要淡色就先用 ``_mix`` 和白底混成不透明色（见 ``_state_dot``）。
- 中文字体只从系统里找（候选见 ``_font_candidates``），**不往插件包里塞几十 MB 字体**；
  找不到可用字体时返回 ``None``，由调用方降级发纯文本 —— 宁可丑，不可空白图。
- 渲染是 CPU 密集的，**必须用 ``asyncio.to_thread`` 包起来调用**，别在事件循环里直接跑。
- 画不出来**绝不能影响功能**：本模块对外只暴露「返回 PNG bytes 或 None」，
  字体找不到 / 图片太大 / Pillow 缺失都返回 ``None``，由调用方退化成纯文本列表。
"""

from __future__ import annotations

import io
import os
import sys
from typing import Any, Optional

from astrbot.api import logger

try:  # Pillow 随 AstrBot 安装；缺失时整个模块退化为「不渲染」
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except Exception:  # pragma: no cover - 仅在没有 Pillow 的环境
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageFont = None  # type: ignore

# ---------------- 版面 ----------------
CARD_W = 880          # 卡片本体默认宽度（不含四周的阴影留白）
CARD_W_MAX = 1160     # 自适应上限：再宽发出去就不像一张卡了
SHADOW_PAD = 26       # 卡片四周的阴影留白（画布会比卡片大这么多）
PAD = 40              # 卡片内左右边距
RADIUS = 30           # 外轮廓圆角（只有最外侧四角用它）
HEAD_PAD = 30         # 头部内容与上下沿的最小留白
BADGE = 40            # 模型行序号方块边长
GBADGE = 34           # 分组行序号方块边长（略小，描边而非实心）
ROW_H = 76
ROW_GAP = 12
ROW_RADIUS = 20
GROUP_H = 56
COLHEAD_H = 34
FOOTER_MIN_H = 64
COL_GAP = 44          # 数值列间距（太窄时「-」和下一个数会读成一体的「-0.0%」）
NAME_MIN = 230        # 名称列至少留这么宽，否则模型名会被数字列挤成「deepseek-v3 …」
CELL_PAD = 18         # 数值列右侧的边距
FIT_PAD = 28          # 名称与右邻元素的硬留白（太小会让名字贴着数字，像连成一句）

# ---------------- 中性色 ----------------
CARD_BG = (255, 255, 255)
ROW_BG = (247, 248, 252)     # 普通行底
BADGE_BG = (238, 241, 249)   # 非强调行的序号块
TEXT_DARK = (30, 33, 44)     # 模型名：接近纯黑，保证「主角」的分量
TEXT_BODY = (56, 62, 78)
TEXT_MUTED = (126, 133, 152)
TEXT_SOFT = (167, 173, 189)
LABEL_INK = (86, 92, 110)    # 头部汇总点的标签色

# ---------------- 主题 ----------------
# 每套主题给足一组色（头部渐变两端、同色系深色文字、强调色、行底、脚底、描边）。
# 只换「强调色」是不够的 —— 头部必须真的有色，否则整张卡会淡到看不出层次。
# 默认那套沿用本插件既有的粉→紫双调（与插件 logo / WebUI 一致）。
THEMES: dict[str, dict[str, tuple]] = {
    "rose": {
        "top": (255, 229, 243),
        "bottom": (221, 203, 252),
        "ink": (92, 26, 68),
        "sub": (140, 74, 116),
        "accent": (214, 40, 112),
        "accent2": (124, 58, 200),
        "soft": (252, 238, 247),
        "footer": (254, 245, 251),
        "border": (240, 210, 232),
    },
    "indigo": {
        "top": (232, 234, 255),
        "bottom": (160, 170, 248),
        "ink": (42, 38, 94),
        "sub": (78, 74, 138),
        "accent": (79, 70, 229),
        "accent2": (79, 70, 229),
        "soft": (238, 240, 255),
        "footer": (243, 244, 255),
        "border": (206, 210, 243),
    },
    "teal": {
        "top": (222, 246, 240),
        "bottom": (142, 214, 199),
        "ink": (18, 70, 64),
        "sub": (46, 110, 102),
        "accent": (13, 128, 118),
        "accent2": (13, 128, 118),
        "soft": (233, 247, 244),
        "footer": (238, 250, 247),
        "border": (192, 227, 218),
    },
}
DEFAULT_THEME = "rose"

# 状态色：浅色底上要压得住，所以全部走深一档
STATE_COLORS = {
    "healthy": (30, 150, 102),
    "degraded": (206, 128, 12),
    "down": (212, 60, 84),
    "unknown": (140, 132, 158),
    "muted": (150, 145, 165),
    "skipped": (150, 145, 165),
}

# ---------------- 字号 ----------------
F_LABEL = 20    # 头部小标签（如「模型状态」）
F_HEAD = 34     # 头部主角（一句话结论）
F_META = 20     # 头部副信息 / 脚部提示
F_STATS = 20    # 头部汇总点
F_INDEX = 22    # 模型行序号
F_INDEX_G = 18  # 分组行序号（方块更小）
F_NAME = 27     # 模型名 / 分组名
F_NOTE = 18     # 行副标题
F_CELL = 24     # 右对齐数值
F_COLHEAD = 17  # 列头
F_TAG = 17      # 行内小胶囊（如「默认」「静音中」）
F_FOOT = 20     # 底部提示

# 字号 → 已加载字体，按字号缓存避免每次重开字体文件
_font_cache: dict[tuple[str, int], Any] = {}
_font_path: Optional[str] = ""  # 空串=未探测；None=探测过且不可用

# 断行优先在这些字符处断开（模型名基本是「供应商 · 模型-版本」结构）
_BREAK_CHARS = " ·-/_,|:：，、"


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
    path = _font_path or ""
    key = (path, size)
    f = _font_cache.get(key)
    if f is None:
        f = ImageFont.truetype(path, size)
        _font_cache[key] = f
    return f


class _Fonts:
    """字体缓存（同一次渲染里每个字号只加载一次）。"""

    def __init__(self) -> None:
        self._cache: dict[int, Any] = {}

    def get(self, size: int) -> Any:
        f = self._cache.get(size)
        if f is None:
            f = _font(size)
            self._cache[size] = f
        return f


def _mix(c1: tuple, c2: tuple, t: float) -> tuple:
    """两色线性插值（``t=0`` 取 c1，``t=1`` 取 c2）。"""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def theme_colors(name: str = "") -> dict[str, tuple]:
    """取主题配色；名字不认识时回落到默认主题（配置写错不该导致卡片画不出来）。"""
    key = str(name or "").strip().lower() or DEFAULT_THEME
    return THEMES.get(key) or THEMES[DEFAULT_THEME]


def _text_w(font, text: Any) -> float:
    try:
        return float(font.getlength(str(text if text is not None else "")))
    except Exception:
        try:
            return len(str(text or "")) * 12.0
        except Exception:
            return 0.0


def _line_h(font) -> int:
    try:
        return int(sum(font.getmetrics()))
    except Exception:
        return 24


def _fit(font, text: Any, max_w: float) -> str:
    """按像素宽度截断，超出补省略号。中文不能按字符数截。"""
    text = str(text if text is not None else "")
    if not text or _text_w(font, text) <= max_w:
        return text
    ell = "…"
    while text and _text_w(font, text + ell) > max_w:
        text = text[:-1]
    return (text + ell) if text else ell


def _last_break(text: str) -> int:
    """找一个适合断行的位置，返回**断点后的下标**（0 = 没找到，只能硬断）。

    要求断点至少落在行的 35% 之后，避免第一行只剩一两个字。
    """
    n = len(text)
    if n < 2:
        return 0
    floor = max(1, int(n * 0.35))
    for i in range(n - 1, -1, -1):
        if text[i] in _BREAK_CHARS and i >= floor:
            return i + 1
    return 0


def _wrap(font, text: Any, limit: float, max_lines: int = 2, tail: str = "…") -> list[str]:
    """按像素宽度折行，最多 ``max_lines`` 行（超出部分用省略号收尾）。

    逐字符累加（中文没有词边界），但**优先在分隔符处断开**。
    """
    text = " ".join(str(text if text is not None else "").split())
    if not text or limit <= 0 or max_lines <= 0:
        return []
    lines: list[str] = []
    cur = ""
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if _text_w(font, cur + ch) <= limit:
            cur += ch
            idx += 1
            continue
        cut = _last_break(cur)
        if cut:  # 在分隔符处断行（不消费 ch，它继续参与下一行）
            lines.append(cur[:cut])
            cur = cur[cut:]
        else:  # 没有可断点 → 硬断
            lines.append(cur)
            cur = ""
        if len(lines) >= max_lines:
            break
    if len(lines) < max_lines:
        lines.append(cur)
        return lines
    rest = cur + text[idx:]
    if rest:
        lines[-1] = _fit(font, lines[-1] + rest, limit, tail)
    return lines


def _gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Any:
    """纵向渐变图层（``top`` → ``bottom``）。"""
    w, h = size
    layer = Image.new("RGBA", (w, max(1, h)))
    draw = ImageDraw.Draw(layer)
    for row in range(max(1, h)):
        t = row / max(1, h - 1)
        draw.line([(0, row), (w, row)], fill=_mix(top, bottom, t) + (255,))
    return layer


def _seg_mask(size: tuple[int, int], radius: int, *, round_top: bool) -> Any:
    """「头的下沿 / 脚的上沿必须是直角」—— 圆角矩形 + 一块方角补丁。

    ``round_top=True`` 时上两角圆、下沿直（头部用）；False 反之（脚部用）。
    """
    w, h = size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
    if round_top:
        draw.rectangle([0, radius, w, h], fill=255)  # 抹掉下方的圆角
    else:
        draw.rectangle([0, 0, w, h - radius], fill=255)  # 抹掉上方的圆角
    return mask


def _card_mask(size: tuple[int, int], radius: int) -> Any:
    """整卡的外轮廓（四角都圆）。"""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    return mask


def _state_dot(draw, cx: int, cy: int, color: tuple) -> None:
    """状态点外圈一层淡色晕：单个小点在浅底上偏弱，晕一圈才有「这行有问题」的注意力。

    不能直接用带 alpha 的颜色 —— ImageDraw 在 RGBA 图上是替换像素而不是混合，
    白底上会挖出个半透明窟窿。所以先把状态色和白底混成不透明淡色。
    """
    r = 5
    halo = r + 4
    draw.ellipse([cx - halo, cy - halo, cx + halo, cy + halo], fill=_mix(color, CARD_BG, 0.72))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def _index_badge(draw, fonts: "_Fonts", x: int, cy: int, n: Any, *, filled: bool,
                 accent: tuple, size: int = BADGE) -> None:
    """序号方块：实心（模型行）或描边（分组行）。

    序号是这张卡的主要入口（看完回一个数字），所以它单独占一个方块，
    绝不和名字挤在一段文字里 —— 名字被截断时序号必须还在。
    """
    font = fonts.get(F_INDEX if size >= BADGE else F_INDEX_G)
    box = [x, cy - size // 2, x + size, cy + size // 2]
    if filled:
        draw.rounded_rectangle(box, 12, fill=accent + (255,))
        ink = (255, 255, 255)
    else:
        draw.rounded_rectangle(box, 12, fill=CARD_BG + (255,), outline=accent + (255,), width=2)
        ink = accent
    draw.text((x + size // 2, cy), str(n), font=font, fill=ink + (255,), anchor="mm")


def _pill(draw, fonts: "_Fonts", text: str, x: int, cy: int, color: tuple) -> None:
    """在 ``x`` 处往右画一个描边小胶囊（透明底 + 彩色边与字）。"""
    if not text:
        return
    font = fonts.get(F_TAG)
    w = int(_text_w(font, text)) + 24
    h = 30
    draw.rounded_rectangle([x, cy - h // 2, x + w, cy + h // 2], h // 2,
                           fill=CARD_BG + (255,), outline=color + (255,), width=2)
    draw.text((x + 12, cy), text, font=font, fill=color + (255,), anchor="lm")


def _column_widths(fonts: "_Fonts", columns: list[str], rows: list[dict]) -> list[int]:
    """每个数值列需要的像素宽：表头与各单元格文本宽的最大值。"""
    widths = [int(_text_w(fonts.get(F_COLHEAD), c)) for c in columns]
    for r in rows:
        cells = list(r.get("cells") or [])
        for i in range(len(columns)):
            txt = str(cells[i]) if i < len(cells) else "-"
            widths[i] = max(widths[i], int(_text_w(fonts.get(F_CELL), txt)))
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
    *,
    headline: str = "",
    meta: str = "",
    numbered: bool = False,
    width: int = 0,
    theme: str = DEFAULT_THEME,
    brand: str = "模型控制台",
) -> Optional[bytes]:
    """画一张浅色卡片，返回 PNG bytes；字体不可用时返回 None 表示「请降级成文本」。

    Args:
        title: 头部小标签（如「模型状态」）。
        badge: 头部右上角的小标签文本（如「实时」「告警」）；空则不画。
        stats: ``[{"label": str, "value": str, "state": str}]``，头部那行汇总点。
        columns: 右对齐数值列表头，例如 ``["延迟", "成功率"]``。
        rows: 行表。每项要么是模型行，要么是分组行：

            - 模型行 ``{"index", "label", "note", "state", "cells", "tag", "highlight"}``
            - 分组行 ``{"index", "label", "note", "kind": "group"}``

            ``index`` 由**调用方**给定（模型行与分组行共用同一个数字空间），
            这样用户看到几就回几；``numbered=False`` 时不画序号方块。
        footnotes: 底部口径说明。**必须写明延迟是探测还是真实对话**，
            两种延迟不可混读（见 docs/模型监测与配置规划.md 第五节）。
        headline: 头部大字（一句话结论，如「12 个模型 · 正常 9」）；空则不画。
        meta: 头部副信息（如「今天 · 3 分钟内有效」）。
        numbered: 是否给行画序号方块（选单类卡片必开）。
        width: 卡片本体宽度；``0`` 表示按内容自适应（下限 :data:`CARD_W`）。
        theme: 主题名（``rose`` / ``indigo`` / ``teal``）；不认识则用默认主题。
        brand: 脚部右侧的品牌字样；空则不画。
    """
    if Image is None or ImageDraw is None or ImageFont is None or ImageFilter is None:
        return None
    if not resolve_font(font_path):
        return None

    try:
        c = theme_colors(theme)
        rows = list(rows or [])
        stats = [s for s in (stats or []) if s]
        cols = list(columns or [])
        footnotes = [str(x) for x in (footnotes or []) if str(x or "").strip()]
        fonts = _Fonts()

        # ---------- 先量后画 ----------
        col_widths = _column_widths(fonts, cols, rows) if cols else []
        numbers_w = (sum(col_widths) + COL_GAP * (len(col_widths) - 1)) if col_widths else 0
        # 宽度跟着内容走：明细卡有五个数值列，固定 880 会把模型名挤到只剩省略号
        natural = (PAD * 2 + (BADGE + 20 if numbered else 0) + NAME_MIN
                   + (numbers_w + FIT_PAD if numbers_w else 0))
        card_w = min(CARD_W_MAX, max(int(width or 0) or CARD_W, natural))
        inner_l, inner_r = PAD, card_w - PAD
        head_limit = card_w - PAD * 2 - 170  # 右上角可能挂着 badge，名字区先让出位置

        head_lines = _wrap(fonts.get(F_HEAD), headline, head_limit, max_lines=2) if headline else []
        label_h = _line_h(fonts.get(F_LABEL))
        head_lh = int(_line_h(fonts.get(F_HEAD)) * 1.12)
        meta_h = _line_h(fonts.get(F_META))
        stats_h = _line_h(fonts.get(F_STATS))
        content_h = label_h + len(head_lines) * (12 + head_lh)
        if stats:
            content_h += 10 + stats_h
        if meta:
            content_h += 8 + meta_h
        head_h = max(content_h + HEAD_PAD * 2, 118)

        body_h = 24
        if cols and rows:
            body_h += COLHEAD_H
        for r in rows:
            body_h += (GROUP_H if r.get("kind") == "group" else ROW_H) + ROW_GAP
        body_h = body_h - ROW_GAP + 24

        foot_lines = [
            ln for note in footnotes
            for ln in _wrap(fonts.get(F_FOOT), note, card_w - PAD * 2 - 140)
        ]
        foot_h = 0
        if foot_lines:
            foot_h = max(FOOTER_MIN_H, 20 + len(foot_lines) * (meta_h + 6))
        card_h = head_h + body_h + foot_h
        canvas_w, canvas_h = card_w + SHADOW_PAD * 2, card_h + SHADOW_PAD * 2

        # ---------- 1) 卡片本体：白底 → 渐变头 → 渐变脚 → 整体套圆角 ----------
        card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        ImageDraw.Draw(card).rectangle([0, 0, card_w, card_h], fill=CARD_BG + (255,))
        card.paste(
            _gradient((card_w, head_h), c["top"], c["bottom"]), (0, 0),
            _seg_mask((card_w, head_h), RADIUS, round_top=True),
        )
        if foot_h:
            card.paste(
                Image.new("RGBA", (card_w, foot_h), c["footer"] + (255,)), (0, card_h - foot_h),
                _seg_mask((card_w, foot_h), RADIUS, round_top=False),
            )
        card.putalpha(_card_mask((card_w, card_h), RADIUS))

        # ---------- 2) 贴到画布上，先铺一层柔和投影 ----------
        img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        shadow = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            [SHADOW_PAD, SHADOW_PAD + 10, SHADOW_PAD + card_w, SHADOW_PAD + card_h + 10],
            RADIUS, fill=_mix(c["ink"], (255, 255, 255), 0.25) + (78,),
        )
        img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))
        ox, oy = SHADOW_PAD, SHADOW_PAD
        img.alpha_composite(card, (ox, oy))
        draw = ImageDraw.Draw(img)

        # ---------- 3) 头部：小标签 + 主角 + 汇总点 + 副信息 + 右上角标签 ----------
        badge = _fit(fonts.get(F_LABEL), badge, 160)
        badge_w = int(_text_w(fonts.get(F_LABEL), badge)) + 26 if badge else 0
        y = oy + max(HEAD_PAD, (head_h - content_h) // 2)
        draw.text((ox + PAD, y),
                  _fit(fonts.get(F_LABEL), title, head_limit + 130 - badge_w),
                  font=fonts.get(F_LABEL), fill=c["sub"] + (255,))
        y += label_h
        for line in head_lines:
            y += 12
            draw.text((ox + PAD, y), line, font=fonts.get(F_HEAD), fill=c["ink"] + (255,))
            y += head_lh
        if stats:
            y += 10
            cx = ox + PAD
            f_stats = fonts.get(F_STATS)
            for s in stats:
                color = STATE_COLORS.get(str(s.get("state") or "unknown"), STATE_COLORS["unknown"])
                label = str(s.get("label") or "")
                value = str(s.get("value") or "")
                draw.ellipse([cx, y + 7, cx + 9, y + 16], fill=color + (255,))
                draw.text((cx + 16, y), label, font=f_stats, fill=LABEL_INK + (255,))
                lw = int(_text_w(f_stats, label))
                draw.text((cx + 20 + lw, y), value, font=f_stats, fill=color + (255,))
                cx += 20 + lw + int(_text_w(f_stats, value)) + 26
            y += stats_h
        if meta:
            y += 8
            draw.text((ox + PAD, y), _fit(fonts.get(F_META), meta, head_limit),
                      font=fonts.get(F_META), fill=c["sub"] + (255,))
        if badge:
            bx = ox + card_w - PAD - badge_w
            by = oy + HEAD_PAD - 6
            draw.rounded_rectangle([bx, by, bx + badge_w, by + 32], 16,
                                   fill=CARD_BG + (210,), outline=c["border"] + (255,), width=2)
            draw.text((bx + 13, by + 16), badge, font=fonts.get(F_LABEL),
                      fill=_mix(c["accent"], c["ink"], 0.4) + (255,), anchor="lm")

        # ---------- 4) 列头 ----------
        anchors = (_anchors_from_widths(col_widths, ox + card_w - PAD - CELL_PAD)
                   if (col_widths and rows) else [])
        y = oy + head_h + 24
        if anchors:
            for i, col_name in enumerate(cols):
                _, right = anchors[i]
                draw.text((right - _text_w(fonts.get(F_COLHEAD), col_name), y + 8), col_name,
                          font=fonts.get(F_COLHEAD),
                          fill=_mix(c["accent"], TEXT_SOFT, 0.35) + (255,))
            y += COLHEAD_H

        # ---------- 5) 行（分组行 + 模型行，序号共用同一个数字空间） ----------
        body_l, body_r = ox + PAD, ox + card_w - PAD
        for ridx, r in enumerate(rows):
            if r.get("kind") == "group":
                draw.rounded_rectangle([body_l, y, body_r, y + GROUP_H], 16,
                                       fill=_mix(c["accent"], CARD_BG, 0.94) + (255,))
                cy = y + GROUP_H // 2
                name_x = body_l + 16
                if numbered:
                    _index_badge(draw, fonts, body_l + 14, cy, r.get("index", "·"),
                                 filled=False, accent=c["accent"], size=GBADGE)
                    name_x = body_l + 14 + GBADGE + 14
                label = _fit(fonts.get(F_NAME), r.get("label") or "",
                             body_r - name_x - 160)
                draw.text((name_x, cy), label, font=fonts.get(F_NAME),
                          fill=_mix(c["accent"], c["ink"], 0.35) + (255,), anchor="lm")
                note = str(r.get("note") or "")
                if note:
                    lw = int(_text_w(fonts.get(F_NAME), label))
                    draw.text((name_x + lw + 12, cy), _fit(fonts.get(F_NOTE), note, 150),
                              font=fonts.get(F_NOTE), fill=TEXT_MUTED + (255,), anchor="lm")
                y += GROUP_H + ROW_GAP
                continue

            cy = y + ROW_H // 2
            draw.rounded_rectangle(
                [body_l, y, body_r, y + ROW_H], ROW_RADIUS,
                fill=(c["soft"] if r.get("highlight") else ROW_BG) + (255,),
            )
            x = body_l + 18
            if numbered:
                _index_badge(draw, fonts, x, cy, r.get("index", ridx + 1), filled=True,
                             accent=c["accent"])
                x += BADGE + 16
            state = str(r.get("state") or "")
            if state:
                _state_dot(draw, x + 6, cy, STATE_COLORS.get(state, STATE_COLORS["unknown"]))
                x += 20
            label_x = x
            # 名字的可用宽度 = 到第一个数值列（没有数值列就到卡片右沿）再减去小胶囊
            right_limit = (anchors[0][0] - FIT_PAD) if anchors else (body_r - CELL_PAD)
            tag = str(r.get("tag") or "")
            tag_w = (int(_text_w(fonts.get(F_TAG), tag)) + 24 + 12) if tag else 0
            limit = max(60, right_limit - label_x - tag_w)
            label = _fit(fonts.get(F_NAME), r.get("label") or "", limit)
            note = str(r.get("note") or "")
            if tag:
                # 胶囊必须画在**名字那一行**的高度上：画在行中央会和下面那行副标题叠在一起
                _pill(draw, fonts, tag, label_x + int(_text_w(fonts.get(F_NAME), label)) + 12,
                      cy - 15 if note else cy, _mix(c["accent2"], TEXT_DARK, 0.15))
            if note:
                draw.text((label_x, cy - 15), label, font=fonts.get(F_NAME),
                          fill=TEXT_DARK + (255,), anchor="lm")
                draw.text((label_x, cy + 16), _fit(fonts.get(F_NOTE), note, limit),
                          font=fonts.get(F_NOTE), fill=TEXT_MUTED + (255,), anchor="lm")
            else:
                draw.text((label_x, cy), label, font=fonts.get(F_NAME),
                          fill=TEXT_BODY + (255,), anchor="lm")
            cells = list(r.get("cells") or [])
            for i, (_, right) in enumerate(anchors):
                txt = str(cells[i]) if i < len(cells) else "-"
                draw.text((right - _text_w(fonts.get(F_CELL), txt), cy), txt,
                          font=fonts.get(F_CELL), fill=TEXT_BODY + (255,), anchor="rm")
            y += ROW_H + ROW_GAP

        # ---------- 6) 脚部：提示 + 品牌 ----------
        if foot_h:
            fy = oy + card_h - foot_h
            line_h = meta_h + 6
            cy = fy + (foot_h - len(foot_lines) * line_h) // 2 + line_h // 2
            for i, line in enumerate(foot_lines):
                if i == 0:
                    draw.ellipse([ox + PAD, cy - 5, ox + PAD + 10, cy + 5], fill=c["accent"] + (255,))
                draw.text((ox + PAD + (22 if i == 0 else 0), cy), line,
                          font=fonts.get(F_FOOT), fill=_mix(c["accent"], c["ink"], 0.45) + (255,),
                          anchor="lm")
                cy += line_h
            if brand:
                draw.text((ox + card_w - PAD, fy + foot_h // 2), str(brand),
                          font=fonts.get(F_TAG),
                          fill=_mix(c["accent"], (255, 255, 255), 0.45) + (255,), anchor="rm")

        # ---------- 7) 描边（最后画，压在色块之上） ----------
        draw.rounded_rectangle([ox, oy, ox + card_w - 1, oy + card_h - 1], RADIUS,
                               outline=c["border"] + (255,), width=2)

        # ---------- 8) 压成白底 RGB 再存 ----------
        # 直接 convert("RGB") 会把卡外那块**透明**区域变成纯黑（阴影也就成了黑边），
        # 而透明度在聊天客户端里的表现各家不一致，所以这里统一摊到白底上。
        flat = Image.alpha_composite(
            Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255)), img
        )
        out = io.BytesIO()
        flat.convert("RGB").save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:
        # 画图失败绝不能影响功能（调用方会退化成纯文本）
        logger.debug("[ModelPanel] 卡片渲染失败，调用方将退回文本", exc_info=True)
        return None
