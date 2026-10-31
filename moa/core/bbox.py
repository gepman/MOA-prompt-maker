"""bbox の検査（9.3章）。

座標はキャンバスのピクセル単位の絶対座標。原点はキャンバス中心、x は右が＋、y は下が＋。
bbox は `[y_min, x_min, y_max, x_max]`。キャンバス幅 W・高さ H のとき、有効範囲は
x ∈ [−W/2, +W/2]、y ∈ [−H/2, +H/2]。
"""
from __future__ import annotations

from pathlib import Path

from .models import Issue, MossItem


def _num(v: float) -> str:
    return f"{v:g}"


def check_inversion(items: list[MossItem], where: str, path: Path | None = None, kind: str = "mossItems") -> list[Issue]:
    """`y_min > y_max` または `x_min > x_max` はエラー（キャンバスに依存しない）。"""
    issues = []
    for i, it in enumerate(items, 1):
        y0, x0, y1, x1 = it.bbox
        if y0 > y1 or x0 > x1:
            issues.append(Issue("error", f"{where} の {i}番目の item（label: {it.label}）の bbox の大小が逆転しています"
                                         f"（[{y0}, {x0}, {y1}, {x1}]）", path, f"{kind}[{i}].bbox"))
    return issues


def check_overflow(items: list[MossItem], canvas: dict, where: str, path: Path | None = None,
                   kind: str = "mossItems") -> list[Issue]:
    """キャンバスからのはみ出しを警告にする。完全にはみ出し（重ならない）ものは文言で区別する。"""
    w, h = canvas["w"], canvas["h"]
    half_w, half_h = w / 2, h / 2
    issues = []
    for i, it in enumerate(items, 1):
        y0, x0, y1, x1 = it.bbox
        if y0 > y1 or x0 > x1:
            continue  # 大小逆転は check_inversion がエラーにする
        sides = []
        if x0 < -half_w:
            sides.append(f"左 {_num(-half_w - x0)}px")
        if x1 > half_w:
            sides.append(f"右 {_num(x1 - half_w)}px")
        if y0 < -half_h:
            sides.append(f"上 {_num(-half_h - y0)}px")
        if y1 > half_h:
            sides.append(f"下 {_num(y1 - half_h)}px")
        if not sides:
            continue
        outside = x1 < -half_w or x0 > half_w or y1 < -half_h or y0 > half_h
        kind = "キャンバスと重ならない位置にあります（完全にはみ出し）" if outside else "キャンバスからはみ出しています"
        issues.append(Issue("warning", f"{where} の {i}番目の item（label: {it.label}）が{kind}："
                                       f"{', '.join(sides)}（キャンバス {w}×{h}）", path, f"{kind}[{i}].bbox"))
    return issues
