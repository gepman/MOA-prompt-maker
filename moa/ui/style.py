"""ビルダーの CSS（SPEC 10.1章）。プロンプト欄と差分欄だけに効く専用クラス `moa-prompt` / `moa-diff` で指定する。"""
from __future__ import annotations

DEFAULT_PROMPT_FONT_PX = 16


def prompt_css(px: int | float = DEFAULT_PROMPT_FONT_PX) -> str:
    return f""".moa-prompt textarea, .moa-prompt .moa-diff {{ font-size: {px}px !important; line-height: 1.7 !important; }}
.moa-diff {{ white-space: pre-wrap; word-break: break-word; padding: 8px 10px; border: 1px solid #c8c8c8; border-radius: 6px; }}
.moa-diff .moa-head {{ font-weight: 700; margin-top: 0.4em; }}
.moa-diff .moa-indent {{ padding-left: 1.2em; }}
.moa-diff .moa-mark {{ display: inline-block; width: 1.2em; font-weight: 700; }}
.moa-diff ins.moa-ins {{ background: #c9f0d0; color: #14532d; font-weight: 700; text-decoration: none; }}
.moa-diff del.moa-del {{ color: #777; text-decoration: line-through; }}
.moa-diff .moa-note {{ color: #666; font-size: 0.8em; }}
.dark .moa-diff {{ border-color: #555; }}
.dark .moa-diff ins.moa-ins {{ background: #1f5b33; color: #d6f5dc; }}
.dark .moa-diff del.moa-del {{ color: #aaa; }}
.dark .moa-diff .moa-note {{ color: #aaa; }}
"""
