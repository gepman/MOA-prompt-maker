# MOA-prompt-maker — Claude Code 向けメモ

MOSS / Anima 用の画像生成プロンプトを、キャラ・服装・背景・行為を入れ替えて組み立てるツール。
ユーザーは日本語で話す。画面の文言・コミットメッセージ・説明も日本語で書く。

## 仕様書（実装の正本）

- `spec_v2/SPEC.md` … データモデル・コンパイル・検証・ビルダー・ComfyUI連携（v2.0）
- `spec_v2/EDITOR_SPEC.md` … マスターデータ編集アプリ `editor.py`（v2.0）と、それに伴うビルダー側の変更（12章）
- `spec_v2/schemas/` … JSON Schema。`spec_v2/samples/` … サンプルデータと期待出力（受け入れテストの正解）
- `spec/` は旧版（v1.5）。移行テストの入力としてだけ使う。変更しない
- 仕様と実装が食い違ったら、勝手にどちらかに合わせず、ユーザーに確認する

## コマンド（Windows / PowerShell・CMD）

- テスト：`.venv\Scripts\python -m pytest -q`（変更のたびに全件通すこと）
- ビルダー：`.venv\Scripts\python app.py --data <フォルダ>`（ポート 7860）
- エディタ：`.venv\Scripts\python editor.py --data <フォルダ>`（ポート 7863。7861 / 7862 は Forge Neo が使うので使わない）
- 移行：`.venv\Scripts\python -m moa.migrate_v2 --src <v1データ> --out <新フォルダ>`

## 構成

- `moa/core/` … Gradio に依存しない共通処理（読み込み・索引・コンパイル・検証・YAML書き戻し）。ビルダーとエディタの両方が使う
- `moa/ui/` … ビルダーの画面。`moa/editor/` … エディタ（`logic/` に処理、`ui/` に画面）
- `moa/comfy/` … ComfyUI 連携。`moa/migrate_v2/` … v1.x → v2 移行ツール

## データの扱いの約束

- 実データは `Main/`（git 管理外）。**テストや動作確認で実データを書き換えない**。サンプルは一時フォルダにコピーして使う
- YAML の読み書きは ruamel.yaml。書き戻しは変更した項目だけを差し替え、コメントとキー順を残す。書き込みは一時ファイル→置き換え
- エディタでは **ID の変更・削除・ファイル移動はしない**（ユーザー決定）
- 使用中の Background はコピーオンライト保存、使用中の Action は上書き保存（EDITOR_SPEC 1c / 9章）
- Scene の `gen` は「動かした項目だけ」書く。家の標準値はワークフロー JSON 側に置く

## Gradio の注意

- 個数が変わる部品（キャスト枠、mossItems のカード）は、最大数を用意して表示／非表示を切り替える方式にする（動的生成しない）
- キー入力ごとの再コンパイル・再描画はしない（ボタンや確定操作のときだけ）
