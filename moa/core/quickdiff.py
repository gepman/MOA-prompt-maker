"""クイック編集の差分（SPEC 10.1章）。difflib だけで作る。テキストの形式は固定（将来の (b)(c) が読むため）。

    === MOA quick-edit diff ===
    scene: <scene id> / action: <action id> / background: <background id>
    [Anima]
    - <削除したタグ>        ← <出どころ>
    + <追加したタグ>        ← <出どころ>
    [MOSS] items[1].caption
      ...[-旧の単語-]{+新しい単語+}...   ← <出どころ>
    [MOSS] items[0].bbox
      [旧の4数値] → [新の4数値]        ← <出どころ>
    [Negative MOSS] / [Negative Anima]

比較の結果は行の構造（Row）で持つ。固定形式のテキスト（`to_text`）と、画面の色付き表示（moa.ui.diffhtml）は
同じ Row から作る（比較の処理は1つだけ）。
"""
from __future__ import annotations

import difflib
import json
from dataclasses import replace

from .jsonpos import decoded_offsets, locate
from .models import CompileResult
from .diffcore import (ARROW, BASE_NEGATIVE_NOTE, CONTEXT_WORDS, GENERATED_NOTE, Row, _words, note, opcodes,  # noqa: F401
                       similar as _similar, tag_lines, tag_spans, to_text, word_diff)
from .sourcemap import Origin, SourceMap, line_paths
from .textutil import split_tags

HEADER = "=== MOA quick-edit diff ==="
DISCARDED = "# 破棄された編集の差分"
UNSAVED = "(未保存)"
FIELDS = ("anima", "moss", "negm", "nega")   # 差分テキストに並べる順


# ---- MOSS（JSON の構造を辿る）----
def _name(path: tuple) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out


def _compact(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _origin_for(sm: SourceMap, path: tuple) -> Origin | None:
    """パスの出どころ。無ければ（bbox の数値など）親のパスをたどる。"""
    p = path
    while p:
        o = sm.moss_origin(p)
        if o is not None:
            return o
        p = p[:-1]
    return None


class _Loc:
    """編集後の MOSS（JSON テキスト）の中の位置。読めない・位置を調べられないときは、すべて None を返す。"""

    def __init__(self, text: str | None):
        self.text = text or ""
        try:
            self.spans = locate(self.text) if text else {}
        except ValueError:
            self.spans = {}

    def start(self, path: tuple) -> int | None:
        s = self.spans.get(path)
        return s[0] if s else None

    def end(self, path: tuple) -> int | None:
        s = self.spans.get(path)
        return s[1] if s else None

    def string_end(self, path: tuple) -> int | None:
        """文字列の値の、閉じ引用符の直前。"""
        s = self.spans.get(path)
        return s[1] - 1 if s else None

    def in_string(self, path: tuple, decoded: int | None) -> int | None:
        """文字列の値の中の、デコード後の位置 → テキストの位置（バックスラッシュのエスケープを数え直す）。"""
        s = self.spans.get(path)
        if not s or decoded is None:
            return None
        offs = decoded_offsets(self.text, s)
        return offs[min(decoded, len(offs) - 1)]


def _moss_diff(old, new, path: tuple, sm: SourceMap, out: list[Row], loc: _Loc, npath: tuple) -> None:
    """path＝コンパイル結果（出どころ）側のパス、npath＝編集後のテキスト（位置）側のパス。"""
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for k in list(old) + [k for k in new if k not in old]:
            if k not in new or k not in old:
                whole = loc.end(npath)
                after = loc.end(npath + (k,)) if k in new else (whole - 1 if whole else None)
                out.append(Row("head", f"[MOSS] {_name(path + (k,))}"))
                out.append(Row("pair", _compact(old[k]) if k in old else "(なし)", _compact(new[k]) if k in new else "(なし)",
                               indent="  ", note=note([_origin_for(sm, path + (k,))]), pos=after))
            else:
                _moss_diff(old[k], new[k], path + (k,), sm, out, loc, npath + (k,))
    elif path == ("items",) and isinstance(old, list) and isinstance(new, list):
        _items_diff(old, new, sm, out, loc)
    elif path[-1:] == ("meta",) and isinstance(old, list) and isinstance(new, list) \
            and all(isinstance(x, str) for x in old + new):
        origins = [_origin_for(sm, path + (i,)) for i in range(len(old))]
        ends = [loc.string_end(npath + (j,)) for j in range(len(new))]
        start0 = loc.start(npath)
        out.append(Row("head", f"[MOSS] {_name(path)}"))
        out += [replace(r, indent="  ") for r in tag_lines(old, new, origins, ends, None if start0 is None else start0 + 1)]
    elif isinstance(old, str) and isinstance(new, str):
        segs, origins, dec = word_diff(old, new, sm, path)
        seg_pos = [loc.in_string(npath, d) for d in dec]
        out += [Row("head", f"[MOSS] {_name(path)}"),
                Row("inline", segs=segs, indent="  ", note=note(origins), seg_pos=seg_pos,
                    pos=next((p for p in seg_pos if p is not None), None))]
    else:   # bbox などの数値・型違い：旧 → 新
        out += [Row("head", f"[MOSS] {_name(path)}"),
                Row("pair", _compact(old), _compact(new), indent="  ", note=note([_origin_for(sm, path)]),
                    pos=loc.end(npath))]


def _item_origin(sm: SourceMap, i: int) -> Origin | None:
    """item の出どころ（その item を持つ Background / Action の mossItems[n]）。"""
    o = _origin_for(sm, ("items", i, "label"))
    return replace(o, path=o.path.removesuffix(".label")) if o is not None else None


def _items_diff(old: list, new: list, sm: SourceMap, out: list[Row], loc: _Loc) -> None:
    keys_old, keys_new = [_compact(x) for x in old], [_compact(x) for x in new]

    def removed(i, at):   # at＝新しい側で、消えた箇所の直前の item の番号（先頭より前なら -1）
        first = loc.start(("items",))
        pos = loc.end(("items", at)) if at >= 0 else (None if first is None else first + 1)
        out.extend([Row("head", f"[MOSS] items[{i}]（item の削除）"),
                    Row("del", keys_old[i], indent="  ", note=note([_item_origin(sm, i)]), pos=pos)])

    def added(j, after):   # 追加の出どころは挿入位置の直前の item（先頭なら直後）
        out.extend([Row("head", f"[MOSS] items[{j}]（item の追加）"),
                    Row("ins", keys_new[j], indent="  ", note=note([_item_origin(sm, after)] if old else []),
                        pos=loc.end(("items", j)))])

    for tag, i1, i2, j1, j2 in opcodes(keys_old, keys_new):
        if tag == "equal":
            continue
        if i2 - i1 == j2 - j1:   # 同じ個数の置き換え：位置で対応づけて中身を比べる
            for k in range(i2 - i1):
                _moss_diff(old[i1 + k], new[j1 + k], ("items", i1 + k), sm, out, loc, ("items", j1 + k))
            continue
        j = j1   # 個数が違う：似ている item どうしを順に対応づけ、残りを削除／追加にする
        for i in range(i1, i2):
            k = next((k for k in range(j, j2) if _similar(keys_old[i], keys_new[k])), None)
            if k is None:
                removed(i, j - 1)
                continue
            for jj in range(j, k):
                added(jj, max(i - 1, 0))
            _moss_diff(old[i], new[k], ("items", i), sm, out, loc, ("items", k))
            j = k + 1
        for jj in range(j, j2):
            added(jj, max(i2 - 1, 0))


def _line_diff(old_text: str, new_text: str, sm: SourceMap, old_json: dict, out: list[Row]) -> None:
    out.append(Row("head", "[MOSS]（JSON を解析できないため行単位）"))
    old, new = old_text.splitlines(), new_text.splitlines()
    paths = line_paths(old_json)
    ends, pos = [], 0   # 新しい各行の最後尾（改行の直前）の位置
    for raw in new_text.splitlines(keepends=True):
        ends.append(pos + len(raw.rstrip("\r\n")))
        pos += len(raw)

    def origin(i: int) -> Origin | None:
        return _origin_for(sm, paths[i]) if 0 <= i < len(paths) and paths[i] is not None else None

    for tag, i1, i2, j1, j2 in opcodes(old, new):
        if tag == "equal":
            continue
        gone = ends[j1 - 1] if j1 > 0 else 0
        out += [Row("del", old[i], indent="  ", note=note([origin(i)]), pos=gone) for i in range(i1, i2)]
        at = i1 if tag == "replace" else max(i1 - 1, 0)
        out += [Row("ins", new[j], indent="  ", note=note([origin(at)]), pos=ends[j]) for j in range(j1, j2)]


def moss_rows(base: CompileResult, new_text: str) -> list[Row]:
    out: list[Row] = []
    old_json = json.loads(base.moss_prompt)
    try:
        new_json = json.loads(new_text)
    except ValueError:
        _line_diff(base.moss_prompt, new_text, base.sources, old_json, out)
        return out
    _moss_diff(old_json, new_json, (), base.sources, out, _Loc(new_text), ())
    return out


# ---- 欄ごと／全体 ----
def field_rows(base: CompileResult, fld: str, new: str | None) -> list[Row]:
    """1つの欄（anima / moss / negm / nega）の差分の行。編集していなければ空。"""
    sm = base.sources
    current = {"anima": base.anima_prompt, "moss": base.moss_prompt,
               "negm": base.negative_moss, "nega": base.negative_anima}[fld]
    if new is None or new == current:
        return []
    if fld == "moss":
        return moss_rows(base, new)
    title, origins = {"anima": ("[Anima]", sm.anima), "negm": ("[Negative MOSS]", sm.negative_moss),
                      "nega": ("[Negative Anima]", sm.negative_anima)}[fld]
    ends = [e for _, e in tag_spans(new)]
    return [Row("head", title)] + tag_lines(split_tags(current), split_tags(new), origins, ends, 0)


def build_diff(base: CompileResult, edited: dict[str, str], scene_label: str | None, discarded: bool = False) -> str:
    """base＝コンパイル結果、edited＝編集後の文字列（キー moss / anima / negm / nega。編集していない欄は省略か同じ値）。

    scene_label が None または空なら `(未保存)`。discarded は、再コンパイルで破棄された編集の差分。
    """
    lines = ([DISCARDED] if discarded else []) + [
        HEADER, f"scene: {scene_label or UNSAVED} / action: {base.action_id} / background: {base.background_id}"]
    body = [to_text(r) for f in FIELDS for r in field_rows(base, f, edited.get(f))]
    return "\n".join(lines + (body or ["(差分なし)"]))
