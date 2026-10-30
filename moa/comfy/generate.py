"""Scene のコンパイル結果を ComfyUI で生成し、画像を取得・記録するところまで。"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from moa.core.models import CompileResult
from moa.core.scene_io import GenerationRecord

from .client import ComfyClient, ComfyError
from .config import STAGE_LABEL, STAGES, ComfyConfig
from .workflow import apply


@dataclass
class ImageRef:
    stage: str  # "moss" | "anima"
    filename: str
    record_path: str  # compiled.images に書くパス
    data: bytes = field(repr=False)


@dataclass
class Generation:
    seed: int
    images: list[ImageRef]  # MOSS段 → Anima段の順
    moss_prompt: str
    anima_prompt: str
    negative_applied: bool
    denoise: float | None = None  # 実際に送った値。denoise を書き換えない設定なら None

    def record(self) -> GenerationRecord:
        return GenerationRecord(self.seed, [i.record_path for i in self.images], self.denoise)


def new_seed() -> int:
    """生成ごとのランダムなシード。JSONやブラウザで丸められない 2^53 未満にする。"""
    return random.SystemRandom().randrange(2**53)


def _unique(path: Path) -> Path:
    n = 1
    cand = path
    while cand.exists():
        cand = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return cand


def _record_path(cfg: ComfyConfig, subfolder: str, filename: str, data: bytes, copy_dir: Path | None) -> str:
    if copy_dir is not None:  # 明示されたフォルダへコピーし、コピー先を記録する
        copy_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique(copy_dir / filename)
        dest.write_bytes(data)
        return str(dest)
    if cfg.output_dir is not None:
        return str(cfg.output_dir / subfolder / filename)
    return "output/" + (f"{subfolder}/" if subfolder else "") + filename  # ComfyUI の output からの相対


def iter_run(cfg: ComfyConfig, result: CompileResult, copy_dir: Path | None = None,
             client: ComfyClient | None = None, seed: int | None = None,
             denoise: float | None = None) -> Iterator[str | Generation]:
    """経過メッセージ(str)を逐次 yield し、最後に Generation を yield する。"""
    if not result.ok:
        raise ComfyError("検証エラーがあるため生成できません")
    client = client or ComfyClient(cfg.url, cfg.timeout_sec)
    seed = new_seed() if seed is None else seed
    denoise = denoise if cfg.denoise else None  # 割り当てがなければ、ワークフローの値のまま

    if result.canvas and (result.canvas["w"] % 16 or result.canvas["h"] % 16):
        yield f"⚠ canvas {result.canvas['w']}×{result.canvas['h']} は16の倍数ではありません（モデルによっては失敗します）"
    yield f"シード {seed}" + (f"、denoise {denoise}" if denoise is not None else "") + " で送信中…"
    prompt_id = client.submit(apply(cfg.workflow, cfg, result, seed, denoise))
    outputs = yield from client.iter_wait(prompt_id)

    images: list[ImageRef] = []
    for stage in STAGES:
        found = [im for nid in cfg.outputs[stage] for im in outputs.get(nid, {}).get("images", [])]
        if not found:
            raise ComfyError(f"{STAGE_LABEL[stage]}の画像が出力に含まれていません（ノード {', '.join(cfg.outputs[stage])}）")
        for im in found:
            yield f"{STAGE_LABEL[stage]}の画像を取得中… {im['filename']}"
            data = client.fetch_image(im["filename"], im.get("subfolder", ""), im.get("type", "output"))
            path = _record_path(cfg, im.get("subfolder", ""), im["filename"], data, copy_dir)
            images.append(ImageRef(stage, im["filename"], path, data))
    yield Generation(seed, images, result.moss_prompt, result.anima_prompt, result.apply_negative, denoise)


def run(cfg: ComfyConfig, result: CompileResult, copy_dir: Path | None = None,
        client: ComfyClient | None = None, seed: int | None = None, progress=None,
        denoise: float | None = None) -> Generation:
    gen = None
    for ev in iter_run(cfg, result, copy_dir, client, seed, denoise):
        if isinstance(ev, Generation):
            gen = ev
        elif progress:
            progress(ev)
    return gen
