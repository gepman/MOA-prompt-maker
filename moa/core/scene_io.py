"""Sceneの保存。ruamel.yaml の round-trip で、コメントとキー順を保持して書き戻す（9.1章）。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from .constants import ID_RE
from .filestamp import ensure_unchanged
from .gen import clean_gen
from .models import CastEntry, CompileResult, Scene
from .yaml_io import atomic_dump, flow_map as _flow, literal_text as _text, load_roundtrip


def _gen_map(gen: dict) -> CommentedMap:
    """`moss: { steps: 50, cfg: 3.0 }` のように、段ごとに1行で書く。"""
    out = CommentedMap()
    for stage, params in clean_gen(gen).items():
        out[stage] = _flow(params)
    return out


def _cast_map(entry: CastEntry) -> CommentedMap:
    m = CommentedMap()
    m["character"] = entry.character
    if entry.outfit is not None:
        m["outfit"] = entry.outfit
    return m


def validate_scene_id(scene_id: str) -> str:
    """Scene の id は `[a-z0-9_]+`（4章）。"""
    scene_id = scene_id.strip()
    if not ID_RE.match(scene_id):
        raise ValueError("Scene の id は半角英小文字・数字・アンダースコアのみで入力してください")
    return scene_id


@dataclass
class GenerationRecord:
    """compiled.seed / images / gen / partial に書く生成結果。images は MOSS段 → Anima段の順。"""
    seed: int | None
    images: list[str]
    gen: dict | None = None  # 生成時に実際に使った値（ワークフローから継承した分も含む全項目）
    partial: bool = False    # MOSS段の画像だけを記録した部分結果（13.4章）


def _sync_cast(doc: CommentedMap, cast: dict[str, CastEntry]) -> None:
    """既存の cast を in-place で更新する（周囲のコメントを残すため）。"""
    current = doc.get("cast")
    if not isinstance(current, CommentedMap):
        doc["cast"] = CommentedMap({slot: _cast_map(e) for slot, e in cast.items()})
        return
    for slot in [s for s in current if s not in cast]:
        del current[slot]
    for slot, entry in cast.items():
        cur = current.get(slot)
        if isinstance(cur, CommentedMap):
            cur["character"] = entry.character
            if entry.outfit is not None:
                cur["outfit"] = entry.outfit
            elif "outfit" in cur:
                del cur["outfit"]
        else:
            current[slot] = _cast_map(entry)


def save_scene(repo, scene: Scene, result: CompileResult, path: Path | None = None,
               generation: GenerationRecord | None = None, folder: Path | None = None,
               expected_stamp: tuple[int, int] | None = None) -> Path:
    """Sceneを保存し compiled にスナップショットを書き込む（7.6章）。errorがあれば保存しない。

    保存先：既存の Scene なら、その場所。新規なら `folder`（`scenes/` 配下。省略時は `scenes/` 直下）に `<id>.yaml`。
    Scene.gen（上書きした項目だけ）は `gen` に書く。
    generation があれば seed・images・gen（実際に使った全項目）・partial を compiled に書く。
    なければ既存の値を残すが、保存済みのスナップショットと今回のプロンプト／ネガティブ適用が食い違う場合は、
    画像と内容が合わなくなるため seed・images・gen・partial をリセットする（13.4章）。
    expected_stamp（読み込み時の更新情報）があり、保存直前のファイルと違えば ExternalChangeError（EDITOR_SPEC 12章-4）。
    """
    if not result.ok:
        raise ValueError("検証エラーがあるため保存できません")

    if path is None:
        path = repo.path_of("scene", scene.id)
    if path is None:  # 新規
        scenes_root = repo.root_dir("scene").resolve()
        target = (folder or scenes_root).resolve()
        try:
            target.relative_to(scenes_root)
        except ValueError:
            raise ValueError(f"保存先は {scenes_root.name}/ 配下のフォルダを選んでください") from None
        path = target / f"{scene.id}.yaml"
        if path.exists():
            raise ValueError(f"{path.name} は既に存在します")
        doc = CommentedMap()
        doc["id"] = scene.id
    else:
        ensure_unchanged(path, expected_stamp)
        doc = load_roundtrip(path)

    if scene.name:
        doc["name"] = scene.name
    doc["background"] = scene.background
    doc["action"] = scene.action
    _sync_cast(doc, scene.cast)
    doc["applyNegative"] = scene.apply_negative
    gen = _gen_map(scene.gen)
    if gen:
        doc["gen"] = gen
    elif "gen" in doc:
        del doc["gen"]

    compiled = doc.get("compiled")
    fresh = not isinstance(compiled, CommentedMap)
    if fresh:
        compiled = CommentedMap()
        doc["compiled"] = compiled
    old_moss, old_anima = compiled.get("mossPrompt"), compiled.get("animaPrompt")
    stale = old_moss is not None and old_anima is not None and (
        str(old_moss).strip() != result.moss_prompt.strip()
        or str(old_anima).strip() != result.anima_prompt.strip()
        or bool(compiled.get("negativeApplied", False)) != result.apply_negative)

    compiled["background"] = scene.background
    compiled["action"] = scene.action
    compiled["canvas"] = _flow({"w": result.canvas["w"], "h": result.canvas["h"]})
    compiled["rating"] = result.rating
    compiled["mossPrompt"] = _text(result.moss_prompt)
    compiled["animaPrompt"] = _text(result.anima_prompt)
    compiled["negativeApplied"] = result.apply_negative
    if fresh:
        compiled["seed"] = None
        compiled["images"] = []

    if generation is not None:
        compiled["seed"] = generation.seed
        compiled["images"] = list(generation.images)
        used = _gen_map(generation.gen or {})
        if used:
            compiled["gen"] = used
        elif "gen" in compiled:
            del compiled["gen"]
        if generation.partial:
            compiled["partial"] = True
        elif "partial" in compiled:
            del compiled["partial"]
    elif stale:
        compiled["seed"] = None
        compiled["images"] = []
        for key in ("gen", "partial"):
            if key in compiled:
                del compiled[key]

    atomic_dump(doc, path)
    return path
