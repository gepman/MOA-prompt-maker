"""クイック編集の差分（SPEC 10.1章）。difflib だけで作る。形式は固定（将来の (b)(c) が読むため）。

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
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import replace

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
def tag_lines(old: list[str], new: list[str], origins: list[Origin]) -> list[str]:
    lines = []
    for tag, i1, i2, j1, j2 in opcodes(old, new):
        if tag == "equal":
            continue
        for i in range(i1, i2):
            lines.append(f"- {old[i]}{note([origins[i] if i < len(origins) else None])}")
        if j2 > j1:
            # 追加の出どころは挿入位置の所有者（置き換えなら置き換えた位置のタグ、挿入なら直前のタグ。先頭なら直後）
            at = i1 if tag == "replace" else (i1 - 1 if i1 > 0 else 0)
            owner = origins[at] if 0 <= at < len(origins) else None
            lines += [f"+ {new[j]}{note([owner])}" for j in range(j1, j2)]
    return lines


# ---- 文字列フィールドの単語差分 ----
def _words(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", text)]


def word_diff(old: str, new: str, sm: SourceMap | None, path: tuple) -> tuple[str, list[Origin | None]]:
    ow, nw = _words(old), _words(new)
    ops = [o for o in opcodes([w[0] for w in ow], [w[0] for w in nw]) if o[0] != "equal"]
    if not ops:   # 空白だけの違い
        return f"[-{old}-]{{+{new}+}}", []
    first, last = ops[0], ops[-1]
    start = max(first[1] - CONTEXT_WORDS, 0)
    end = min(last[2] + CONTEXT_WORDS, len(ow))
    pieces: list[str] = []
    origins: list[Origin | None] = []
    pos = start
    for _, i1, i2, j1, j2 in ops:
        pieces += [w[0] for w in ow[pos:i1]]
        deleted = " ".join(w[0] for w in ow[i1:i2])
        added = " ".join(w[0] for w in nw[j1:j2])
        pieces.append((f"[-{deleted}-]" if deleted else "") + (f"{{+{added}+}}" if added else ""))
        if sm is not None:
            if i2 > i1:
                origins.append(sm.moss_origin(path, ow[i1][1], ow[i2 - 1][2]))
            else:   # 追加だけ：直前の語の持ち主（先頭なら直後の語）
                k = i1 - 1 if i1 > 0 else 0
                origins.append(sm.moss_origin(path, ow[k][1], ow[k][2]) if ow else sm.moss_origin(path))
        pos = i2
    pieces += [w[0] for w in ow[pos:end]]
    return ("..." if start > 0 else "") + " ".join(pieces) + ("..." if end < len(ow) else ""), origins


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


def _moss_diff(old, new, path: tuple, sm: SourceMap, out: list[str]) -> None:
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for k in list(old) + [k for k in new if k not in old]:
            if k not in new or k not in old:
                out.append(f"[MOSS] {_name(path + (k,))}")
                out.append(f"  {_compact(old[k]) if k in old else '(なし)'} → "
                           f"{_compact(new[k]) if k in new else '(なし)'}{note([_origin_for(sm, path + (k,))])}")
            else:
                _moss_diff(old[k], new[k], path + (k,), sm, out)
    elif path == ("items",) and isinstance(old, list) and isinstance(new, list):
        _items_diff(old, new, sm, out)
    elif path[-1:] == ("meta",) and isinstance(old, list) and isinstance(new, list) \
            and all(isinstance(x, str) for x in old + new):
        origins = [_origin_for(sm, path + (i,)) for i in range(len(old))]
        out.append(f"[MOSS] {_name(path)}")
        out += ["  " + ln for ln in tag_lines(old, new, origins)]
    elif isinstance(old, str) and isinstance(new, str):
        text, origins = word_diff(old, new, sm, path)
        out += [f"[MOSS] {_name(path)}", f"  {text}{note(origins)}"]
    else:   # bbox などの数値・型違い：旧 → 新
        out += [f"[MOSS] {_name(path)}", f"  {_compact(old)} → {_compact(new)}{note([_origin_for(sm, path)])}"]


def _item_origin(sm: SourceMap, i: int) -> Origin | None:
    """item の出どころ（その item を持つ Background / Action の mossItems[n]）。"""
    o = _origin_for(sm, ("items", i, "label"))
    return replace(o, path=o.path.removesuffix(".label")) if o is not None else None


def _similar(a: str, b: str) -> bool:
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return m.real_quick_ratio() >= 0.6 and m.quick_ratio() >= 0.6 and m.ratio() >= 0.6


def _items_diff(old: list, new: list, sm: SourceMap, out: list[str]) -> None:
    keys_old, keys_new = [_compact(x) for x in old], [_compact(x) for x in new]

    def removed(i):
        out.extend([f"[MOSS] items[{i}]（item の削除）", f"  - {keys_old[i]}{note([_item_origin(sm, i)])}"])

    def added(j, after):   # 追加の出どころは挿入位置の直前の item（先頭なら直後）
        out.extend([f"[MOSS] items[{j}]（item の追加）",
                    f"  + {keys_new[j]}{note([_item_origin(sm, after)] if old else [])}"])

    for tag, i1, i2, j1, j2 in opcodes(keys_old, keys_new):
        if tag == "equal":
            continue
        if i2 - i1 == j2 - j1:   # 同じ個数の置き換え：位置で対応づけて中身を比べる
            for k in range(i2 - i1):
                _moss_diff(old[i1 + k], new[j1 + k], ("items", i1 + k), sm, out)
            continue
        j = j1   # 個数が違う：似ている item どうしを順に対応づけ、残りを削除／追加にする
        for i in range(i1, i2):
            k = next((k for k in range(j, j2) if _similar(keys_old[i], keys_new[k])), None)
            if k is None:
                removed(i)
                continue
            for jj in range(j, k):
                added(jj, max(i - 1, 0))
            _moss_diff(old[i], new[k], ("items", i), sm, out)
            j = k + 1
        for jj in range(j, j2):
            added(jj, max(i2 - 1, 0))


def _line_diff(old_text: str, new_text: str, sm: SourceMap, old_json: dict, out: list[str]) -> None:
    out.append("[MOSS]（JSON を解析できないため行単位）")
    old, new = old_text.splitlines(), new_text.splitlines()
    paths = line_paths(old_json)

    def origin(i: int) -> Origin | None:
        return _origin_for(sm, paths[i]) if 0 <= i < len(paths) and paths[i] is not None else None

    for tag, i1, i2, j1, j2 in opcodes(old, new):
        if tag == "equal":
            continue
        out += [f"  - {old[i]}{note([origin(i)])}" for i in range(i1, i2)]
        at = i1 if tag == "replace" else max(i1 - 1, 0)
        out += [f"  + {new[j]}{note([origin(at)])}" for j in range(j1, j2)]


def moss_lines(base: CompileResult, new_text: str) -> list[str]:
    out: list[str] = []
    old_json = json.loads(base.moss_prompt)
    try:
        new_json = json.loads(new_text)
    except ValueError:
        _line_diff(base.moss_prompt, new_text, base.sources, old_json, out)
        return out
    _moss_diff(old_json, new_json, (), base.sources, out)
    return out


# ---- 全体 ----
def build_diff(base: CompileResult, edited: dict[str, str], scene_label: str | None, discarded: bool = False) -> str:
    """base＝コンパイル結果、edited＝編集後の文字列（キー moss / anima / negm / nega。編集していない欄は省略か同じ値）。

    scene_label が None または空なら `(未保存)`。discarded は、再コンパイルで破棄された編集の差分。
    """
    lines = ([DISCARDED] if discarded else []) + [
        HEADER, f"scene: {scene_label or UNSAVED} / action: {base.action_id} / background: {base.background_id}"]
    sm = base.sources
    body: list[str] = []

    new = edited.get("anima")
    if new is not None and new != base.anima_prompt:
        body += ["[Anima]"] + tag_lines(split_tags(base.anima_prompt), split_tags(new), sm.anima)
    new = edited.get("moss")
    if new is not None and new != base.moss_prompt:
        body += moss_lines(base, new)
    for key, title, old, origins in (("negm", "[Negative MOSS]", base.negative_moss, sm.negative_moss),
                                     ("nega", "[Negative Anima]", base.negative_anima, sm.negative_anima)):
        new = edited.get(key)
        if new is not None and new != old:
            body += [title] + tag_lines(split_tags(old), split_tags(new), origins)
    return "\n".join(lines + (body or ["(差分なし)"]))
