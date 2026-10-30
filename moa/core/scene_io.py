"""Sceneの作成と保存。ruamel.yaml の round-trip でコメントを保持する（9.1章）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString

from .constants import ID_RE
from .models import CompileResult, Scene


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.allow_unicode = True
    y.width = 1_000_000  # 長いタグ列を折り返さない
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _text(s: str):
    return LiteralScalarString(s) if "\n" in s else s


def new_scene_id(situation_id: str, slug: str) -> str:
    """`<situation id>__<scene名>`。slug は [a-z0-9_]+ のみ。"""
    slug = slug.strip()
    scene_id = f"{situation_id}__{slug}"
    if not slug or not ID_RE.match(scene_id):
        raise ValueError("Scene名は半角英小文字・数字・アンダースコアのみで入力してください")
    return scene_id


def _sync_mapping(target: CommentedMap, values: dict) -> None:
    """既存のマッピングを in-place で更新する（周囲のコメントを残すため）。"""
    for k in [k for k in target if k not in values]:
        del target[k]
    for k, v in values.items():
        target[k] = v


@dataclass
class GenerationRecord:
    """compiled.seed / compiled.images に書く生成結果。images は MOSS段 → Anima段の順。"""
    seed: int | None
    images: list[str]


def save_scene(repo, scene: Scene, result: CompileResult, path: Path | None = None,
               generation: GenerationRecord | None = None) -> Path:
    """Sceneを保存し compiled にスナップショットを書き込む。errorがあれば保存しない。

    generation があれば seed と images を書く。なければ既存の値を残すが、保存済みの
    スナップショットと今回のプロンプト／ネガティブ適用が食い違う場合は、画像と内容が
    合わなくなるため seed と images をリセットする。
    """
    if not result.ok:
        raise ValueError("検証エラーがあるため保存できません")

    y = _yaml()
    if path is None:
        path = repo.path_of("scene", scene.id)
    if path is None:  # 新規：Situationと同じフォルダに置く
        sit_path = repo.path_of("situation", scene.situation)
        path = sit_path.parent / f"{scene.id}.yaml"
        if path.exists():
            raise ValueError(f"{path.name} は既に存在します")
        doc = CommentedMap()
        doc["id"] = scene.id
    else:
        with open(path, encoding="utf-8") as f:
            doc = y.load(f)

    if scene.name:
        doc["name"] = scene.name
    doc["situation"] = scene.situation
    if isinstance(doc.get("cast"), CommentedMap):
        _sync_mapping(doc["cast"], dict(scene.cast))
    else:
        doc["cast"] = dict(scene.cast)

    specified = {k: v for k, v in scene.outfits.items() if v is not None}
    if specified:
        if isinstance(doc.get("outfits"), CommentedMap):
            _sync_mapping(doc["outfits"], specified)
        else:
            doc["outfits"] = specified
    elif "outfits" in doc:
        del doc["outfits"]

    doc["applyNegative"] = scene.apply_negative

    compiled = doc.get("compiled")
    if not isinstance(compiled, CommentedMap):
        compiled = CommentedMap()
        compiled["seed"] = None
        compiled["images"] = []
        doc["compiled"] = compiled
    old_moss, old_anima = compiled.get("mossPrompt"), compiled.get("animaPrompt")
    if generation is not None:
        compiled["seed"] = generation.seed
        compiled["images"] = list(generation.images)
    elif old_moss is not None and old_anima is not None and (
            str(old_moss).strip() != result.moss_prompt.strip()
            or str(old_anima).strip() != result.anima_prompt.strip()
            or bool(compiled.get("negativeApplied", False)) != result.apply_negative):
        compiled["seed"] = None
        compiled["images"] = []
    compiled["mossPrompt"] = _text(result.moss_prompt)
    compiled["animaPrompt"] = _text(result.anima_prompt)
    compiled["negativeApplied"] = result.apply_negative

    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        y.dump(doc, f)
    os.replace(tmp, path)
    return path
