"""クイック編集（セッション限定。SPEC 10.1章）の状態遷移。Gradio に依存しない純粋関数。

状態 qe（画面のセッションごと）：
  result    … 編集の土台になっているコンパイル結果（出どころ付き）
  key       … (open_seq, action id, background id)。変わると再コンパイルとみなして編集をリセットする
  edits     … 編集した欄だけ {欄: 編集後の文字列}。保存はしない
  discarded … リセットで破棄された編集の差分（最新の1件）
  label     … 差分ヘッダーの scene（未保存なら None）
"""
from __future__ import annotations

import json
from dataclasses import replace

from moa.core.models import CompileResult
from moa.core.quickdiff import build_diff

FIELDS = ("moss", "anima", "negm", "nega")
FIELD_LABEL = {"moss": "MOSS JSON", "anima": "Anima", "negm": "ネガティブ（MOSS用）", "nega": "ネガティブ（Anima用）"}
NEG_OFF_NOTE = "適用OFFのため送信されません"
EDITED = "✏ 編集済み"


class QuickEditError(ValueError):
    pass


def new_qe() -> dict:
    return {"result": None, "key": None, "edits": {}, "discarded": "", "label": None}


def norm(s: str | None) -> str:
    return (s or "").replace("\r\n", "\n")


def base_values(r: CompileResult | None) -> dict[str, str]:
    if r is None or not r.ok:
        return {f: "" for f in FIELDS}
    return {"moss": r.moss_prompt, "anima": r.anima_prompt, "negm": r.negative_moss, "nega": r.negative_anima}


def refresh(qe: dict, r: CompileResult | None, key: tuple, label: str | None) -> tuple[dict, bool]:
    """新しいコンパイル結果を土台にする。(新しい qe, 編集をリセットしたか)。

    新しい結果の4つの文字列か key（Scene を開いた／Action／Background）が変わったときだけリセットする。
    リセットする前に、保留中の編集があれば差分を discarded に自動で残す（確認のモーダルは出さない）。
    """
    qe = {**qe, "edits": dict(qe["edits"])}
    old = qe["result"]
    reset = old is not None and (qe["key"] != key or base_values(old) != base_values(r))
    if reset:
        if qe["edits"] and old.ok:
            qe["discarded"] = build_diff(old, qe["edits"], qe["label"], discarded=True)
        qe["edits"] = {}
    qe.update(result=r, key=key, label=label)
    return qe, reset


def set_edits(qe: dict, values: dict[str, str]) -> dict:
    """欄の今の文字列を取り込む（blur と［差分を更新］のとき）。コンパイル結果と同じ欄は「編集なし」に戻す。"""
    qe = {**qe, "edits": dict(qe["edits"])}
    base = base_values(qe["result"])
    for f in FIELDS:
        if f not in values:
            continue
        v = norm(values[f])
        if v != base[f]:
            qe["edits"][f] = v
        else:
            qe["edits"].pop(f, None)
    return qe


def reset_field(qe: dict, field: str) -> dict:
    qe = {**qe, "edits": dict(qe["edits"])}
    qe["edits"].pop(field, None)
    return qe


def shown_values(qe: dict) -> dict[str, str]:
    base = base_values(qe["result"])
    return {f: qe["edits"].get(f, base[f]) for f in FIELDS}


def statuses(qe: dict, apply_negative: bool) -> list[str]:
    """欄ごとの表示（編集済み／適用OFFの注記）。FIELDS の順。"""
    out = []
    for f in FIELDS:
        if f not in qe["edits"]:
            out.append("")
        elif f in ("negm", "nega") and not apply_negative:
            out.append(f"{EDITED}　**{NEG_OFF_NOTE}**")
        else:
            out.append(EDITED)
    return out


def diff_view(qe: dict) -> str:
    """現在の差分。破棄された編集の差分があれば、その下に続ける。"""
    parts = []
    r = qe["result"]
    if qe["edits"] and r is not None and r.ok:
        parts.append(build_diff(r, qe["edits"], qe["label"]))
    if qe["discarded"]:
        parts.append(qe["discarded"])
    return "\n\n".join(parts)


# ---- 生成 ----
def apply_quick_edit(result: CompileResult, values: dict[str, str]) -> tuple[CompileResult, list[str]]:
    """生成に送る結果を作る。(送る CompileResult, 実際に差し替えた欄)。

    表示中の文字列がコンパイル結果と違う欄だけ差し替える。ネガティブは applyNegative が ON のときだけ。
    MOSS の JSON が読めなければ QuickEditError（生成を止める）。
    """
    base = base_values(result)
    changed = [f for f in FIELDS if f in values and norm(values[f]) != base[f]]
    sent = [f for f in changed if f in ("moss", "anima") or result.apply_negative]
    if "moss" in sent:
        try:
            json.loads(values["moss"])
        except ValueError as e:
            raise QuickEditError(f"MOSS JSON を解析できません（{e}）。直してから生成してください") from e
    key = {"moss": "moss_prompt", "anima": "anima_prompt", "negm": "negative_moss", "nega": "negative_anima"}
    return replace(result, **{key[f]: norm(values[f]) for f in sent}), sent


def quick_info(result: CompileResult, sent: list[str]) -> dict | None:
    """生成結果のスナップショットに足す記録。再試行で最初の送信内容を使うため、土台の文字列も持つ。"""
    return {"fields": list(sent), "base": base_values(result)} if sent else None


def retry_result(result: CompileResult, snap: dict) -> CompileResult:
    """Anima 段だけの再試行で送る結果。クイック編集した生成なら、最初に送った文字列（欄の今の値は使わない）。"""
    if not snap.get("quick_edit"):
        return result
    return replace(result, moss_prompt=snap["moss_prompt"], anima_prompt=snap["anima_prompt"],
                   negative_moss=snap["negative_moss"], negative_anima=snap["negative_anima"])
