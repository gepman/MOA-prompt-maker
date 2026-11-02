# MOA-prompt-maker（MOSS/Anima プロンプトビルダー）

過去に作った画像生成プロンプトについて、**キャラクター・服装・背景・行為を入れ替えて再利用**するためのツールです。
キャラや背景を変えると、MOSS 用の JSON と Anima 用のタグ列が両方とも自動で作り直され、
ComfyUI に送って MOSS → Anima の2段で画像まで生成できます。

- データは人が読んで手で編集できる YAML ファイル群です
- 画面は「シーン組み立て画面」1つだけ（Python + Gradio）
- **現行の仕様は [`spec_v2/SPEC.md`](spec_v2/SPEC.md)（v2.0）** です。[`spec/`](spec/) は旧版（v1.5）で、参照用と移行テストの入力として残しています

## v2 の考え方

v1.x の「Situation」を **Background（背景）** と **Action（行為）** に分けました。
Scene が、Background・Action・キャスト（キャラと服装）を選んで組み合わせます。

| エンティティ | 意味 | ルート |
|---|---|---|
| Character | キャラクター（1キャラ＝1形態につき1ファイル） | `characters/` |
| Outfit | 服装（キャラとは独立） | `outfits/` |
| Background | 背景。解像度、背景の文章、情景オブジェクト（bbox）、情景タグ | `backgrounds/` |
| Action | 行為。スロット、行為の文章、キャラのアイテム（bbox）、Anima テンプレート | `actions/` |
| Scene | Background ＋ Action ＋ キャスト割当。コンパイル結果を保持する | `scenes/` |

品質タグ・rating タグ・人数タグ（`1girl, solo` など）・`summary.meta` は、コンパイラが組み立てます。ファイルには書きません。

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
| `--data` | `data` | データフォルダ（上の5つのルートを含む） |
| `--config` | `config.yaml` | ComfyUI 連携の設定ファイル |
| `--comfy-url` | （設定ファイルの値） | ComfyUI の URL。設定ファイルより優先 |
| `--host` / `--port` | `127.0.0.1` / `7860`（`config.yaml` の `builder.port`） | 待ち受けるアドレスとポート（他端末から開くときは `--host 0.0.0.0`） |

プロンプト欄（MOSS JSON・Anima・ネガティブ）と差分欄の文字サイズは、`config.yaml` の `builder.promptFontSize`（px、既定 16）で変えられます。
エディタは `editor.promptFontSize` があればそちらを優先し、なければ `builder.promptFontSize` を使います（8〜48 の数値。範囲外は無効）。
設定を変えたら、アプリを再起動してください。

まず試すだけなら、同梱のサンプルデータで起動できます。
**保存すると YAML が書き換わる**ので、サンプルを触りたくないときはコピーしたフォルダを指定してください。

```powershell
.\.venv\Scripts\python app.py --data spec_v2/samples/data
```

## 使い方

1. 左の一覧から **Scene を開く**か、**Action を選んで**新規に作ります（右の「📂 Scene を開く」「🎬 Action を選ぶ」）。
   一覧はフォルダを1階層ずつ移動できます。フォルダ名は `_folder.yaml` の表示名です。
   「YAMLファイルを指定」で、ファイルを直接選ぶこともできます（YAML の `id` から `data/` 内の元ファイルを特定します）。
2. Action を選ぶと、その **推奨背景** が Background の初期選択になります。推奨背景がなければ未選択なので、
   「🏞 Background を選ぶ」で選びます。Background はいつでも選び直せて、**自分で選んだ背景が常に優先**です
   （Action を選び直しても変わりません）。
3. Action の `slots` の数だけ、キャスト枠が出ます。枠の「キャラを選ぶ」「服装を選ぶ」を押すと、左の一覧がその選択先に
   切り替わるので、そこから選びます。Action を選び直したときは、同名スロットのキャストと服装を引き継ぎます。
4. 服装は **未指定（継承）／none／個別の服装** から選びます。未指定のときは、
   Scene → Action → キャラの `defaultOutfit` の順で解決され、枠には出どころが表示されます
   （例：「未指定（アクション指定：guilty_suit_pink）」「未指定（キャラ既定：skyla_pilot）」）。
5. 下の欄に、MOSS JSON・Anima プロンプト・ネガティブ、実効 rating と人数タグが表示されます（コピーボタン付き）。
   bbox のはみ出しや、Action に書かれた人数タグの除去などの警告も一覧に出ます。
   ネガティブの適用は既定で OFF で、OFF でも合成結果は参考表示されます。
6. 「🎨 生成」で ComfyUI に送信します（下の「ComfyUI 連携」）。
7. 一番下の **保存** で Scene を保存します。

ビルダーは Background / Action を編集しません。直すときは YAML を直接編集して、「🔄 データ再読み込み」を押してください。

### クイック編集（プロンプトを直して試し打ち）

表示中のプロンプト（MOSS JSON・Anima・ネガティブ）は、そのまま書き換えられます。向きや汗など、一語〜一文だけ直して試し打ちしたいときに、エディタを開かずに済みます。
仕様は [`spec_v2/SPEC.md`](spec_v2/SPEC.md) の 10.1 章。

- **編集は保存されません**。Scene の YAML にも `compiled` にも書かれません。生成には、表示中の文字列がそのまま送られます。
- 編集した文字列で生成した結果は、保存しても**シードと画像が記録されません**（試し打ち）。
- 編集した欄には「編集済み」と［コンパイル結果に戻す］が出ます。MOSS の JSON が壊れていると、生成を止めてエラーを出します。
- ネガティブは、「ネガティブを適用する」が ON のときだけ、編集した内容を送ります（OFF のときは「適用OFFのため送信されません」と出ます）。
- Anima 段だけの再試行は、**最初の生成で送った文字列**を使います（その後に欄を直しても、再試行には使いません）。
- キャスト・服装・Background・Action を変えて再コンパイルされると、編集はリセットされます。確認は出ませんが、リセット前に、
  破棄される編集の差分が「破棄された編集の差分」として差分欄に残ります。

編集した欄のすぐ下に、**変更箇所が色付きで**出ます（追加は緑の背景、削除はグレーの取り消し線）。
表示が更新されるのは、欄からフォーカスを外したときと［差分を更新］を押したときだけです（キー入力ごとには更新しません）。

- 変更箇所（または行末の［移動］）をクリックすると、**該当の編集欄にフォーカスし、変更箇所の最後尾にカーソルを置き**、画面の真ん中に来るようスクロールします。
- 下の「クイック編集の差分」欄に、コピーできる固定形式のテキストが出ます。各行に**出どころ**（`Action beach_chat の mossItems[1].caption`、
  `Character elesa_bw の anima（{{center}}）` など）が付くので、エディタで直すときの手がかりになります。
  コンパイラが付けた部分（品質タグ、rating、人数タグ、solo、meta など）には「エディタでは直せない。rating／extras／sex で変える」と注記が付きます。
- 差分はエディタへ自動では反映されません。出どころを見て、エディタで手で直します。

### Scene の保存ルール

保存ボタンは誤操作を避けるため、画面の一番下にあります。

- **id が空**：`<名前>_1`、`<名前>_2` … と番号を付けて**新規保存**します。名前は、開いている Scene があればその id（末尾の `_数字` は除く）、
  なければ `<Action の id>__scene` です。既存の Scene は上書きしません。
- **既存と同じ id を入力**：そのときだけ**上書き**します（その Scene が今ある場所に保存します）。
- 新規保存の保存先は、「📁 保存先フォルダを選ぶ」で `scenes/` 配下から選びます（左の一覧でフォルダを開き、「📍 このフォルダを保存先にする」）。
  既定は `scenes/` 直下です。保存したら、保存先のフルパスを表示します。
- 検証エラーがあるときは保存しません。
- 生成した画像のパスとシードは、**保存したときに** `compiled` に記録されます。
  生成時と保存時で食い違う項目があると記録されず、**食い違った項目名**が表示されます（下の「ComfyUI 連携」）。

## データの置き方

実データは `data/` に置きます（`spec_v2/samples/` はテスト用のサンプルです）。

```
data/
  characters/   キャラクター（任意の深さのフォルダ可）
  outfits/      服装
  backgrounds/  背景
  actions/      行為
  scenes/       Scene
```

- フォルダの階層は整理のための棚です。参照はすべて `id` で行うので、エクスプローラーでフォルダを移動・改名しても壊れません。
- ファイル名は `<id>.yaml`。`id` は**同じルートの中で**一意です（ルートが違えば同じ `id` でもかまいません）。
- フォルダには `_folder.yaml` を置けます（表示名 `name`、並び順 `order`）。なくても動きます。
- 並び順は、ファイル名の自然順（数字を数として読む）です。順番を付けたいときは `01_` のような番号をファイル名に付けます。
- 名前が `_` または `.` で始まる**フォルダ**は読み込みません（下書き置き場に使えます）。
- YAML の形式は `spec_v2/schemas/*.schema.json` が正本です。サンプルは `spec_v2/samples/data/` を参照してください。

### プレイスホルダー

Action の `mossCaption`、`mossItems[].caption`、`anima` の中で使えます（**Background では使えません**）。

| 書式 | 意味 |
|---|---|
| `{{left}}` | `left` スロットのキャラクター |
| `{{left.outfit}}` | `left` スロットに解決された服装 |
| `{{left.bodyHair}}` | `left` スロットのキャラクターの体毛（`bodyHairMoss` / `bodyHairAnima`。無ければ空） |

MOSS 用か Anima 用かは書きません。MOSS 側では MOSS 用の文章に、Anima 側では Anima 用のタグに自動で置き換わります。
体毛や服装が無いときは空文字になり、エラーにはなりません（MOSS は空白、Anima は空タグ・連続カンマを整えます）。
`bodyHairMoss` と服装の `moss` は、主語を含む**完全な文**で書きます（例：`She has sparse thin brown pubic hair.`）。
`{{left.bodyHair}}` は単独の文の位置に置くと、あれば文として溶け込み、なければ文ごと消えます。

### 人数タグと solo

キャストの `sex`（`female` / `male`、省略時 `female`）と Action の `extras`（キャスト以外に描く人物の人数）から、
コンパイラが `1girl, solo` や `2girls, 1boy` を付けます。Action や Background の anima に書かれた人数タグ・`solo` は、
コンパイル時に除去して警告します（キャラ・服装のブロック内は除去しません）。
キャスト以外の特定の人物（例：説明を聞いている男の子）は、Action に `extras: { male: 1 }` のように書くと、人数タグに数えられます。

## エディタ（マスターデータの編集）

Character / Outfit / Background / Action を画面で編集するアプリです（Scene はビルダーで編集します）。仕様は [`spec_v2/EDITOR_SPEC.md`](spec_v2/EDITOR_SPEC.md)。

```powershell
.\.venv\Scripts\python editor.py --data data
```

<http://127.0.0.1:7863> を開きます。ポートは `--port`、なければ `config.yaml` の `editor.port`（ビルダーは `builder.port`）。
ビルダーと同時に起動してよく、同じ `--data` を指してください。

- **編集できるのは ID 以外の項目と新規追加だけ**です。ID の変更・削除・ファイル移動はしません。
  新規作成・複製（［複製して新規作成］）では ID を作成時に入力します。
- コメントとキー順は残したまま、変更した項目だけを書き戻します。保存前に、スキーマと意味チェックを行います（エラーがあれば保存しません。警告だけなら保存できます）。
- **使用中の Background**（Scene が参照している背景）は、上書きせず**新しい ID の複製として保存**します。
  **使用中の Action** はその場で上書きしますが、参照している Scene を確認欄に出します。参照中の Action では、スロットの削除・名前変更はできません（追加は可能）。
- 右のプレビューは、**未保存の内容**でコンパイル結果を見るものです（保存ファイルには触りません）。
- ファイルを VS Code などで外部から変更していたら、保存を止めて［再読み込み（編集を破棄）］［上書き保存］を選びます。
- フォルダは［＋フォルダ作成］で作れます（フォルダの表示名・並び順 `_folder.yaml` も編集できます）。
  エクスプローラー等でファイルやフォルダを移動・改名したときは、参照は id で行うので壊れませんが、**エディタとビルダーの［再読み込み］で反映**してください。
- ビルダーでは、Scene を開いたとき、保存済みのプロンプトと今のコンパイル結果が違えば「保存時から内容が変わっています」と表示します。
- **変更点の色付き表示**：長い文章・タグの欄（moss、anima、mossCaption、カードの caption など）は、編集したときだけ、欄のすぐ下に差分を出します
  （anima 系はタグ単位、ほかは単語単位。追加は緑、削除はグレーの取り消し線）。カードでは label・bbox の変化も出ます。
  保存ボタンの上の **「保存前の変更点」** に、変更した全項目を一覧します（短い項目は 旧→新、tags・slots などは要素単位、カードの追加・削除・並べ替えも）。
  使用中の Background の複製保存と、使用中の Action の確認欄にも、同じ変更点を併記します。
  差分の基準は、開いたとき（新規は初期値、複製は複製元の内容、保存後は保存した内容）です。
  更新はフォーカスを外したとき・カード操作・［差分を更新］のときだけです。
- **クリックで移動**：差分の変更箇所（または［移動］）をクリックすると、該当の編集欄にフォーカスし、変更箇所の最後尾にカーソルを置いて、画面の真ん中にスクロールします
  （ビルダーと同じ動き。「保存前の変更点」の行からも移動できます）。仕様は EDITOR_SPEC の 14 章。

## ComfyUI 連携

`config.yaml` で設定します。

```yaml
comfyui:
  url: http://127.0.0.1:8188
  workflow: workflow/moss_to_anima_i2i_api_v1.1.json   # API 形式で保存したワークフロー
  timeoutSec: 600      # 段ごとの待ち時間の上限
  outputDir: null      # ComfyUI の output フォルダ。指定すると記録するパスが絶対パスになる
nodes:                 # 値を差し込むノード（node は API 形式 JSON のキー）
  mossPositive: {node: "4", input: text}
  animaPositive: {node: "13", input: text}
  seed: [{node: "7", input: seed}, {node: "16", input: seed}]
  samplers:            # 任意。Scene の gen で上書きできるサンプラー
    moss:  {node: "7"}     # steps / cfg
    anima: {node: "16"}    # steps / cfg / denoise
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
- **画像のサイズ**は、Scene の Background の `canvas` が入ります（16の倍数でなければ警告）。
- **シード**：生成ごとに `0〜4294967295` のランダムな値を1つ決め、`nodes.seed` のすべて（MOSS 段と Anima 段）に同じ値を入れます。
  ワークフロー側の seed 入力にもっと小さい上限があれば、小さい方を上限にします。範囲外なら生成を始めません。
- **ネガティブ**：適用 ON のときだけ、合成したネガティブでノードを置き換えます。OFF のときはワークフローに書かれた既定をそのまま使います。
- **生成パラメータ**（`gen`）：MOSS 段の steps / cfg、Anima 段の steps / cfg / denoise を、項目ごとのスライダーで変えられます。
  各項目に「出どころ」（Scene / ワークフロー）が表示され、**動かした項目だけ**が Scene の `gen` に保存されます。
  「↩ ワークフローの値に戻す」で、その項目を `gen` から外せます。
- **解像度倍率**（Anima 段の列）：0.1〜2.0 倍（既定 1.0）。canvas の縦横比を保ったまま Anima 段の解像度を変えます。
  幅・高さは最も近い16の倍数に丸めて引き伸ばすので、縦横比が1%弱ずれることがあります（下限64px、512px 未満は警告）。
  **画面だけの設定**で、Scene（`gen`）にも `compiled` にも保存しません。1.0 以外のときだけ、送信時に MOSS → Anima の接続へ
  `ImageScale` ノードを挟みます（ワークフロー JSON と `config.yaml` は変えません。`nodes.width` / `nodes.height` が必要）。
  「Anima段だけ再試行」では、そのときの倍率が使われます。
- **生成の流れ**：通常は MOSS → Anima を1つのプロンプトで送ります。段ごとに `timeoutSec` の制限があり、超えたらその段の失敗として中断します
  （他のジョブのキュー待ちの時間は数えません）。
  - **MOSS 段の失敗**：画像は記録されません。シードは破棄し、再生成では新しいシードを使います。
  - **Anima 段の失敗（MOSS 段は成功）**：MOSS 画像を残して表示し、Anima 枠は「失敗」になります。
    「🔁 Anima段だけ再試行」で、MOSS 画像を入力にして Anima 段だけを、**同じシード・同じ `gen`** で再送できます
    （MOSS 段は再実行しません。MOSS 画像を一度 PNG（8bit）で読み直すため、通常の生成とは画がわずかに違うことがあります）。
    再試行は、最初の生成から内容が変わっていないときだけできます。
- **画像の記録先**：既定では ComfyUI 側のパス（`output/...`）を記録します。
  「📁 フォルダを選ぶ」でコピー先を指定すると、そこへ画像を保存し、コピー先のパスを記録します。
  `compiled.images` の並びは **MOSS 段 → Anima 段** です。
- **記録の条件**：保存時に、`mossPrompt`・`animaPrompt`・ネガティブ適用（ON なら合成ネガティブも）・Background / Action の id・`canvas`・
  `gen` の実効値が、生成時とすべて一致していれば `compiled.seed` / `images` / `gen` に記録されます。
  1つでも違うと記録されず、違った項目名が表示されます。MOSS 段だけの結果は `compiled.partial: true` で記録できます。

## v1.x のデータの移行

v1.x（Situation 形式）のデータは、移行ツールで v2 に変換できます。**元のフォルダは一切変更せず**、結果は新しいフォルダに書きます
（`--out` が存在して空でなければ中止します）。

```powershell
.\.venv\Scripts\python -m moa.migrate_v2 --src <v1.x の data フォルダ> --out <新しいフォルダ>
```

- `characters/` と `outfits/` はそのままコピーします（`sex` は書き足しません＝ female 扱い）。
- Situation 1つが、Background 1つ（id は `<id>_bg`）と Action 1つ（id は元の id）に機械的に分割されます。
  Scene は `scenes/` に移り、`cast` と `outfits` が1つにまとまります。
- Scene の `compiled.seed` が v2 の範囲（0〜4294967295）の外のとき（v1.x のアプリは 2^53 までの値を書いていました）は、
  別の値には直せない（その値では画像を再現できない）ので、`null` にして、元の値を YAML のコメントとレポートに残します。画像のパスは保持します。
- **`<新しいフォルダ>/MIGRATION_REPORT.md`** に、手作業が必要なものと、移行前後のコンパイル結果の差分が出ます。
  特に次を確認してください。
  - Action の anima に残った人数タグ・`solo`（v2 ではコンパイル時に除去されます）。**特定のモブを表すもの（例 `1boy`）は、`extras` を手で設定する必要があります**。
  - `3children` のような人数を表していそうなタグ。キャスト1人の Action では、`extras.others` を設定しないと誤って `solo` が付きます。
  - v1.x で人数タグを書いていなかった Action には、v2 で `1girl` などが新しく付きます。
  - summary.caption の背景／行為への書き分けは、機械移行では行いません（全文が Action に入ります）。任意の作業で、しなくても動きます。
- 移行後のデータは、そのままアプリで使えます。

  ```powershell
  .\.venv\Scripts\python app.py --data <新しいフォルダ>
  ```

## テスト

```powershell
.\.venv\Scripts\python -m pytest -q
```

- 受け入れテスト（SPEC v2 の11章）は `tests/test_acceptance.py` で、`spec_v2/samples/examples/` の出力と一致することを確認します。
  移行（11章の15）は `tests/test_migrate.py` で、旧サンプル（`spec/samples/data`）を移行して、v1.5 の出力と完全一致することを確認します。
- エディタの受け入れテスト（EDITOR_SPEC の13章）は `tests/test_editor_acceptance.py`、ビルダー側の変更（12章）は `tests/test_ui_logic.py`、
  画面の起動テストは `tests/test_editor_build.py` です。テストはサンプルを一時フォルダにコピーして使うので、実データには触りません。
- クイック編集（SPEC 10.1 章）は `tests/test_quickedit.py`（送信・記録しないこと・差分・リセット・再試行）、`tests/test_sourcemap.py`（出どころ）、
  色付き差分と文字サイズは `tests/test_diffhtml.py`、クリックで移動は `tests/test_jump.py`。
  エディタ側（EDITOR_SPEC 14 章）は `tests/test_editor_diff.py`（基準・差分・文字サイズ）と `tests/test_editor_jump.py`（移動）です。
- ComfyUI 連携のテストは、実物ではなく、テスト内で立てる偽のサーバーとスクリプト化したイベントを相手にします。ComfyUI を起動していなくても通ります。

## 構成

```
app.py              ビルダーの起動スクリプト
editor.py           エディタの起動スクリプト
config.yaml         ComfyUI 連携の設定
data/               実データ（characters / outfits / backgrounds / actions / scenes）
workflow/           ComfyUI の API 形式ワークフロー
moa/
  core/             コンパイル処理・出どころの記録・差分の共通部品（Gradio に依存しない）
  comfy/            ComfyUI 連携（設定・クライアント・生成・ノード割り当ての推定）
  migrate_v2/       v1.x からの移行ツール
  ui/               ビルダーの Gradio 画面（クイック編集、色付き差分の HTML、CSS）
  editor/           エディタ（logic/ に処理、ui/ に画面）
spec_v2/            現行の仕様書（v2.0）・JSON Schema・サンプル
spec/               旧版（v1.5）の仕様書・サンプル（移行テストの入力として使用）
tests/              pytest
```
