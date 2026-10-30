"""ワークフローから config.yaml の割り当て候補を出力する。

    python -m moa.comfy.suggest workflow/moss_to_anima_i2i_api_v1.1.json
"""
import argparse
import json
import sys
from pathlib import Path

from .workflow import suggest, suggestion_to_yaml


def main() -> None:
    ap = argparse.ArgumentParser(description="ComfyUI APIワークフローから nodes/outputs の候補を出力")
    ap.add_argument("workflow", type=Path)
    args = ap.parse_args()
    wf = json.loads(args.workflow.read_text(encoding="utf-8"))
    sys.stdout.reconfigure(encoding="utf-8")
    print(suggestion_to_yaml(suggest(wf)), end="")


if __name__ == "__main__":
    main()
