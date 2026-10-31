"""マスターデータ（Character / Outfit / Background / Action）の書き戻し（EDITOR_SPEC 1章）。

ruamel.yaml で読み込んだ CommentedMap の、**変更した項目だけ**を差し替える。コメントとキー順は壊さない。
values はフォームの値（YAML のキー名のままのプレーンな dict）。
"""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from .filestamp import ensure_unchanged
from .yaml_io import atomic_dump, load_roundtrip

# 種別ごとの正準キー順（新しいキーはこの順の位置に入れる）
ORDER = {
    "character": ["id", "name", "group", "tags", "sex", "defaultOutfit", "moss", "anima",
                  "bodyHairMoss", "bodyHairAnima", "negativeMoss", "negativeAnima"],
    "outfit": ["id", "name", "tags", "moss", "anima", "negativeMoss", "negativeAnima"],
    "background": ["id", "name", "tags", "rating", "canvas", "metaTags", "mossCaption", "mossItems",
                   "anima", "negativeMoss", "negativeAnima"],
    "action": ["id", "name", "tags", "rating", "background", "slots", "outfits", "extras", "metaTags",
               "mossCaption", "mossItems", "anima", "negativeMoss", "negativeAnima"],
}
# 空でもキーを残す項目。それ以外の任意項目は、空ならキーごと消す
REQUIRED = {
    "character": {"id", "name", "moss", "anima"},
    "outfit": {"id", "name", "moss", "anima"},
    "background": {"id", "name", "canvas", "mossCaption", "anima"},
    "action": {"id", "name", "slots", "mossCaption", "mossItems", "anima"},
}
DEFAULTS = {"rating": "safe", "sex": "female"}   # キーが無いときの値（同じ値ならキーを足さない）
FLOW_LISTS = {"tags", "metaTags", "slots"}
FLOW_MAPS = {"canvas", "extras", "outfits"}
# 文章・タグのブロックは、1行でも `|` で書く（サンプルと同じ書式）
BLOCK_TEXT = {"moss", "anima", "bodyHairMoss", "bodyHairAnima", "negativeMoss", "negativeAnima", "mossCaption"}


def _flow_seq(values: list) -> CommentedSeq:
    s = CommentedSeq(values)
    s.fa.set_flow_style()
    return s


def _flow_map(values: dict) -> CommentedMap:
    m = CommentedMap(values)
    m.fa.set_flow_style()
    return m


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    return not value  # [] / {}


def _plain(value):
    """比較用に、ruamel の型を素の型にする。文字列は前後の空白を無視する。"""
    if isinstance(value, str):
        return str(value).strip()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)) or isinstance(value, CommentedSeq):
        return [_plain(v) for v in value]
    return value


def _text(value: str, existing, block: bool = False):
    """複数行は `|` ブロック。元が `|` なら1行でも `|` を保つ。block=True は常に `|`。"""
    if value.strip() == "":
        return ""
    if block or "\n" in value.strip() or isinstance(existing, LiteralScalarString):
        return LiteralScalarString(value.strip("\n").rstrip() + "\n")
    return value.strip()


def _items_seq(items: list[dict], existing) -> CommentedSeq:
    """mossItems。位置が同じ既存のカードはその場で更新して、コメントを残す。"""
    out = CommentedSeq()
    for i, it in enumerate(items):
        old = existing[i] if isinstance(existing, CommentedSeq) and i < len(existing) else None
        bbox = _flow_seq([int(v) for v in it["bbox"]])
        caption = _text(it["caption"], old.get("caption") if isinstance(old, CommentedMap) else None)
        if isinstance(old, CommentedMap):
            if _plain(old.get("label")) != it["label"]:
                old["label"] = it["label"]
            if _plain(old.get("caption")) != _plain(caption):
                old["caption"] = caption
            if _plain(old.get("bbox")) != list(bbox):
                old["bbox"] = bbox
            out.append(old)
        else:
            m = CommentedMap()
            m["label"], m["caption"], m["bbox"] = it["label"], caption, bbox
            out.append(m)
    return out


def _build(key: str, value, existing):
    if key in ("mossItems",):
        return _items_seq(value, existing)
    if key in FLOW_LISTS:
        return _flow_seq(list(value))
    if key in FLOW_MAPS:
        return _flow_map(dict(value))
    if isinstance(value, str):
        return _text(value, existing, key in BLOCK_TEXT)
    return value


def apply_values(doc: CommentedMap, kind: str, values: dict) -> None:
    """doc に、変更のあった項目だけを反映する。"""
    order, required = ORDER[kind], REQUIRED[kind]
    for key in order:
        if key == "id" or key not in values:
            continue
        value = values[key]
        has = key in doc
        if _is_empty(value) and key not in required:
            if has:
                del doc[key]
            continue
        if key in DEFAULTS and not has and value == DEFAULTS[key]:
            continue
        if has and _plain(doc[key]) == _plain(value):
            continue
        new = _build(key, value, doc[key] if has else None)
        if has:
            doc[key] = new
            continue
        prev = [k for k in order[:order.index(key)] if k in doc]
        doc.insert(list(doc).index(prev[-1]) + 1 if prev else 0, key, new)


def new_path(repo, kind: str, entity_id: str, folder: Path | None) -> Path:
    """新規の保存先 `<folder>/<id>.yaml`。folder はそのルート配下であること。"""
    root = repo.root_dir(kind).resolve()
    target = (Path(folder) if folder else root).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"保存先は {root.name}/ 配下のフォルダを選んでください") from None
    path = target / f"{entity_id}.yaml"
    if path.exists():
        raise ValueError(f"{path.name} は既に存在します")
    return path


def save_entity(repo, kind: str, values: dict, *, path: Path | None = None, folder: Path | None = None,
                expected_stamp: tuple[int, int] | None = None) -> Path:
    """既存ファイル（path）なら差分だけ書き戻す。path が None なら folder に `<id>.yaml` を新規作成する。

    expected_stamp が読み込み時と違えば ExternalChangeError（外部変更の検知）。
    """
    if path is None:
        path = new_path(repo, kind, values["id"], folder)
        doc = CommentedMap()
        doc["id"] = values["id"]
    else:
        ensure_unchanged(path, expected_stamp)
        doc = load_roundtrip(path)
    apply_values(doc, kind, values)
    atomic_dump(doc, path)
    return path


def save_folder_meta(folder: Path, name: str, description: str, order: list[str],
                     expected_stamp: tuple[int, int] | None = None) -> Path:
    """`_folder.yaml`。無ければ作る。全部空でも削除せず、空のマップのファイルとして残す。"""
    path = Path(folder) / "_folder.yaml"
    if path.exists():
        ensure_unchanged(path, expected_stamp)
        doc = load_roundtrip(path)
        if not isinstance(doc, CommentedMap):
            doc = CommentedMap()
    else:
        doc = CommentedMap()
    for key, value in (("name", name.strip()), ("description", description.strip()),
                       ("order", [o.strip() for o in order if o.strip()])):
        if value:
            if key not in doc or _plain(doc[key]) != _plain(value):
                doc[key] = value
        elif key in doc:
            del doc[key]
    atomic_dump(doc, path)
    return path

