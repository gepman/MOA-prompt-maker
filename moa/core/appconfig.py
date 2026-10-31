"""config.yaml の builder.port / editor.port（起動オプションが優先）。"""
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
