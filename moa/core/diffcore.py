"""差分の共通部品（ビルダーのクイック編集とエディタの変更点表示の両方が使う）。コンパイルには依存しない。

比較の結果は行の構造（Row）で持ち、固定形式のテキスト（`to_text`）と、画面の色付き表示（moa.ui.diffhtml）は同じ Row から作る。
  - `opcodes`   … 順序つきの比較（同じ要素が繰り返されても、どの位置が消えた／増えたかが決まる）
  - `tag_lines` … タグ単位の差分（Anima・ネガティブ・meta・tags など）
  - `word_diff` … 文字列の単語単位の差分
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from .sourcemap import Origin, SourceMap
from .textutil import split_tags

GENERATED_NOTE = "　※ エディタでは直せない。rating／extras／sex で変える"
BASE_NEGATIVE_NOTE = "　※ エディタでは直せない（全体ベースのネガティブ。アプリの定数）"
CONTEXT_WORDS = 3
ARROW = "   ← "


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
    target: str | None = None      # ジャンプ先の編集欄の名前。行ごとに違う画面（エディタの変更点一覧）で使う。None なら呼び出し側が決める


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


def similar(a: str, b: str) -> bool:
    """似ているか（文字列の類似度 0.6 以上）。item・カードの対応づけに使う。"""
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return m.real_quick_ratio() >= 0.6 and m.quick_ratio() >= 0.6 and m.ratio() >= 0.6


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
