"""Scene.gen / compiled.gen（生成パラメータ）の共通定義。"""
from __future__ import annotations

# 段ごとに上書きできる項目（ワークフローのサンプラーの入力名と同じ）。
# MOSS段は t2i なので denoise は持たない。
GEN_PARAMS: dict[str, tuple[str, ...]] = {
    "moss": ("steps", "cfg"),
    "anima": ("steps", "cfg", "denoise"),
}


def normalize_value(param: str, value):
    """steps は整数、cfg / denoise は丸めた実数にする（スライダーの浮動小数点誤差を除く）。"""
    return int(round(float(value))) if param == "steps" else round(float(value), 4)


def clean_gen(gen: dict | None) -> dict:
    """既知の段・項目だけを、決まった順序で取り出す。空の段は落とす。"""
    out: dict = {}
    for stage, params in GEN_PARAMS.items():
        values = {p: gen[stage][p] for p in params if gen and stage in gen and gen[stage].get(p) is not None}
        if values:
            out[stage] = values
    return out
