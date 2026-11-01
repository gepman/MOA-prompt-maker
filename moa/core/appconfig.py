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


def config_prompt_font_size(config_path: Path | str, default: int = 16) -> int | float:
    """builder.promptFontSize（px）。8〜48 の数値だけ受け付け、それ以外・読めないときは既定。"""
    try:
        data = YAML(typ="safe").load(Path(config_path).read_text(encoding="utf-8"))
        size = (data or {}).get("builder", {}).get("promptFontSize")
    except Exception:
        return default
    ok = isinstance(size, (int, float)) and not isinstance(size, bool) and 8 <= size <= 48
    return size if ok else default
