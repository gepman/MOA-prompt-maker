"""v1.x のデータ → v2 への機械的な移行（SPEC v2 の14章）。元のフォルダは読むだけで、一切変更しない。"""
from __future__ import annotations

import difflib
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, LiteralScalarString

from moa.core import Repository, compile_scene
from moa.core.constants import COUNT_TAG_RE, META_BASE, MOSS_BBOX_FORMAT, MOSS_CANVAS, MOSS_COORD_SPACE, RATINGS
from moa.core.models import CompileResult, Issue
from moa.core.textutil import collapse_ws

from .v1compile import V1Result, compile_v1

RATING_WORDS = {*RATINGS, "nsfw"}                                   # meta から除く rating 語（nsfw は v1.x の慣用）
QUALITY_RE = re.compile(r"^(masterpiece|best quality|score_\d+\w*)$", re.IGNORECASE)   # 先頭の品質タグ
OTHER_COUNT_RE = re.compile(r"^\d+\+?[a-z]+$")                      # 人数を表していそうな他のタグ（例 3children）
COMPILED_KEYS = {"background", "action", "canvas", "rating", "mossPrompt", "animaPrompt", "negativeApplied",
                 "gen", "seed", "images", "partial"}                # v2 の compiled に書けるキー
SEED_MAX = 4294967295
FIXED_MOSS = {"canvas": MOSS_CANVAS, "bbox_coord_space": MOSS_COORD_SPACE, "bbox_format": MOSS_BBOX_FORMAT}


def _rt() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.allow_unicode = True
    y.width = 1_000_000
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _load(path: Path) -> dict:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}


def _flow_map(values: dict) -> CommentedMap:
    m = CommentedMap(values)
    m.fa.set_flow_style()
    return m


def _flow_seq(values: list) -> CommentedSeq:
    s = CommentedSeq(values)
    s.fa.set_flow_style()
    return s


def _block(text: str) -> LiteralScalarString:
    return LiteralScalarString(text.strip() + "\n")


def _dump(doc, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        _rt().dump(doc, f)


# ---- 結果の入れ物（レポートの材料）----
@dataclass
class SituationResult:
    id: str
    name: str
    src: Path
    rel_folder: str
    background_path: Path | None = None
    action_path: Path | None = None
    slots: list[str] = field(default_factory=list)
    count_tags_left: list[str] = field(default_factory=list)   # Action の anima に残った人数タグ・solo
    other_count_like: list[str] = field(default_factory=list)  # 人数を表していそうな他のタグ
    had_people_tags: bool = False                              # v1.x のテンプレートが girl/boy の人数タグを書いていたか
    removed_leading: list[str] = field(default_factory=list)   # anima の先頭から除いた品質タグ・rating 語
    discarded: list[str] = field(default_factory=list)         # コンパイラの固定値として破棄したもの
    coerced: list[str] = field(default_factory=list)           # 値を変換したもの
    dropped: list[str] = field(default_factory=list)           # 移行先に置き場がなくて捨てたもの
    errors: list[str] = field(default_factory=list)            # 移行できなかったもの
    scenes: list[str] = field(default_factory=list)


@dataclass
class SceneResult:
    id: str
    src: Path
    situation_id: str
    out_path: Path | None = None
    notes: list[str] = field(default_factory=list)
    before: V1Result | None = None
    before_error: str | None = None
    after: CompileResult | None = None
    after_error: str | None = None


@dataclass
class MigrationResult:
    src: Path
    out: Path
    characters: int = 0
    outfits: int = 0
    folder_files: list[Path] = field(default_factory=list)
    situations: list[SituationResult] = field(default_factory=list)
    scenes: list[SceneResult] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)          # 読み込めなかったファイルなど
    post_issues: list[Issue] = field(default_factory=list)     # 移行後のデータを v2 で読み込んだ結果


# ---- Situation → Background ＋ Action ----
def strip_leading_quality(anima: str) -> tuple[str, list[str]]:
    """anima の先頭に並ぶ品質タグ（masterpiece / best quality / score_*）と rating 語だけを除く。"""
    tags = [t for t in (collapse_ws(p) for p in anima.split(",")) if t]
    removed: list[str] = []
    i = 0
    while i < len(tags) and (QUALITY_RE.match(tags[i]) or tags[i].casefold() in RATING_WORDS):
        removed.append(tags[i])
        i += 1
    return ", ".join(tags[i:]), removed


def _split_meta(meta: list) -> list[str]:
    return [m for m in meta if m != META_BASE and str(m).casefold() not in RATING_WORDS]


def _bbox(value, where: str, res: SituationResult) -> list:
    if not isinstance(value, list) or len(value) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        res.errors.append(f"{where} の bbox が数値4つの配列ではありません（{value!r}）。そのまま移行したので、スキーマ検査でエラーになります")
        return value if isinstance(value, list) else [value]
    out = []
    for v in value:
        if isinstance(v, float):
            if v != int(v):
                res.coerced.append(f"{where} の bbox の {v} を {round(v)} に丸めました（bbox は整数）")
            v = round(v)
        out.append(int(v))
    return out


def _moss_parts(sit: dict, res: SituationResult):
    """mossPrompt（JSON 文字列）を読み、(meta, caption, items) を返す。読めなければ None。"""
    try:
        tree = json.loads(sit["mossPrompt"])
    except (KeyError, json.JSONDecodeError, TypeError) as e:
        res.errors.append(f"mossPrompt がJSONとして読めないため、この Situation は移行できませんでした：{e}")
        return None
    if not isinstance(tree, dict) or not isinstance(tree.get("summary"), dict) or not isinstance(tree.get("items", []), list):
        res.errors.append("mossPrompt に summary / items がないため、この Situation は移行できませんでした")
        return None

    summary = tree["summary"]
    for key, fixed in FIXED_MOSS.items():
        value = summary.get(key) if key == "canvas" else tree.get(key)
        if value is not None and value != fixed:
            res.discarded.append(f"{'summary.' if key == 'canvas' else ''}{key} = {value!r}（v2 ではコンパイラの固定値 {fixed!r} になります）")
    for key in set(summary) - {"meta", "canvas", "caption"}:
        res.dropped.append(f"summary.{key}")
    for key in set(tree) - {"summary", "items", "bbox_coord_space", "bbox_format"}:
        res.dropped.append(key)

    items = []
    for i, raw in enumerate(tree.get("items", []), 1):
        if not isinstance(raw, dict):
            res.errors.append(f"items[{i}] がオブジェクトではありません")
            continue
        for key in set(raw) - {"label", "caption", "bbox"}:
            res.dropped.append(f"items[{i}].{key}")
        items.append({"label": raw.get("label", "object"), "caption": raw.get("caption", ""),
                      "bbox": _bbox(raw.get("bbox"), f"items[{i}]", res)})
    return list(summary.get("meta") or []), summary.get("caption", ""), items


def _item_doc(item: dict) -> CommentedMap:
    m = CommentedMap()
    m["label"] = item["label"]
    m["caption"] = DoubleQuotedScalarString(item["caption"])
    m["bbox"] = _flow_seq(item["bbox"]) if isinstance(item["bbox"], list) else item["bbox"]
    return m


def split_situation(sit: dict, src: Path, rel_folder: str) -> tuple[SituationResult, CommentedMap | None, CommentedMap | None]:
    sid = sit["id"]
    res = SituationResult(id=sid, src=src, rel_folder=rel_folder, name=sit.get("name", sid), slots=list(sit.get("slots") or []))
    canvas = sit.get("canvas")
    if not isinstance(canvas, dict) or not {"w", "h"} <= set(canvas):
        res.errors.append("canvas（w, h）がないため、この Situation は移行できませんでした")
        return res, None, None
    parts = _moss_parts(sit, res)
    if parts is None:
        return res, None, None
    meta, caption, items = parts

    bg = CommentedMap()
    bg["id"] = f"{sid}_bg"
    bg["name"] = f"{res.name}（背景）"
    bg["rating"] = sit.get("rating", "safe")
    bg["canvas"] = _flow_map({"w": canvas["w"], "h": canvas["h"]})
    meta_tags = _split_meta(meta)
    if meta_tags:
        bg["metaTags"] = _flow_seq(meta_tags)
    bg["mossCaption"] = ""                        # 機械移行では全文が Action 側に入る（書き分けは手作業。任意）
    bg["mossItems"] = _flow_seq([])
    bg["anima"] = ""

    anima, res.removed_leading = strip_leading_quality(sit.get("animaPrompt", ""))
    tags = [collapse_ws(t) for t in anima.split(",") if t.strip()]
    res.count_tags_left = [t for t in tags if COUNT_TAG_RE.match(t) or t.casefold() == "solo"]
    res.other_count_like = [t for t in tags if OTHER_COUNT_RE.match(t.casefold()) and t not in res.count_tags_left]
    res.had_people_tags = any(COUNT_TAG_RE.match(t) for t in tags)

    action = CommentedMap()
    action["id"] = sid
    action["name"] = res.name
    if sit.get("tags"):
        action["tags"] = _flow_seq(list(sit["tags"]))
    action["rating"] = sit.get("rating", "safe")
    action["background"] = f"{sid}_bg"
    action["slots"] = _flow_seq(list(sit.get("slots") or []))
    outfits = {k: v for k, v in (sit.get("outfits") or {}).items() if v is not None}   # null は捨てる
    if len(outfits) != len(sit.get("outfits") or {}):
        res.dropped.append("outfits の null（v2 では不正。キーを書かないことで委譲を表す）")
    if outfits:
        action["outfits"] = CommentedMap(outfits)
    action["mossCaption"] = _block(caption)
    action["mossItems"] = CommentedSeq([_item_doc(i) for i in items])
    action["anima"] = _block(anima)
    for key in ("negativeMoss", "negativeAnima"):    # ネガティブは元のものをそのまま Action に移す
        if str(sit.get(key) or "").strip():
            action[key] = _block(sit[key])
    return res, bg, action


# ---- Scene ----
def convert_scene(path: Path, d: dict, res: SceneResult) -> CommentedMap:
    """元の YAML を round-trip で読み直し、コメントを残したまま v2 の形に直す。"""
    doc = _rt().load(path.read_text(encoding="utf-8"))
    sit_id = d["situation"]
    outfits = d.get("outfits") or {}

    pos = list(doc).index("situation")
    del doc["situation"]
    doc.insert(pos, "background", f"{sit_id}_bg")
    doc.insert(pos + 1, "action", sit_id)

    cast = CommentedMap()
    for slot, cid in d["cast"].items():
        entry = CommentedMap()
        entry["character"] = cid
        if outfits.get(slot) is not None:
            entry["outfit"] = outfits[slot]
        cast[slot] = entry
    for slot in outfits:
        if slot not in d["cast"]:
            res.notes.append(f"outfits の '{slot}' は cast にないスロットなので捨てました")
    doc["cast"] = cast
    if "outfits" in doc:
        del doc["outfits"]
    if any(v is None for v in outfits.values()):
        res.notes.append("outfits の null は省略にしました（v2 では null は不正）")

    if "denoise" in doc:                                           # 旧 denoise → gen.anima.denoise
        denoise = doc["denoise"]
        del doc["denoise"]
        gen = doc.get("gen")
        if not isinstance(gen, CommentedMap):
            gen = CommentedMap()
            keys = list(doc)
            after = keys.index("applyNegative") if "applyNegative" in doc else keys.index("cast")
            doc.insert(after + 1, "gen", gen)
        anima_gen = gen.get("anima")
        if not isinstance(anima_gen, CommentedMap):
            anima_gen = _flow_map({})
            gen["anima"] = anima_gen
        if "denoise" not in anima_gen:
            anima_gen["denoise"] = denoise
        res.notes.append("旧 denoise を gen.anima.denoise に移しました")

    compiled = doc.get("compiled")
    if isinstance(compiled, CommentedMap):
        for key in [k for k in compiled if k not in COMPILED_KEYS]:
            res.notes.append(f"compiled.{key} は v2 のスキーマにないので捨てました")
            del compiled[key]
        seed = compiled.get("seed")
        if isinstance(seed, int) and not isinstance(seed, bool) and not 0 <= seed <= SEED_MAX:
            # v1.x のシードは最大 2^53 まで。v2 の範囲に収まる別の値では、元の画像を再現できない（偽の記録になる）ので、
            # null にして、元の値は YAML のコメントとレポートに残す。生成済みの画像のパスはそのまま保つ。
            compiled["seed"] = None
            compiled.yaml_add_eol_comment(f"v1.x のシード {seed}（v2 の範囲 0〜{SEED_MAX} の外のため null にしました）", "seed")
            res.notes.append(f"compiled.seed = {seed} は v2 の範囲外（0〜{SEED_MAX}）のため null にしました。"
                             "元の値は YAML のコメントに残し、画像のパスはそのまま保持しています")
    return doc


# ---- フォルダメタ ----
def _folder_for_root(path: Path, root: str, situation_ids: set[str], scene_ids: set[str]) -> CommentedMap:
    doc = _rt().load(path.read_text(encoding="utf-8")) or CommentedMap()
    order = doc.get("order")
    if order:
        out = []
        for entry in order:
            if not str(entry).endswith(".yaml"):
                out.append(entry)                      # フォルダ名はそのまま
                continue
            stem = str(entry)[:-5]
            if stem in situation_ids and root == "backgrounds":
                out.append(f"{stem}_bg.yaml")
            elif stem in situation_ids and root == "actions":
                out.append(entry)
            elif stem in scene_ids and root == "scenes":
                out.append(entry)
        if out:
            doc["order"] = out
        else:
            del doc["order"]
    return doc


# ---- 差分 ----
def diff_lines(before: str, after: str, limit: int = 30) -> list[str]:
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), "移行前", "移行後", lineterm="", n=1))
    return lines[:limit] + ([f"…（残り {len(lines) - limit} 行）"] if len(lines) > limit else [])


def diff_tags(before: str, after: str) -> dict:
    b = [t for t in (collapse_ws(x) for x in before.split(",")) if t]
    a = [t for t in (collapse_ws(x) for x in after.split(",")) if t]
    added, removed = list((Counter(a) - Counter(b)).elements()), list((Counter(b) - Counter(a)).elements())
    return {"added": added, "removed": removed, "reordered": not added and not removed and a != b}


# ---- 全体 ----
def migrate(src: Path, out: Path) -> MigrationResult:
    result = MigrationResult(src=src, out=out)
    out.mkdir(parents=True, exist_ok=True)

    # characters / outfits はそのままコピー（sex は書き足さない＝female 扱い）
    chars, outfits = {}, {}
    for name, store in (("characters", chars), ("outfits", outfits)):
        root = src / name
        if root.is_dir():
            shutil.copytree(root, out / name)
            for p in sorted(root.rglob("*.yaml")):
                if p.name == "_folder.yaml":
                    continue
                try:
                    d = _load(p)
                    store[d["id"]] = d
                except Exception as e:
                    result.problems.append(f"{p.relative_to(src).as_posix()} を読み込めませんでした：{e}")
    result.characters, result.outfits = len(chars), len(outfits)

    # situations/ を Situation と Scene に分ける（`situation` と `cast` の両方を持てば Scene）
    sit_root = src / "situations"
    situations: dict[str, tuple[Path, dict]] = {}
    scene_files: list[tuple[Path, dict]] = []
    if sit_root.is_dir():
        for p in sorted(sit_root.rglob("*.yaml"), key=lambda x: str(x).casefold()):
            if p.name == "_folder.yaml":
                continue
            try:
                d = _load(p)
            except Exception as e:
                result.problems.append(f"{p.relative_to(src).as_posix()} を読み込めませんでした：{e}")
                continue
            if "situation" in d and "cast" in d:
                scene_files.append((p, d))
            elif "mossPrompt" in d:
                situations[d["id"]] = (p, d)
            else:
                result.problems.append(f"{p.relative_to(src).as_posix()} は Situation でも Scene でもないため移行しませんでした")

    # Situation → Background ＋ Action
    for sid, (p, d) in situations.items():
        rel = p.parent.relative_to(sit_root).as_posix()
        folder = Path("" if rel == "." else rel)
        res, bg, action = split_situation(d, p, rel)
        result.situations.append(res)
        if bg is None:
            continue
        res.background_path = out / "backgrounds" / folder / f"{sid}_bg.yaml"
        res.action_path = out / "actions" / folder / f"{sid}.yaml"
        _dump(bg, res.background_path)
        _dump(action, res.action_path)

    # Scene
    by_situation = {r.id: r for r in result.situations}
    for p, d in scene_files:
        rel = p.parent.relative_to(sit_root).as_posix()
        folder = Path("" if rel == "." else rel)
        sres = SceneResult(id=d["id"], src=p, situation_id=d["situation"])
        result.scenes.append(sres)
        if d["situation"] not in situations:
            sres.notes.append(f"Situation '{d['situation']}' が見つかりません（参照先がないまま移行しました）")
        elif by_situation[d["situation"]].background_path is None:
            sres.notes.append(f"Situation '{d['situation']}' を移行できなかったため、参照先がありません")
        else:
            by_situation[d["situation"]].scenes.append(d["id"])
        sres.out_path = out / "scenes" / folder / f"{d['id']}.yaml"
        _dump(convert_scene(p, d, sres), sres.out_path)

    # _folder.yaml：situations/ の各フォルダのものを、backgrounds / actions / scenes に複製する
    sit_ids, scene_ids = set(situations), {d["id"] for _, d in scene_files}
    if sit_root.is_dir():
        for p in sorted(sit_root.rglob("_folder.yaml")):
            rel = p.parent.relative_to(sit_root)
            for root in ("backgrounds", "actions", "scenes"):
                dest = out / root / rel / "_folder.yaml"
                _dump(_folder_for_root(p, root, sit_ids, scene_ids), dest)
                result.folder_files.append(dest)

    # 移行後のデータを v2 で読み込み、移行前後のコンパイル結果を比べる
    repo = Repository(out)
    result.post_issues = list(repo.issues)
    for sres in result.scenes:
        _, scene_dict = next(((p, d) for p, d in scene_files if d["id"] == sres.id), (None, None))
        sit = situations.get(sres.situation_id)
        if sit is not None:
            try:
                sres.before = compile_v1(chars, outfits, sit[1], scene_dict)
            except Exception as e:
                sres.before_error = f"{type(e).__name__}: {e}"
        scene = repo.scene(sres.id)
        if scene is None:
            sres.after_error = "移行後のデータで読み込めません（スキーマ検査の結果を参照）"
        else:
            sres.after = compile_scene(repo, scene)
            if not sres.after.ok:
                sres.after_error = "; ".join(i.message for i in sres.after.issues if i.level == "error")
    return result
