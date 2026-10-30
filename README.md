# MOA-prompt-maker（MOSS/Anima プロンプトビルダー）

過去に作った画像生成プロンプトの**キャラクターと服装だけを差し替えて再利用**するためのツールです。
キャラを変えると、MOSS 用の JSON と Anima 用のタグ列が両方とも自動で作り直され、
ComfyUI に送って MOSS → Anima の2段で画像まで生成できます。

- データは人が読んで手で編集できる YAML ファイル群です
- 画面は「シーン組み立て画面」1つだけ（Python + Gradio）
- 仕様の詳細は [`spec/SPEC.md`](spec/SPEC.md)（v1.2）にあります

## セットアップ

Python 3.11 で動作確認しています。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## 起動

```powershell
.\.venv\Scripts\python app.py
```

ブラウザで <http://127.0.0.1:7860> を開きます。

| オプション | 既定値 | 内容 |
|---|---|---|
| `--data` | `data` | データフォルダ（`characters/` `outfits/` `situations/` を含む） |
| `--config` | `config.yaml` | ComfyUI 連携の設定ファイル |
| `--comfy-url` | （設定ファイルの値） | ComfyUI の URL。設定ファイルより優先 |
| `--host` / `--port` | `127.0.0.1` / `7860` | 待ち受けるアドレスとポート |

まず試すだけなら、同梱のサンプルデータで起動できます。
**保存すると YAML が書き換わる**ので、サンプルを触りたくないときはコピーしたフォルダを指定してください。

```powershell
.\.venv\Scripts\python app.py --data spec/samples/data
```

## 使い方

1. 左の一覧から **Situation**（場面のテンプレート）を選びます。既存の **Scene** を選ぶと、その内容を開きます。
   フォルダは `_folder.yaml` の表示名で表示され、クリックで1階層ずつ移動できます。
   「YAMLファイルを指定」で、ファイルを直接選ぶこともできます（YAML の `id` から `data/` 内の元ファイルを特定します）。
2. Situation の `slots` の数だけ、右にキャスト枠が出ます。枠の「キャラを選ぶ」「服装を選ぶ」を押すと、
   左の一覧がその選択先に切り替わるので、そこから選びます。
3. 服装は **未指定（継承）／none／個別の服装** から選びます。
   未指定のときは、Scene → Situation → キャラの `defaultOutfit` の順で解決されます。
   枠には、実際に使われる服装の出どころが表示されます。
4. 下の欄に、MOSS JSON・Anima プロンプト・ネガティブが表示されます（コピーボタン付き）。
   ネガティブの適用は既定で OFF で、OFF でも合成結果は参考表示されます。
5. 「🎨 生成」で ComfyUI に送信します。完了すると、MOSS 段と Anima 段の画像が表示されます。
   生成ボタンの上のスライダーで、**Anima i2i の denoise**（MOSS 画像をどれだけ描き直すか。0.0〜1.0）を変えられます。
   初期値はワークフローの値で、Scene を保存するとその値も保存され、次に開いたときに使われます。
6. 一番下の **保存** で Scene を保存します。

### Scene の保存ルール

保存ボタンは誤操作を避けるため、画面の一番下にあります。

- **Scene 名が空**：`<名前>_1`、`<名前>_2` … と番号を付けて**新規保存**します。既存の Scene は上書きしません。
- **既存と同じ Scene 名を入力**：そのときだけ**上書き**します。
- 保存先は、Situation と同じフォルダの `<situation id>__<Scene名>.yaml` です。
- 生成した画像のパスとシードは、**保存したときに** `compiled.images` と `compiled.seed` に記録されます。
  生成後にキャストやスライダーを変えた場合は、内容が食い違うため記録されません。
  生成時の denoise は `compiled.denoise` に記録されます。

## データの置き方

実データは `data/` に置きます（`spec/samples/` はテスト用のサンプルです）。

```
data/
  characters/   キャラクター（任意の深さのフォルダ可）
  outfits/      服装
  situations/   Situation と Scene
```

- フォルダの階層は整理のための棚です。参照はすべて `id` で行うので、フォルダを移動・改名しても壊れません。
- ファイル名は `id` と一致させます（`skyla_bw.yaml` なら `id: skyla_bw`）。
- フォルダには `_folder.yaml` を置けます（表示名 `name`、並び順 `order`）。なくても動きます。
- 並び順は、ファイル名の自然順（数字を数として読む）です。順番を付けたいときは `01_` のような番号をファイル名に付けます。
- `situations/` の中では、`situation` と `cast` の両方のキーを持つファイルが Scene、持たないものが Situation です。
- YAML の形式は `spec/schemas/*.schema.json` が正本です。サンプルは `spec/samples/data/` を参照してください。
- YAML を手で編集したら、画面の「🔄 データ再読み込み」を押します。検証のエラーと警告は画面に表示されます。

Situation の `mossPrompt` と `animaPrompt` には、次のプレイスホルダーを書きます。

| 書式 | 意味 |
|---|---|
| `{{left}}` | `left` スロットのキャラクター |
| `{{left.outfit}}` | `left` スロットに解決された服装 |

MOSS 用か Anima 用かは書きません。`mossPrompt` の中では MOSS 用の文章に、`animaPrompt` の中では Anima 用のタグに自動で置き換わります。

## ComfyUI 連携

`config.yaml` で設定します。

```yaml
comfyui:
  url: http://127.0.0.1:8188
  workflow: workflow/moss_to_anima_i2i_api_v1.1.json   # API 形式で保存したワークフロー
  timeoutSec: 600
  outputDir: null      # ComfyUI の output フォルダ。指定すると記録するパスが絶対パスになる
nodes:                 # 値を差し込むノード（node は API 形式 JSON のキー）
  mossPositive: {node: "4", input: text}
  animaPositive: {node: "13", input: text}
  seed: [{node: "7", input: seed}, {node: "16", input: seed}]
  denoise: {node: "16", input: denoise}   # 任意。指定するとスライダーが有効になる
  ...
outputs:               # 画像を取得する保存ノード
  moss: ["9"]
  anima: ["18"]
```

- **ワークフローは「API 形式で保存」したものを使います。**
- 別のワークフローを使うときは、ノード ID の候補を推定できます。出力を `config.yaml` に貼り付けてください。

  ```powershell
  .\.venv\Scripts\python -m moa.comfy.suggest workflow/<ワークフロー>.json
  ```

- 設定がワークフローと合っていない（ノードや入力名が存在しない等）と、画面にその理由が出て、生成ボタンは押せません。
- **シード**：生成ごとにランダムな値を1つ決め、`nodes.seed` のすべてに同じ値を入れます。
- **ネガティブ**：適用 ON のときだけ、合成したネガティブでノードを置き換えます。OFF のときはワークフローに書かれた既定をそのまま使います。
- **画像の記録先**：既定では ComfyUI 側のパス（`output/...`）を記録します。
  「📁 フォルダを選ぶ」でコピー先を指定すると、そこへ画像を保存し、コピー先のパスを記録します。
  `compiled.images` の並びは **MOSS 段 → Anima 段** です。

## テスト

```powershell
.\.venv\Scripts\python -m pytest -q
```

- 受け入れテスト（SPEC 11章）は `tests/test_acceptance.py` で、`spec/samples/examples/` の出力と一致することを確認します。
- ComfyUI 連携のテストは、実物ではなく、テスト内で立てる偽のサーバーを相手にします。ComfyUI を起動していなくても通ります。

## 構成

```
app.py              起動スクリプト
config.yaml         ComfyUI 連携の設定
data/               実データ（characters / outfits / situations）
workflow/           ComfyUI の API 形式ワークフロー
moa/
  core/             コンパイル処理（Gradio に依存しない）
  comfy/            ComfyUI 連携（設定・クライアント・生成・ノード割り当ての推定）
  ui/               Gradio の画面
spec/               仕様書・JSON Schema・サンプル
tests/              pytest
```
