"""python -m moa.migrate_v2 --src <v1.xのdataフォルダ> --out <新しいフォルダ>

元のフォルダは一切変更しない。結果は --out に新しく書く（--out が空でなければ中止）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .convert import migrate
from .report import render_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m moa.migrate_v2", description="v1.x のデータを v2 に移行する（元のフォルダは変更しない）")
    ap.add_argument("--src", required=True, type=Path, help="v1.x の data フォルダ（characters/ outfits/ situations/ を含む）")
    ap.add_argument("--out", required=True, type=Path, help="移行先の新しいフォルダ（存在するなら空であること）")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    src, out = args.src.resolve(), args.out.resolve()
    if not src.is_dir():
        print(f"エラー：--src がフォルダではありません：{src}")
        return 2
    if out == src or src in out.parents or out in src.parents:
        print("エラー：--out は --src の内側・外側ではなく、別のフォルダにしてください（元のフォルダを変更しないため）")
        return 2
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        print(f"エラー：--out が空ではありません。中止しました：{out}")
        return 2

    result = migrate(src, out)
    report = out / "MIGRATION_REPORT.md"
    report.write_text(render_report(result), encoding="utf-8", newline="\n")

    migrated = sum(s.background_path is not None for s in result.situations)
    n_err = sum(i.level == "error" for i in result.post_issues)
    n_warn = len(result.post_issues) - n_err
    print(f"移行しました：{out}")
    print(f"  characters {result.characters} / outfits {result.outfits} / Situation {migrated}/{len(result.situations)} / Scene {len(result.scenes)}")
    print(f"  移行後の検査：エラー {n_err} / 警告 {n_warn}")
    print(f"  レポート：{report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
