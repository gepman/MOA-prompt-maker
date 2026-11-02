"""Scene のコンパイル結果を ComfyUI で生成する（13章）。

通常は MOSS → Anima を **1つのプロンプト**で送る。WebSocket の進捗で「MOSS の保存ノードが終わった」時点を知り、
段ごとのタイムアウトと部分結果（Anima 段の失敗＋MOSS 画像あり）を扱う。Anima 段が失敗したときだけ、
MOSS 画像を入力にした Anima 段だけのプロンプトを再送できる（`iter_retry_anima`）。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Iterator

from moa.core.constants import SEED_MAX
from moa.core.models import CompileResult
from moa.core.scene_io import GenerationRecord

from .client import ComfyClient, ComfyError
from .config import STAGE_LABEL, STAGES, ComfyConfig
from .workflow import SMALL_SIDE, anima_only_workflow, anima_size, apply, read_gen


@dataclass
class ImageRef:
    stage: str  # "moss" | "anima"
    filename: str
    subfolder: str
    type: str
    record_path: str  # compiled.images に書くパス
    data: bytes = field(repr=False)


@dataclass
class Generation:
    seed: int
    images: list[ImageRef]  # MOSS段 → Anima段の順
    moss_prompt: str
    anima_prompt: str
    negative_applied: bool
    negative_moss: str = ""
    negative_anima: str = ""
    background: str = ""
    action: str = ""
    canvas: dict = field(default_factory=dict)
    gen: dict = field(default_factory=dict)  # 実際に使った生成パラメータ（継承分も含む全項目）
    anima_size: tuple[int, int] | None = None  # 倍率をかけたときの Anima 段の解像度（表示だけに使う。記録しない）
    failed_stage: str | None = None          # "moss" | "anima"
    error: str | None = None

    def stage_images(self, stage: str) -> list[ImageRef]:
        return [i for i in self.images if i.stage == stage]

    @property
    def complete(self) -> bool:
        return self.failed_stage is None and all(self.stage_images(s) for s in STAGES)

    @property
    def partial(self) -> bool:
        """MOSS段の画像だけがある（Anima段は未完了または失敗）。"""
        return bool(self.stage_images("moss")) and not self.stage_images("anima")

    def record(self) -> GenerationRecord:
        return GenerationRecord(self.seed, [i.record_path for i in self.images], self.gen, partial=self.partial)

    def snapshot(self) -> dict:
        """画面の状態に持たせる、画像のバイト列を含まない記録（再試行と記録条件の確認に使う）。"""
        moss = self.stage_images("moss")
        return {
            "seed": self.seed, "images": [i.record_path for i in self.images], "partial": self.partial,
            "failed_stage": self.failed_stage, "error": self.error,
            "moss_prompt": self.moss_prompt, "anima_prompt": self.anima_prompt,
            "negative_applied": self.negative_applied, "negative_moss": self.negative_moss,
            "negative_anima": self.negative_anima, "background": self.background, "action": self.action,
            "canvas": dict(self.canvas), "gen": self.gen,
            "moss_ref": ({"filename": moss[0].filename, "subfolder": moss[0].subfolder, "type": moss[0].type,
                          "record_path": moss[0].record_path} if moss else None),
        }


# ---- シード（13.2章）----
def seed_limit(cfg: ComfyConfig, client: ComfyClient) -> int:
    """SEED_MAX（2^32−1）と、ワークフローの seed 入力の上限（ComfyUI の /object_info）の小さい方。"""
    limits = [SEED_MAX]
    for b in cfg.seeds:
        mx = client.input_max(cfg.workflow[b.node]["class_type"], b.input)
        if mx is not None:
            limits.append(mx)
    return min(limits)


def new_seed(limit: int = SEED_MAX) -> int:
    """生成ごとのランダムなシード（0〜limit）。"""
    return random.SystemRandom().randint(0, limit)


def check_seed(seed: int, limit: int) -> None:
    if not 0 <= seed <= limit:
        raise ComfyError(f"シード {seed} は範囲外です（0〜{limit}）。生成を開始しません")


# ---- 記録するパス ----
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


# ---- 実行（1プロンプト分）----
Outcome = tuple  # (images, failed_stage, error)


def _execute(cfg: ComfyConfig, client: ComfyClient, workflow: dict, expect: list[str],
             copy_dir: Path | None) -> Generator[str, None, Outcome]:
    """1つのプロンプトを送り、WebSocket の進捗で各段の完了・失敗・タイムアウトを扱う。

    expect は、このプロンプトで画像が出る段（通常は moss, anima。再試行では anima だけ）。
    経過メッセージを yield し、最後に `(images, failed_stage, error)` を返す。
    """
    images: list[ImageRef] = []
    seen: dict[str, set[str]] = {s: set() for s in expect}
    done: set[str] = set()
    timeout = cfg.timeout_sec

    def current() -> str | None:
        return next((s for s in expect if s not in done), None)

    failed: str | None = None
    error: str | None = None
    with client.events() as stream:
        prompt_id = client.submit(workflow)
        t0 = time.monotonic()
        deadline: float | None = None  # 各段のタイマーは、実行が始まってから数える（他のジョブのキュー待ちは含めない）
        last_msg = 0.0
        while True:
            now = time.monotonic()
            if deadline is None and now - t0 > timeout:
                client.cancel_queued(prompt_id)
                raise ComfyError(f"{timeout:.0f}秒待っても実行が始まりませんでした。ComfyUI のキューが詰まっていないか確認してください")
            if deadline is not None and now > deadline:
                failed = current()
                error = f"{STAGE_LABEL[failed]}が {timeout:.0f}秒以内に終わらなかったため中断しました（タイムアウト）"
                client.interrupt()
                for _ in range(10):  # 中断の通知を少しだけ待つ（ジョブを残さないため）
                    ev = stream.get(0.5)
                    if ev and ev.get("type") in ("execution_interrupted", "execution_error", "execution_success"):
                        break
                break
            try:
                ev = stream.get(1.0)
            except ComfyError as e:
                failed, error = current(), str(e)
                break
            if ev is None:
                if now - last_msg >= 1.0:
                    last_msg = now
                    stage = current()
                    yield (f"キュー待ち… {now - t0:.0f}秒" if deadline is None
                           else f"{STAGE_LABEL[stage]}を実行中… {now - (deadline - timeout):.0f}秒")
                continue
            data = ev.get("data") or {}
            if data.get("prompt_id") not in (None, prompt_id):
                continue
            kind = ev.get("type")

            if kind == "execution_start":
                deadline = now + timeout
                yield "実行を開始しました"
            elif kind == "progress" and current():
                yield f"{STAGE_LABEL[current()]}：{data.get('value')}/{data.get('max')} ステップ"
            elif kind == "executed":
                node = str(data.get("node"))
                for stage in expect:
                    if node not in cfg.outputs[stage]:
                        continue
                    for im in (data.get("output") or {}).get("images", []):
                        sub, typ = im.get("subfolder", ""), im.get("type", "output")
                        yield f"{STAGE_LABEL[stage]}の画像を取得中… {im['filename']}"
                        try:
                            bytes_ = client.fetch_image(im["filename"], sub, typ)
                            path = _record_path(cfg, sub, im["filename"], bytes_, copy_dir)
                        except (ComfyError, OSError) as e:
                            failed, error = stage, f"{STAGE_LABEL[stage]}の画像を保存できません：{e}"
                            break
                        images.append(ImageRef(stage, im["filename"], sub, typ, path, bytes_))
                    if failed:
                        break
                    seen[stage].add(node)
                    if seen[stage] >= set(cfg.outputs[stage]):
                        done.add(stage)
                        yield f"{STAGE_LABEL[stage]}が完了しました"
                        if current():
                            deadline = time.monotonic() + timeout  # 次の段のタイマー開始
                if failed:
                    break
                if not current():
                    break
            elif kind == "execution_error":
                failed = current() or expect[-1]
                error = (f"{STAGE_LABEL[failed]}で ComfyUI の実行中にエラーが発生しました：{data.get('exception_message', '')}"
                         f"（ノード {data.get('node_id')} {data.get('node_type', '')}）").strip()
                break
            elif kind == "execution_interrupted":
                failed = current() or expect[-1]
                error = f"{STAGE_LABEL[failed]}が中断されました"
                break
            elif kind == "execution_success" or (kind == "executing" and data.get("node") is None):
                break  # プロンプト全体の完了

    if failed is None and current():
        failed = current()
        error = f"{STAGE_LABEL[failed]}の画像が出力に含まれていません（ノード {', '.join(cfg.outputs[failed])}）"
    return images, failed, error


def _prepare(cfg: ComfyConfig, result: CompileResult, client: ComfyClient | None):
    if not result.ok:
        raise ComfyError("検証エラーがあるため生成できません")
    return client or ComfyClient(cfg.url, cfg.timeout_sec)


def _warn_canvas(result: CompileResult) -> Iterator[str]:
    if result.canvas and (result.canvas["w"] % 16 or result.canvas["h"] % 16):
        yield f"⚠ canvas {result.canvas['w']}×{result.canvas['h']} は16の倍数ではありません（モデルによっては失敗します）"


def _scaled_size(result: CompileResult, anima_scale: float) -> tuple[int, int] | None:
    """倍率が効くときの Anima 段の解像度。倍率1.0または canvas なしなら None。"""
    return anima_size(result.canvas, anima_scale) if anima_scale != 1.0 and result.canvas else None


def _warn_scale(size: tuple[int, int] | None, anima_scale: float) -> Iterator[str]:
    if size:
        yield f"Anima段は {size[0]}×{size[1]}（×{anima_scale:g}）で生成します"
        if min(size) < SMALL_SIDE:
            yield f"⚠ Anima段の解像度が {SMALL_SIDE}px 未満です（画質が落ちます）"


def _generation(seed: int, images: list[ImageRef], result: CompileResult, used: dict,
                failed: str | None, error: str | None, size: tuple[int, int] | None = None) -> Generation:
    return Generation(seed, images, result.moss_prompt, result.anima_prompt, result.apply_negative,
                      result.negative_moss, result.negative_anima, result.background_id, result.action_id,
                      dict(result.canvas or {}), used, size, failed, error)


def iter_run(cfg: ComfyConfig, result: CompileResult, copy_dir: Path | None = None,
             client: ComfyClient | None = None, seed: int | None = None,
             gen: dict | None = None, anima_scale: float = 1.0) -> Iterator[str | Generation]:
    """経過メッセージ(str)を逐次 yield し、最後に Generation を yield する。

    段の失敗（タイムアウト・ComfyUI のエラー）は例外にせず、Generation の `failed_stage` / `error` で返す。
    送信前に分かる問題（検証エラー、接続できない、シードが範囲外、ワークフローの拒否）は ComfyError。
    """
    client = _prepare(cfg, result, client)
    limit = seed_limit(cfg, client)
    if seed is None:
        seed = new_seed(limit)
    check_seed(seed, limit)

    yield from _warn_canvas(result)
    workflow = apply(cfg.workflow, cfg, result, seed, gen, anima_scale)
    used = read_gen(workflow, cfg)  # 実際に送る値（Scene.gen → ワークフローの値）
    size = _scaled_size(result, anima_scale)
    yield from _warn_scale(size, anima_scale)
    yield f"シード {seed} で送信中…"
    images, failed, error = yield from _execute(cfg, client, workflow, list(STAGES), copy_dir)
    yield _generation(seed, images, result, used, failed, error, size)


def iter_retry_anima(cfg: ComfyConfig, result: CompileResult, previous: dict, copy_dir: Path | None = None,
                     client: ComfyClient | None = None, gen: dict | None = None,
                     anima_scale: float = 1.0) -> Iterator[str | Generation]:
    """Anima 段だけを再送する。MOSS 画像を入力にし、シード・gen・プロンプトは最初の生成と同じ。

    previous は最初の生成の `Generation.snapshot()`（`failed_stage` が anima で、MOSS 画像があること）。
    """
    if previous.get("failed_stage") != "anima" or not previous.get("moss_ref"):
        raise ComfyError("再試行できるのは、MOSS段が成功して Anima 段が失敗した生成だけです")
    client = _prepare(cfg, result, client)
    seed = previous["seed"]
    check_seed(seed, seed_limit(cfg, client))

    yield from _warn_canvas(result)
    workflow = apply(cfg.workflow, cfg, result, seed, gen, anima_scale)
    used = read_gen(workflow, cfg)
    size = _scaled_size(result, anima_scale)
    yield from _warn_scale(size, anima_scale)
    ref = previous["moss_ref"]
    anima_workflow = anima_only_workflow(workflow, cfg, ref)
    yield f"シード {seed} で Anima 段だけを再送中…"
    moss_bytes = client.fetch_image(ref["filename"], ref["subfolder"], ref["type"])   # 表示のために MOSS 画像を取り直す
    images, failed, error = yield from _execute(cfg, client, anima_workflow, ["anima"], copy_dir)
    moss = ImageRef("moss", ref["filename"], ref["subfolder"], ref["type"], ref["record_path"], moss_bytes)
    yield _generation(seed, [moss] + images, result, used, failed, error, size)


def run(cfg: ComfyConfig, result: CompileResult, copy_dir: Path | None = None, client: ComfyClient | None = None,
        seed: int | None = None, progress=None, gen: dict | None = None, anima_scale: float = 1.0) -> Generation:
    done = None
    for ev in iter_run(cfg, result, copy_dir, client, seed, gen, anima_scale):
        if isinstance(ev, Generation):
            done = ev
        elif progress:
            progress(ev)
    return done


# ---- 記録条件（13.4章）----
def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) < 1e-9
    return a == b


def differences(snap: dict, result: CompileResult, effective_gen: dict | None) -> list[str]:
    """生成時（snapshot）と今の内容で、食い違っている項目名。空なら記録してよい。

    比べるもの：mossPrompt、animaPrompt、ネガティブ適用（ONなら合成ネガティブも）、Background / Action の id、canvas、
    gen の実効値（MOSS steps/cfg、Anima steps/cfg/denoise）。
    """
    out = []
    # クイック編集で生成したときは、送った文字列ではなく編集前（コンパイル結果）で比べる（再試行の可否のため）。
    # 編集した生成を記録しないことは、保存側（moa.ui.logic.save）が snapshot の quick_edit で止める
    base = (snap.get("quick_edit") or {}).get("base") or {
        "moss": snap["moss_prompt"], "anima": snap["anima_prompt"],
        "negm": snap["negative_moss"], "nega": snap["negative_anima"]}
    if base["moss"] != result.moss_prompt:
        out.append("mossPrompt")
    if base["anima"] != result.anima_prompt:
        out.append("animaPrompt")
    if snap["negative_applied"] != result.apply_negative:
        out.append("ネガティブ適用")
    elif result.apply_negative:
        if base["negm"] != result.negative_moss:
            out.append("ネガティブ（MOSS）")
        if base["nega"] != result.negative_anima:
            out.append("ネガティブ（Anima）")
    if snap["background"] != result.background_id:
        out.append("Background")
    if snap["action"] != result.action_id:
        out.append("Action")
    if snap["canvas"] != (result.canvas or {}):
        out.append("canvas")
    if effective_gen is not None:
        for stage, params in snap["gen"].items():
            for name, value in params.items():
                if not _same(effective_gen.get(stage, {}).get(name), value):
                    out.append(f"gen.{stage}.{name}")
    return out
