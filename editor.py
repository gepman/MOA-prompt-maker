"""起動: python editor.py [--data data] [--config config.yaml] [--host 127.0.0.1] [--port 7863]

マスターデータ（Character / Outfit / Background / Action）の編集アプリ。ビルダー（app.py）と同時に起動してよい。
ポートは 起動オプション > config.yaml の editor.port > 7863。
"""
import argparse
from pathlib import Path

from moa.core import Repository
from moa.core.appconfig import config_port
from moa.editor.ui.app import build_editor


def main() -> None:
    ap = argparse.ArgumentParser(description="MOA エディタ（マスターデータの編集）")
    ap.add_argument("--data", default="data", help="データフォルダ（ビルダーと同じものを指す）")
    ap.add_argument("--config", default="config.yaml", help="設定ファイル（editor.port を読む）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    root = Path(args.data)
    if not root.is_dir():
        raise SystemExit(f"データフォルダが見つかりません: {root}")
    repo = Repository(root)
    print(f"データ: {repo.root}  エラー {sum(i.level == 'error' for i in repo.issues)} 件")
    port = args.port if args.port is not None else config_port(args.config, "editor", 7863)
    build_editor(repo).launch(server_name=args.host, server_port=port)


if __name__ == "__main__":
    main()
