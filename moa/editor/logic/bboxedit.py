"""bbox 編集図（EDITOR_SPEC 8.1）の座標変換と、図 → カードの反映。Gradio に依存しない。

土台の画像は、キャンバスの周りに余白（キャンバスの幅・高さ × margin）を付けたもの。部品の座標は左上原点の
`xmin, ymin, xmax, ymax`（土台のピクセル）、MOA の bbox は中心原点の `[y_min, x_min, y_max, x_max]`。
関数はすべて純粋（新しいリストを返し、渡されたものを書き換えない）。
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass

from .cards import can_add
from .forms import MAX_ITEMS

DEFAULT_MARGIN = 0.5
TOL = 1   # 部品が座標を ±1px ずらして返すことがあるので、この範囲の差は「触っていない」とみなす
OUT_OF_RANGE = "図の範囲外"
REVERSED = "大小が逆"


def _r(v: float) -> int:
    """四捨五入（.5 は切り上げ。Python の round のような偶数丸めにしない）。"""
    return int(math.floor(v + 0.5))


@dataclass(frozen=True)
class Frame:
    w: int
    h: int
    mx: int   # 左右の余白（px）
    my: int   # 上下の余白（px）

    @property
    def ox(self) -> int:
        """bbox の x=0 が土台の何 px 目か。"""
        return self.w // 2 + self.mx

    @property
    def oy(self) -> int:
        return self.h // 2 + self.my

    @property
    def width(self) -> int:
        return self.w + 2 * self.mx

    @property
    def height(self) -> int:
        return self.h + 2 * self.my

    @property
    def canvas_rect(self) -> tuple[int, int, int, int]:
        """キャンバスの枠（土台のピクセル）。［全面］と同じ `W//2` を使う。"""
        return self.ox - self.w // 2, self.oy - self.h // 2, self.ox + self.w // 2, self.oy + self.h // 2


def valid_margin(margin) -> bool:
    return isinstance(margin, (int, float)) and not isinstance(margin, bool) and 0 <= margin <= 2


def make_frame(canvas: dict, margin: float = DEFAULT_MARGIN) -> Frame:
    m = margin if valid_margin(margin) else DEFAULT_MARGIN
    w, h = int(canvas["w"]), int(canvas["h"])
    return Frame(w, h, _r(w * m), _r(h * m))


def to_image(bbox, f: Frame) -> tuple[int, int, int, int]:
    """MOA の bbox → 部品の座標 (xmin, ymin, xmax, ymax)。"""
    y0, x0, y1, x1 = bbox
    return x0 + f.ox, y0 + f.oy, x1 + f.ox, y1 + f.oy


def from_image(xmin: float, ymin: float, xmax: float, ymax: float, f: Frame) -> list[int]:
    """部品の座標 → MOA の bbox。整数に丸め、大小が逆なら並べ替え、土台の範囲に収める。"""
    def clamp(v, lo, hi):
        return max(lo, min(hi, v))
    xs = sorted(clamp(_r(v) - f.ox, -f.ox, f.width - f.ox) for v in (xmin, xmax))
    ys = sorted(clamp(_r(v) - f.oy, -f.oy, f.height - f.oy) for v in (ymin, ymax))
    return [ys[0], xs[0], ys[1], xs[1]]


def editable(bbox, f: Frame) -> str | None:
    """図で編集できないときの理由（できるなら None）。"""
    y0, x0, y1, x1 = bbox
    if y0 > y1 or x0 > x1:
        return REVERSED
    if x0 < -f.ox or x1 > f.width - f.ox or y0 < -f.oy or y1 > f.height - f.oy:
        return OUT_OF_RANGE
    return None


def label_of(n: int) -> str:
    return f"#{n}"


def figure_boxes(items: list[dict], f: Frame) -> tuple[list[dict], list[str]]:
    """カード → 図の四角（編集できるものだけ。ラベルはカードの番号）と、編集対象外の説明。"""
    boxes, notes = [], []
    for i, it in enumerate(items):
        reason = editable(it["bbox"], f)
        if reason:
            notes.append(f"{label_of(i + 1)} は{reason}のため、カードで編集してください")
            continue
        xmin, ymin, xmax, ymax = to_image(it["bbox"], f)
        boxes.append({"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax, "label": label_of(i + 1)})
    return boxes, notes


def _parse(box) -> tuple[str, float, float, float, float] | None:
    try:
        return (str(box.get("label") or ""), float(box["xmin"]), float(box["ymin"]), float(box["xmax"]),
                float(box["ymax"]))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _near(a, b) -> bool:
    return all(abs(p - q) <= TOL for p, q in zip(a, b))


def _empty(p) -> bool:
    """丸めたあとに幅か高さが 0 になる四角。"""
    return abs(_r(p[3]) - _r(p[1])) < 1 or abs(_r(p[4]) - _r(p[2])) < 1


def apply_figure(items: list[dict], boxes, f: Frame) -> tuple[list[dict], bool, list[str]]:
    """図から届いた四角をカードに反映する。(新しいカード, 変わったか, 通知)。

    - ラベル `#n` の四角 → n 番目のカードの bbox（±TOL 以内なら触っていない）。編集対象外のカードは変えない
    - 図から消えた四角は、削除とは扱わない（カードはそのまま）
    - ラベルが `#n` でない四角（図で描いたもの）→ 末尾に新しいカード（label=object、caption 空）。上限なら追加しない
    """
    out = [copy.deepcopy(it) for it in items]
    notes: list[str] = []
    parsed = [p for b in (boxes or []) if (p := _parse(b))]
    claimed: set[int] = set()
    foreign: set[str] = set()   # 編集対象外のカードのラベル（図に残っていても無視する）
    for i, it in enumerate(items):
        label = label_of(i + 1)
        if editable(it["bbox"], f):
            foreign.add(label)
            continue
        cands = [k for k, p in enumerate(parsed) if p[0] == label and k not in claimed]
        if not cands:
            continue
        cur = to_image(it["bbox"], f)
        same = next((k for k in cands if _near(parsed[k][1:], cur)), None)
        if same is not None:
            claimed.add(same)
            continue
        k = cands[0]
        claimed.add(k)
        if not _empty(parsed[k]):
            out[i]["bbox"] = from_image(*parsed[k][1:], f)
    for k, p in enumerate(parsed):
        if k in claimed or p[0] in foreign or _empty(p):
            continue
        if not can_add(out):
            notes.append(f"最大{MAX_ITEMS}件です。図では追加できません")
            continue
        out.append({"label": "object", "caption": "", "bbox": from_image(*p[1:], f)})
    return out, out != items, notes


def figure_matches(items: list[dict], boxes, f: Frame) -> bool:
    """図の四角が、カードから作った四角と（±TOL で）一致しているか。一致なら描き直さなくてよい。"""
    expected = {b["label"]: (b["xmin"], b["ymin"], b["xmax"], b["ymax"]) for b in figure_boxes(items, f)[0]}
    parsed = [p for b in (boxes or []) if (p := _parse(b))]
    if len(parsed) != len(expected) or len({p[0] for p in parsed}) != len(parsed):
        return False
    return all(p[0] in expected and _near(p[1:], expected[p[0]]) for p in parsed)
