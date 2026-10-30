"""起動: python app.py [--data data] [--host 127.0.0.1] [--port 7860]"""
import argparse
from pathlib import Path

from moa.core import Repository
from moa.ui.app import build_ui


def main() -> None:
    ap = argparse.ArgumentParser(description="MOSS/Anima プロンプトビルダー")
    ap.add_argument("--data", default="data", help="データフォルダ（characters/ outfits/ situations/ を含む）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7860)
    args = ap.parse_args()

    root = Path(args.data)
    if not root.is_dir():
        raise SystemExit(f"データフォルダが見つかりません: {root}")
    repo = Repository(root)
    print(f"データ: {repo.root}  エラー {sum(i.level == 'error' for i in repo.issues)} 件")
    build_ui(repo).launch(server_name=args.host, server_port=args.port)


if __name__ == "__main__":
    main()
