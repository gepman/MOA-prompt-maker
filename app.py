"""起動: python app.py [--data data] [--config config.yaml] [--comfy-url URL] [--host 127.0.0.1] [--port 7860]"""
import argparse
from pathlib import Path

from moa.comfy import load_config
from moa.core import Repository
from moa.ui.app import build_ui


def main() -> None:
    ap = argparse.ArgumentParser(description="MOSS/Anima プロンプトビルダー")
    ap.add_argument("--data", default="data", help="データフォルダ（characters/ outfits/ backgrounds/ actions/ scenes/ を含む）")
    ap.add_argument("--config", default="config.yaml", help="ComfyUI連携の設定ファイル")
    ap.add_argument("--comfy-url", default=None, help="ComfyUIのURL（config.yaml の comfyui.url より優先）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7860)
    args = ap.parse_args()

    root = Path(args.data)
    if not root.is_dir():
        raise SystemExit(f"データフォルダが見つかりません: {root}")
    repo = Repository(root)
    print(f"データ: {repo.root}  エラー {sum(i.level == 'error' for i in repo.issues)} 件")
    build_ui(repo, lambda: load_config(args.config, args.comfy_url)).launch(server_name=args.host, server_port=args.port)


if __name__ == "__main__":
    main()
