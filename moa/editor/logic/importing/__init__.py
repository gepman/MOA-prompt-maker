"""インポート（EDITOR_SPEC 15章）。Gradio に依存しない処理。

source（入力を読む）→ inspection（検査）→ plan（計画。書き込まない）→ apply（書き込み直前に再確認して書く）、report（結果の表示）。
"""
from .access import folder_dialog_blocked, is_local_client
from .apply import apply_plan, rewrite_id
from .inspection import Candidate, Inspection, inspect_batch
from .plan import Choices, Plan, resolve
from .report import choices_from_tables, result_rows, summary_md
from .source import Batch, read_batch

__all__ = ["Batch", "Candidate", "Choices", "Inspection", "Plan", "apply_plan", "choices_from_tables",
           "folder_dialog_blocked", "inspect_batch", "is_local_client", "read_batch", "resolve", "result_rows",
           "rewrite_id", "summary_md"]
