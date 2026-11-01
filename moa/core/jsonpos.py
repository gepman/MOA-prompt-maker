"""JSON テキストの中の値の位置（文字オフセット）を調べる。クイック編集の差分から、編集欄の該当箇所へカーソルを動かすために使う。

`json.loads` が通るテキストだけを対象にする（読めないときは ValueError）。位置はすべて Python の文字（コードポイント）単位。
"""
from __future__ import annotations

import json

_WS = " \t\n\r"


def locate(text: str) -> dict[tuple, tuple[int, int]]:
    """すべての値の (開始, 終了) を、パス（キーと添字のタプル）から引ける辞書で返す。文字列は引用符を含む範囲。"""
    spans: dict[tuple, tuple[int, int]] = {}
    n = len(text)

    def ws(i: int) -> int:
        while i < n and text[i] in _WS:
            i += 1
        return i

    def string_end(i: int) -> int:
        i += 1
        while text[i] != '"':
            i += 2 if text[i] == "\\" else 1
        return i + 1

    def value(i: int, path: tuple) -> int:
        i = ws(i)
        start = i
        c = text[i]
        if c == "{":
            i = ws(i + 1)
            if text[i] == "}":
                i += 1
            else:
                while True:
                    i = ws(i)
                    key_end = string_end(i)
                    key = json.loads(text[i:key_end])
                    i = ws(key_end)
                    i = value(i + 1, path + (key,))   # text[i] は ":"
                    i = ws(i)
                    if text[i] == ",":
                        i += 1
                        continue
                    i += 1                             # "}"
                    break
        elif c == "[":
            i = ws(i + 1)
            if text[i] == "]":
                i += 1
            else:
                k = 0
                while True:
                    i = value(i, path + (k,))
                    k += 1
                    i = ws(i)
                    if text[i] == ",":
                        i += 1
                        continue
                    i += 1                             # "]"
                    break
        elif c == '"':
            i = string_end(i)
        else:
            while i < n and text[i] not in ",]} \t\n\r":
                i += 1
        spans[path] = (start, i)
        return i

    try:
        json.loads(text)
        value(0, ())
    except (IndexError, ValueError, RecursionError) as e:
        raise ValueError(f"JSON の位置を調べられません：{e}") from e
    return spans


def decoded_offsets(text: str, span: tuple[int, int]) -> list[int]:
    """文字列の値（span は引用符を含む範囲）について、デコード後の i 文字目が text の何文字目にあたるかの表。
    末尾に「閉じ引用符の位置」を1つ足す（長さ＝デコード後の文字数＋1）。`\\n` `\\"` `\\uXXXX`（サロゲートペア含む）を1文字として数える。"""
    start, end = span[0] + 1, span[1] - 1
    out: list[int] = []
    r = start
    while r < end:
        out.append(r)
        if text[r] != "\\":
            r += 1
        elif text[r + 1] == "u":
            code = int(text[r + 2:r + 6], 16)
            if 0xD800 <= code < 0xDC00 and text[r + 6:r + 8] == "\\u" \
                    and 0xDC00 <= int(text[r + 8:r + 12], 16) < 0xE000:
                r += 12
            else:
                r += 6
        else:
            r += 2
    out.append(end)
    return out
