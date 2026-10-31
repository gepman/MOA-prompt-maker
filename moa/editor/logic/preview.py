"""プレビュー（EDITOR_SPEC 5章）。未保存の内容を使い、保存ファイルには一切触らずにコンパイル結果を見る。

索引の一部をドラフトで差し替えたオーバーレイ（core.overlay）の上で、合成した仮の Scene を `compile_scene` にかける。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from moa.core import CastEntry, Scene, compile_scene
from moa.core.models import Issue
from moa.core.overlay import OverlayRepository
from moa.core.repository import Repository
from moa.core.resolve import resolve_outfit

from .forms import DEFAULT_CANVAS, normalize, to_obj
from .validation import DRAFT_ID

PREVIEW_SCENE_ID = "__preview__"


@dataclass
class PreviewResult:
    ok: bool = False
    moss: str = ""
    anima: str = ""
    negative_moss: str = ""
    negative_anima: str = ""
    people_tags: str = ""
    rating: str = ""
    issues: list[Issue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # 補完したキャスト、服装の解決順など
    canvas: dict = field(default_factory=lambda: dict(DEFAULT_CANVAS))
    bg_items: list[dict] = field(default_factory=list)   # bbox 図用
    action_items: list[dict] = field(default_factory=list)
    chosen: dict = field(default_factory=dict)           # 実際に使った action / background / slot / character


def options(repo: Repository) -> dict[str, list[tuple[str, str]]]:
    """検索付きの選択欄に出す候補 [(表示名（id）, id)]。"""
    def of(kind):
        return [(f"{repo.entry(kind, i).obj.name}（{i}）", i) for i in repo.ids(kind)]
    return {"action": of("action"), "background": of("background"), "character": of("character")}


def _items(obj) -> list[dict]:
    return [{"label": i.label, "caption": i.caption, "bbox": list(i.bbox)} for i in obj.moss_items]


def _scenes(repo: Repository, **match) -> list:
    out = []
    for e in repo.entries["scene"].values():
        s = e.obj
        if "action" in match and s.action != match["action"]:
            continue
        if "background" in match and s.background != match["background"]:
            continue
        if "character" in match and not any(c.character == match["character"] for c in s.cast.values()):
            continue
        out.append(s)
    return out


def _source_scene(repo: Repository, action_id: str | None, prefer: dict | None = None):
    """その Action を使っている既存 Scene（prefer に合うものを優先。無ければ先頭）。"""
    if not action_id:
        return None
    scenes = _scenes(repo, action=action_id)
    for s in scenes:
        if prefer and _scenes_match(s, prefer):
            return s
    return scenes[0] if scenes else None


def _scenes_match(scene, prefer: dict) -> bool:
    if "background" in prefer and scene.background != prefer["background"]:
        return False
    if "character" in prefer and not any(c.character == prefer["character"] for c in scene.cast.values()):
        return False
    return True


def _fill_cast(repo: Repository, action, source, skip_char: str | None) -> tuple[dict, list[tuple[str, str, str]]]:
    """スロットごとのキャスト。Scene のキャスト（服装指定も）→ 無ければキャラ一覧の先頭から。"""
    pool = [c for c in repo.ids("character") if c != skip_char] or repo.ids("character")
    cast, filled, n = {}, [], 0
    for slot in action.slots:
        if source is not None and slot in source.cast:
            e = source.cast[slot]
            cast[slot] = CastEntry(e.character, e.outfit)
            filled.append((slot, e.character, f"Scene {source.id} から"))
        elif pool:
            cast[slot] = CastEntry(pool[n % len(pool)], None)
            filled.append((slot, pool[n % len(pool)], "キャラ一覧から"))
            n += 1
    return cast, filled


def _outfit_order_note(ov, action, slot: str, char_id: str, outfit_id: str) -> str:
    char = ov.character(char_id)
    res = resolve_outfit(slot, None, action.outfits, char.default_outfit if char else None)
    if res.outfit_id == outfit_id and res.source in ("action", "character"):
        where = "Action 指定" if res.source == "action" else "キャラ既定"
        return f"通常の解決順でも {where} で効きます"
    layer = {"action": "Action 指定", "none_action": "Action 指定", "character": "キャラ既定",
             "none_character": "キャラ既定"}.get(res.source)
    if layer:
        shown = res.outfit_id or "none"
        return (f"通常の解決順では {layer}（{shown}）に負けます。このプレビューでは Scene 層に差し込むので、"
                "ドラフトが優先されています")
    return "通常の解決順では指定がありません（Scene で指定したときだけ使われます）"


def preview(repo: Repository, kind: str, values: dict, *, original_id: str | None = None,
            choices: dict | None = None) -> PreviewResult:
    """choices: {"action","background","slot","character"} のうち選んだもの（None は初期値）。"""
    ch = {k: v for k, v in (choices or {}).items() if v}
    res = PreviewResult()
    v = normalize(kind, values)
    vid = v.get("id") or DRAFT_ID
    try:
        draft = to_obj(kind, {**v, "id": vid})
    except Exception as e:   # 型が壊れていてオブジェクトにできない
        res.issues = [Issue("error", f"プレビューできません（入力が不正です）：{e}")]
        return res
    ov = OverlayRepository(repo, {(kind, vid): draft})

    # ---- Action ----
    source_slot = None
    if kind == "action":
        action_id = vid
    else:
        action_id = ch.get("action")
        if not action_id:
            mine = _scenes(repo, **{kind: original_id}) if original_id and kind in ("character", "background") else []
            if mine:
                action_id = mine[0].action
                if kind == "character":
                    source_slot = next(s for s, c in mine[0].cast.items() if c.character == original_id)
            elif kind == "outfit":
                action_id = next((i for i in ov.ids("action") if vid in ov.action(i).outfits.values()), None)
            action_id = action_id or next(iter(ov.ids("action")), None)
    action = ov.action(action_id) if action_id else None
    if action is None:
        res.issues = [Issue("error", "プレビューに使う行為（Action）がありません")]
        return res

    # ---- Background ----
    if kind == "background":
        bg_id = vid
    else:
        bg_id = ch.get("background") or (action.background if action.background and ov.background(action.background)
                                         else None) or next(iter(ov.ids("background")), None)
    if not bg_id or ov.background(bg_id) is None:
        res.issues = [Issue("error", "プレビューに使う背景（Background）がありません")]
        return res

    # ---- キャスト ----
    prefer = {}
    if kind == "background":
        prefer["background"] = original_id
    if kind == "character" and original_id:
        prefer["character"] = original_id
    source = _source_scene(repo, original_id if kind == "action" else action_id, prefer)
    cast, filled = _fill_cast(repo, action, source, vid if kind == "character" else None)

    slot = ch.get("slot") if ch.get("slot") in action.slots else None
    if kind == "character":
        slot = slot or (source_slot if source_slot in action.slots else None) or action.slots[0]
        outfit = None
        if source is not None and slot in source.cast and source.cast[slot].character == original_id:
            outfit = source.cast[slot].outfit
        cast[slot] = CastEntry(vid, outfit)
        filled = [f for f in filled if f[0] != slot]
        res.notes.append(f"差し込み：{slot} = {vid}（ドラフト）")
    elif kind == "outfit":
        slot = slot or next((s for s, o in action.outfits.items() if o == vid and s in action.slots), None) \
            or action.slots[0]
        char_id = ch.get("character") or (cast[slot].character if slot in cast else None)
        if char_id is None or ov.character(char_id) is None:
            res.issues = [Issue("error", "プレビューに使うキャラクターがありません")]
            return res
        cast[slot] = CastEntry(char_id, vid)   # Scene 層（いちばん優先される位置）に差し込む
        filled = [f for f in filled if f[0] != slot]
        res.notes.append(f"差し込み：{slot} の服装 = {vid}（ドラフト。Scene 層）")
        res.notes.append(_outfit_order_note(ov, action, slot, char_id, vid))
    for s, c, src in filled:
        res.notes.append(f"補完：{s} = {c}（{src}）")

    if len(cast) < len(action.slots):
        res.issues = [Issue("error", "プレビューに使うキャラクターが足りません")]
        return res

    scene = Scene(id=PREVIEW_SCENE_ID, background=bg_id, action=action_id, cast=cast)
    out = compile_scene(ov, scene)
    bg = ov.background(bg_id)
    res.issues = out.issues
    res.canvas = dict(bg.canvas)
    res.bg_items, res.action_items = _items(bg), _items(action)
    res.chosen = {"action": action_id, "background": bg_id, "slot": slot,
                  "character": cast[slot].character if slot else None}
    if out.ok:
        res.ok = True
        res.moss, res.anima = out.moss_prompt, out.anima_prompt
        res.negative_moss, res.negative_anima = out.negative_moss, out.negative_anima
        res.people_tags, res.rating = out.people_tags, out.rating
    return res
