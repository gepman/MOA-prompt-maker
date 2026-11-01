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
import re
from dataclasses import dataclass, field, replace

from .jsonpos import decoded_offsets, locate
from .models import CompileResult
from .sourcemap import Origin, SourceMap, line_paths
from .textutil import split_tags

HEADER = "=== MOA quick-edit diff ==="
DISCARDED = "# 破棄された編集の差分"
UNSAVED = "(未保存)"
GENERATED_NOTE = "　※ エディタでは直せない。rating／extras／sex で変える"
BASE_NEGATIVE_NOTE = "　※ エディタでは直せない（全体ベースのネガティブ。アプリの定数）"
CONTEXT_WORDS = 3
ARROW = "   ← "
FIELDS = ("anima", "moss", "negm", "nega")   # 差分テキストに並べる順


@dataclass
class Row:
    """差分の1行。kind：head（見出し）/ del / ins（削除・追加）/ inline（単語差分）/ pair（旧 → 新）。"""
    kind: str
    text: str = ""                 # head / del / ins の本文。pair では旧
    new: str = ""                  # pair の新
    segs: list[tuple[str, str]] = field(default_factory=list)   # inline：("eq" | "del" | "ins", 文字列)
    indent: str = ""
    note: str = ""                 # 「   ← 出どころ」（コンパイラが付けた部分の注記を含む）
    # 編集欄の中で、この変更の最後尾（削除なら削除した箇所）にあたる位置（コードポイント単位）。画面でクリックしたときのジャンプ先
    pos: int | None = None
    seg_pos: list[int | None] = field(default_factory=list)   # inline：segs と同じ長さ（eq は None）


def to_text(row: Row) -> str:
    """固定形式の1行。"""
    if row.kind == "head":
        return row.text
    if row.kind in ("del", "ins"):
        return f"{row.indent}{'-' if row.kind == 'del' else '+'} {row.text}{row.note}"
    if row.kind == "pair":
        return f"{row.indent}{row.text} → {row.new}{row.note}"
    mark = {"eq": "{}", "del": "[-{}-]", "ins": "{{+{}+}}"}
    return row.indent + "".join(mark[op].format(t) for op, t in row.segs) + row.note


def opcodes(a: list, b: list) -> list[tuple[str, int, int, int, int]]:
    """順序つきの比較。先頭と末尾の一致を先に取ってから、残りを SequenceMatcher にかける。
    同じ要素が繰り返されても、どの位置が消えた／増えたかが決まる（先頭側の一致を優先する）。"""
    pre = 0
    while pre < len(a) and pre < len(b) and a[pre] == b[pre]:
        pre += 1
    suf = 0
    while suf < len(a) - pre and suf < len(b) - pre and a[len(a) - 1 - suf] == b[len(b) - 1 - suf]:
        suf += 1
    ops = [("equal", 0, pre, 0, pre)] if pre else []
    sm = difflib.SequenceMatcher(None, a[pre:len(a) - suf], b[pre:len(b) - suf], autojunk=False)
    ops += [(t, i1 + pre, i2 + pre, j1 + pre, j2 + pre) for t, i1, i2, j1, j2 in sm.get_opcodes()]
    if suf:
        ops.append(("equal", len(a) - suf, len(a), len(b) - suf, len(b)))
    return [o for o in ops if not (o[0] == "equal" and o[1] == o[2])]


def note(origins: list[Origin | None]) -> str:
    """「← 出どころ」。重複は1つにまとめる。コンパイラが付けた部分には注記を付ける。"""
    seen: list[Origin] = []
    for o in origins:
        if o is not None and o not in seen:
            seen.append(o)
    if not seen:
        return ""
    text = ARROW + "、".join(o.label() for o in seen)
    generated = [o for o in seen if o.generated]
    if generated:
        text += BASE_NEGATIVE_NOTE if all(o.path == "negativeBase" for o in generated) else GENERATED_NOTE
    return text


# ---- タグ列（Anima・ネガティブ・meta）----
def tag_spans(text: str) -> list[tuple[int, int]]:
    """split_tags と同じ分け方で、各タグが text の何文字目から何文字目か（前後の空白を除く）。"""
    out: list[tuple[int, int]] = []
    pos = 0
    for part in text.split(","):
        m = re.search(r"\S(?:.*\S)?", part, re.S)
        if m:
            out.append((pos + m.start(), pos + m.end()))
        pos += len(part) + 1
    return out


def tag_lines(old: list[str], new: list[str], origins: list[Origin],
              ends: list[int | None] | None = None, start0: int | None = 0) -> list[Row]:
    """ends[j]＝新しい j 番目のタグの最後尾の位置、start0＝先頭より前の位置（ジャンプ先。省略可）。"""
    def end_of(k: int) -> int | None:
        if k < 0:
            return start0
        return ends[k] if ends is not None and k < len(ends) else None

    rows: list[Row] = []
    for tag, i1, i2, j1, j2 in opcodes(old, new):
        if tag == "equal":
            continue
        for i in range(i1, i2):   # 削除は、消えた箇所（新しい側の直前のタグの最後尾）
            rows.append(Row("del", old[i], note=note([origins[i] if i < len(origins) else None]), pos=end_of(j1 - 1)))
        if j2 > j1:
            # 追加の出どころは挿入位置の所有者（置き換えなら置き換えた位置のタグ、挿入なら直前のタグ。先頭なら直後）
            at = i1 if tag == "replace" else (i1 - 1 if i1 > 0 else 0)
            owner = origins[at] if 0 <= at < len(origins) else None
            rows += [Row("ins", new[j], note=note([owner]), pos=end_of(j)) for j in range(j1, j2)]
    return rows


# ---- 文字列フィールドの単語差分 ----
def _words(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def word_diff(old: str, new: str, sm: SourceMap | None, path: tuple
              ) -> tuple[list[tuple[str, str]], list[Origin | None], list[int | None]]:
    """(segs, 出どころ, 各 seg のジャンプ先)。segs は ("eq" | "del" | "ins", 文字列) の並び。
    ジャンプ先は新しい文字列の中の位置：追加は追加した語の最後尾、削除は消えた箇所（直前の語の最後尾。先頭なら 0）。"""
    ow, nw = _words(old), _words(new)
    ops = [o for o in opcodes([w[0] for w in ow], [w[0] for w in nw]) if o[0] != "equal"]
    if not ops:   # 空白だけの違い
        return [("del", old), ("ins", new)], [], [len(new), len(new)]
    first, last = ops[0], ops[-1]
    start = max(first[1] - CONTEXT_WORDS, 0)
    end = min(last[2] + CONTEXT_WORDS, len(ow))
    Piece = tuple[str, str, int | None]
    pieces: list[list[Piece]] = []   # 語、または変更（削除＋追加）のまとまり。間は半角スペース1つ
    origins: list[Origin | None] = []
    pos = start
    for _, i1, i2, j1, j2 in ops:
        pieces += [[("eq", w[0], None)] for w in ow[pos:i1]]
        deleted = " ".join(w[0] for w in ow[i1:i2])
        added = " ".join(w[0] for w in nw[j1:j2])
        change: list[Piece] = []
        if deleted:
            change.append(("del", deleted, nw[j1 - 1][2] if j1 > 0 else 0))
        if added:
            change.append(("ins", added, nw[j2 - 1][2]))
        pieces.append(change)
        if sm is not None:
            if i2 > i1:
                origins.append(sm.moss_origin(path, ow[i1][1], ow[i2 - 1][2]))
            else:   # 追加だけ：直前の語の持ち主（先頭なら直後の語）
                k = i1 - 1 if i1 > 0 else 0
                origins.append(sm.moss_origin(path, ow[k][1], ow[k][2]) if ow else sm.moss_origin(path))
        pos = i2
    pieces += [[("eq", w[0], None)] for w in ow[pos:end]]
    flat: list[Piece] = [("eq", "...", None)] if start > 0 else []
    for n, piece in enumerate(pieces):
        flat += ([("eq", " ", None)] if n else []) + piece
    if end < len(ow):
        flat.append(("eq", "...", None))
    return [(op, t) for op, t, _ in flat], origins, [p for _, _, p in flat]


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


def _similar(a: str, b: str) -> bool:
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return m.real_quick_ratio() >= 0.6 and m.quick_ratio() >= 0.6 and m.ratio() >= 0.6


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
