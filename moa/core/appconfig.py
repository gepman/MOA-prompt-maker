"""config.yaml の builder.port / editor.port（起動オプションが優先）と builder.promptFontSize。"""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML


def config_port(config_path: Path | str, section: str, default: int) -> int:
    try:
        data = YAML(typ="safe").load(Path(config_path).read_text(encoding="utf-8"))
        port = (data or {}).get(section, {}).get("port")
    except Exception:
        return default
    return port if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 else default


def _valid_font_size(size) -> bool:
    return isinstance(size, (int, float)) and not isinstance(size, bool) and 8 <= size <= 48


def config_prompt_font_size(config_path: Path | str, default: int = 16, section: str | None = None) -> int | float:
    """プロンプト欄の文字サイズ（px）。`section`（例 "editor"）の promptFontSize があればそれ、なければ builder の値、なければ既定。
    8〜48 の数値だけ受け付け、それ以外（数値でない・範囲外）は無効として次を見る。読めないときは既定。"""
    try:
        data = YAML(typ="safe").load(Path(config_path).read_text(encoding="utf-8")) or {}
    except Exception:
        return default
    for name in ([section] if section else []) + ["builder"]:
        block = data.get(name)
        size = block.get("promptFontSize") if isinstance(block, dict) else None
        if _valid_font_size(size):
            return size
    return default
