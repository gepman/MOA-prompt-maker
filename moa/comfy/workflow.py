"""ワークフローへの値の差し込み（apply）と、割り当て候補の推定（suggest）。"""
from __future__ import annotations

import copy
from collections import deque

from moa.core.gen import GEN_PARAMS, clean_gen
from moa.core.models import CompileResult

from .client import ComfyError
from .config import ComfyConfig, STAGES


SIZE_UNIT = 16        # モデルが要求する幅・高さの倍数
MIN_SIDE = 64         # 倍率をかけた辺の下限（これ未満にはしない）
SMALL_SIDE = 512      # これ未満になるときは警告を出す


def anima_size(canvas: dict, scale: float) -> tuple[int, int]:
    """canvas に倍率をかけた Anima 段の (幅, 高さ)。それぞれ最も近い16の倍数にし、下限は MIN_SIDE。"""
    def one(x: int) -> int:
        return max(MIN_SIDE, int(x * scale / SIZE_UNIT + 0.5) * SIZE_UNIT)
    return one(canvas["w"]), one(canvas["h"])


def supports_anima_scale(cfg: ComfyConfig) -> bool:
    """倍率を使えるか。canvas を差し込む設定（width / height）があり、MOSS→Anima の接続が1つに決まること。"""
    if not (cfg.width and cfg.height):
        return False
    try:
        stage_boundary(cfg.workflow, cfg)
    except ComfyError:
        return False
    return True


def apply(workflow: dict, cfg: ComfyConfig, result: CompileResult, seed: int,
          gen: dict | None = None, anima_scale: float = 1.0) -> dict:
    """プロンプト・シード・サイズを差し込んだワークフローを返す（元は変更しない）。

    ネガティブは「適用ON」のときだけ合成済みで置き換える。OFFのときはワークフローに
    書かれている既定をそのまま使う（ノードに触らない）。
    gen（Scene.gen）は、書かれた項目だけサンプラーの入力を上書きする。解決順は
    Scene.gen → ワークフローの値で、書かれなかった項目はワークフローの値のまま。
    nodes.samplers にない段の項目は無視する。
    anima_scale が1.0以外のときだけ、MOSS→Anima の接続に ImageScale を挟み、Anima段の解像度を変える
    （画面だけの設定で、Scene には保存しない）。
    """
    wf = copy.deepcopy(workflow)

    def put(b, value):
        wf[b.node]["inputs"][b.input] = value

    put(cfg.moss_positive, result.moss_prompt)
    put(cfg.anima_positive, result.anima_prompt)
    if result.apply_negative:
        if cfg.moss_negative:
            put(cfg.moss_negative, result.negative_moss)
        if cfg.anima_negative:
            put(cfg.anima_negative, result.negative_anima)
    for b in cfg.seeds:
        put(b, seed)
    for stage, params in clean_gen(gen).items():
        if cfg.has_sampler(stage):
            wf[cfg.samplers[stage]]["inputs"].update(params)
    if cfg.width and cfg.height and result.canvas:
        put(cfg.width, result.canvas["w"])
        put(cfg.height, result.canvas["h"])
        if anima_scale != 1.0:
            _insert_scale(wf, cfg, *anima_size(result.canvas, anima_scale))
    return wf


def _insert_scale(wf: dict, cfg: ComfyConfig, w: int, h: int) -> None:
    """MOSS段の画像を受け取る接続（VAEEncode.pixels など）の手前に、指定サイズへ拡大縮小する ImageScale を挟む。"""
    consumer, input_name = stage_boundary(wf, cfg)
    source = wf[consumer]["inputs"][input_name]
    scale_id = str(max([int(k) for k in wf if k.isdigit()] + [0]) + 1)
    wf[scale_id] = {"class_type": "ImageScale",
                    "inputs": {"image": source, "upscale_method": "lanczos", "width": w, "height": h, "crop": "disabled"},
                    "_meta": {"title": "Anima段の解像度倍率"}}
    wf[consumer]["inputs"][input_name] = [scale_id, 0]


def read_gen(wf: dict, cfg: ComfyConfig) -> dict:
    """送信するワークフローで実際に使われる生成パラメータ（継承分も含む全項目）。"""
    return {stage: {p: wf[cfg.samplers[stage]]["inputs"][p] for p in params}
            for stage, params in GEN_PARAMS.items() if cfg.has_sampler(stage)}


# ---- Anima 段だけの再試行（13.4章）----
def ancestors(wf: dict, roots: list[str]) -> set[str]:
    """roots とその上流のすべてのノード id。"""
    seen: set[str] = set()
    stack = [r for r in roots if r in wf]
    while stack:
        nid = stack.pop()
        if nid not in seen:
            seen.add(nid)
            stack += _upstream(wf, nid)
    return seen


def stage_boundary(wf: dict, cfg: ComfyConfig) -> tuple[str, str]:
    """Anima 段が MOSS 段の画像を受け取る接続 `(ノード id, 入力名)`。

    Anima の保存ノードの上流のうち MOSS 段に属さないノードで、MOSS 段のノードからの接続を持つ入力を探す
    （この ComfyUI ワークフローでは VAEEncode の `pixels`）。1つに決まらなければ再試行できない。
    """
    moss_nodes = ancestors(wf, cfg.outputs["moss"])
    anima_nodes = ancestors(wf, cfg.outputs["anima"])
    edges = [(nid, name) for nid in sorted(anima_nodes - moss_nodes)
             for name, v in wf[nid].get("inputs", {}).items() if _is_link(v) and v[0] in moss_nodes]
    edges = [e for e in edges if e[1] in ("pixels", "image", "images")] or edges
    if len(edges) != 1:
        found = ", ".join(f"{n}.{i}" for n, i in edges) or "なし"
        raise ComfyError("Anima 段だけの再試行には、Anima 段が MOSS 段の画像を受け取る接続が1つに決まる必要があります"
                         f"（見つかった接続：{found}）")
    return edges[0]


def anima_only_workflow(wf: dict, cfg: ComfyConfig, moss_image: dict) -> dict:
    """`apply()` 済みのワークフローから、Anima 段だけのワークフローを作る。

    MOSS の保存ノードを除き、MOSS 段のサンプラーなどは送らない。Anima 段が MOSS の画像を受け取る入力を、
    MOSS 画像を読む LoadImage に付け替える。画像は ComfyUI の output から直接読む（`"<subfolder/>名前 [output]"`）。
    シード・gen・プロンプトは、渡された `wf`（最初の生成と同じ）のまま。
    """
    consumer, input_name = stage_boundary(wf, cfg)
    out = copy.deepcopy(wf)
    load_id = str(max([int(k) for k in out if k.isdigit()] + [0]) + 1)
    sub = moss_image.get("subfolder") or ""
    out[load_id] = {"class_type": "LoadImage",
                    "inputs": {"image": f"{sub + '/' if sub else ''}{moss_image['filename']} [{moss_image.get('type') or 'output'}]"},
                    "_meta": {"title": "MOSS画像（Anima段の再試行）"}}
    out[consumer]["inputs"][input_name] = [load_id, 0]
    for nid in cfg.outputs["moss"]:
        out.pop(nid, None)
    keep = ancestors(out, cfg.outputs["anima"])
    return {k: v for k, v in out.items() if k in keep}


# ---- 割り当て候補の推定 ----
def _is_link(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def _upstream(wf: dict, nid: str) -> list[str]:
    return [v[0] for v in wf[nid].get("inputs", {}).values() if _is_link(v) and v[0] in wf]


def _bfs(wf: dict, start: str, pred, include_start: bool = False) -> str | None:
    """startの上流を近い順にたどり、最初に pred を満たしたノードidを返す。"""
    seen = {start}
    queue = deque([start] if include_start else _upstream(wf, start))
    seen.update(queue)
    while queue:
        nid = queue.popleft()
        if pred(nid):
            return nid
        for up in _upstream(wf, nid):
            if up not in seen:
                seen.add(up)
                queue.append(up)
    return None


def _is_text_node(wf: dict, nid: str) -> bool:
    return isinstance(wf[nid].get("inputs", {}).get("text"), str)


def _is_size_node(wf: dict, nid: str) -> bool:
    i = wf[nid].get("inputs", {})
    return isinstance(i.get("width"), int) and isinstance(i.get("height"), int)


def _title(wf: dict, nid: str) -> str:
    return (wf[nid].get("_meta") or {}).get("title", "")


def _hint(text: str) -> str | None:
    t = text.casefold()
    has_moss, has_anima = "moss" in t, "anima" in t
    return "moss" if has_moss and not has_anima else "anima" if has_anima and not has_moss else None


def _entry(wf: dict, nid: str, input_name: str) -> dict:
    return {"node": nid, "input": input_name, "title": _title(wf, nid), "class_type": wf[nid]["class_type"]}


def suggest(wf: dict) -> dict:
    """KSampler系を起点に、プロンプト・シード・サイズ・保存ノードの割り当て候補を推定する。

    段の判定は、サンプラー／プロンプトノードのタイトルに MOSS・Anima の語があればそれを優先し、
    なければ latent の由来（空latent＝t2i＝MOSS段 / VAEEncode＝i2i＝Anima段）で決める。
    """
    notes: list[str] = []
    nodes: dict[str, object] = {}
    samplers = [n for n, d in wf.items()
                if {"seed", "positive", "negative", "latent_image"} <= set(d.get("inputs", {}))]
    stage_of: dict[str, str] = {}
    size_node: dict[str, str] = {}
    sampler_entries: dict[str, dict] = {}  # 段 -> nodes.samplers の候補

    for sid in sorted(samplers, key=lambda s: (len(s), s)):
        inputs = wf[sid]["inputs"]

        def text_node(link):
            if not _is_link(link) or link[0] not in wf:
                return None
            return link[0] if _is_text_node(wf, link[0]) else _bfs(wf, link[0], lambda n: _is_text_node(wf, n))

        pos, neg = text_node(inputs["positive"]), text_node(inputs["negative"])
        latent =inputs["latent_image"][0] if _is_link(inputs["latent_image"]) else None
        origin = _bfs(wf, latent, lambda n: _is_size_node(wf, n) or "VAEEncode" in wf[n]["class_type"],
                      include_start=True) if latent else None
        structural = None
        if origin:
            structural = "moss" if _is_size_node(wf, origin) else "anima"
        hinted = _hint(_title(wf, sid)) or (_hint(_title(wf, pos)) if pos else None)
        stage = hinted or structural
        if hinted and structural and hinted != structural:
            notes.append(f"サンプラー {sid} は、タイトルは{hinted}段ですが latent の由来は{structural}段です（タイトルを優先）")
        if stage is None:
            notes.append(f"サンプラー {sid} の段（MOSS/Anima）を判定できませんでした")
            continue
        if stage in stage_of.values():
            notes.append(f"{stage}段のサンプラーが複数あります（{sid} は割り当てていません）")
            continue
        stage_of[sid] = stage
        sampler_entries[stage] = _entry(wf, sid, "-")
        if pos:
            nodes[f"{stage}Positive"] = _entry(wf, pos, "text")
        if neg:
            nodes[f"{stage}Negative"] = _entry(wf, neg, "text")
        if origin and _is_size_node(wf, origin):
            size_node[stage] = origin

    if "moss" in size_node:
        nodes["width"] = _entry(wf, size_node["moss"], "width")
        nodes["height"] = _entry(wf, size_node["moss"], "height")
    elif size_node:
        notes.append("画像サイズのノードがMOSS段にありません")
    nodes["seed"] = [_entry(wf, sid, "seed") for sid in stage_of]
    if sampler_entries:
        nodes["samplers"] = sampler_entries  # Scene.gen（steps / cfg / denoise）で上書きするサンプラー

    outputs: dict[str, list[str]] = {s: [] for s in STAGES}
    for nid, d in wf.items():
        if not any(k in d["class_type"] for k in ("SaveImage", "PreviewImage")):
            continue
        sampler = _bfs(wf, nid, lambda n: n in stage_of)
        if sampler is None:
            notes.append(f"保存ノード {nid} の上流にサンプラーが見つかりません")
        else:
            outputs[stage_of[sampler]].append(nid)
    for stage in STAGES:
        if f"{stage}Positive" not in nodes:
            notes.append(f"{stage}段のポジティブプロンプトのノードが見つかりません")
        if not outputs[stage]:
            notes.append(f"{stage}段の画像保存ノードが見つかりません")
    return {"nodes": nodes, "outputs": outputs, "notes": notes}


def suggestion_to_yaml(s: dict) -> str:
    """config.yaml の nodes/outputs に貼り付けられる形で出力する。"""
    def fmt(e: dict) -> str:
        return f'{{node: "{e["node"]}", input: {e["input"]}}}'

    lines = ["nodes:"]
    for key, e in s["nodes"].items():
        if key == "seed":
            lines.append("  seed:")
            lines += [f"    - {fmt(x)}   # {x['title']} ({x['class_type']})" for x in e]
        elif key == "samplers":
            lines.append("  samplers:")
            lines += [f'    {stage}: {{node: "{x["node"]}"}}   # {x["title"]} ({x["class_type"]})'
                      for stage, x in e.items()]
        else:
            lines.append(f"  {key}: {fmt(e)}   # {e['title']} ({e['class_type']})")
    lines.append("outputs:")
    for stage, ids in s["outputs"].items():
        lines.append(f"  {stage}: [{', '.join(chr(34) + i + chr(34) for i in ids)}]")
    if s["notes"]:
        lines += ["", "# 注意"] + [f"# - {n}" for n in s["notes"]]
    return "\n".join(lines) + "\n"


def effective_gen(cfg: ComfyConfig, overrides: dict | None) -> dict:
    """Scene.gen の上書き → ワークフローの値 で解決した、全項目の実効値（13.4章の記録条件で比べる）。"""
    over = clean_gen(overrides)
    return {stage: {p: over.get(stage, {}).get(p, cfg.workflow_value(stage, p)) for p in params}
            for stage, params in GEN_PARAMS.items() if cfg.has_sampler(stage)}
