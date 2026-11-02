# MOSS/Anima プロンプトビルダー 仕様書 v2.0

この文書は v1.5 までの仕様と Background／Action 分割案を統合した**単独で読める仕様書**である。
v1.x の文書を参照しなくても実装できるように書いている。v1.5 からの変更点は「変更履歴」と各章の **[v2]** 印で示す。

## 変更履歴

- v2.2（2026-11-01） プロンプト欄の文字サイズ（`builder.promptFontSize`）と、編集した欄の色付き差分表示（10.1章）
- v2.1（2026-11-01） ビルダーに「クイック編集（セッション限定）」を追加（10.1章）。コンパイラにソースマップ（出どころの記録）を追加（出力は不変）
- v2.0（2026-10-31）
  - **Situation を Background（背景）と Action（行為）に分割**した。エンティティは Character / Outfit / Background / Action / Scene の5種、
    ルートは `characters/` `outfits/` `backgrounds/` `actions/` `scenes/` の5つ（3章、4章、8章）
  - Scene は Background と Action を**両方とも選ぶ**。Action の `background` は推奨背景（初期値）にすぎない（4.5章）
  - Scene は新設の `scenes/` ルートに置く。ファイル名は Scene の ID（4.5章、8章）
  - **Scene の `cast` をスロットごとのネスト形式に変更**し、別マップ `outfits` を廃止した（4.5章）
  - 服装指定の三値（省略／`none`／id）を Scene・Action・Character の全レイヤーで統一。**`null` は不正**（v1.x では許されていた）（5.2章）
  - 品質タグ、rating タグ、人数タグ、`summary.meta`、MOSS JSON の固定値は**コンパイラが組み立てる**。ファイルには書かない（7章）
  - rating は Background と Action の両方が持ち、**厳しい方**を採用する（7.2章）
  - Character に `sex`（`female` / `male`、省略時 `female`）を追加。人数タグはキャストから自動生成し、1人なら `1girl, solo` のように必ず併記（7.4章）
  - Action に `extras`（キャスト以外に特定の人物として描く登場人物の人数。female / male / others）を追加（4.4章、7.4章）
  - テンプレート側に書かれた人数タグ・`solo` は**コンパイル時に除去して警告**（7.4章）
  - bbox のはみ出し判定式を定義（警告）。大小逆転はエラー（9.3章）
  - ComfyUI連携に、シードの値域、段ごとの失敗・タイムアウト、部分結果（`compiled.partial`）、gen 実効値を含む記録条件を追加（13章）
  - v1.x データの移行ツールを定義（14章）
- v1.5（2026-10-31） キャラクターの体毛 `bodyHairMoss` / `bodyHairAnima` と `{{slot.bodyHair}}` を追加
- v1.4（2026-10-30） Scene ごとの生成パラメータ `gen` と `compiled.gen` を追加
- v1.3（2026-10-30） Anima i2i の `denoise`（v1.4 で `gen` に統合）
- v1.2（2026-10-30） ComfyUI連携
- v1.1（2026-10-30） ネガティブの全体ベース、服装 moss の完全文化、`extraMoss` 廃止、UI章 ほか
- v1（2026-10-30） 初版

## 1. 目的

過去に作成した画像生成プロンプトについて、**キャラクターだけを入れ替えて再利用**できるようにする
プロンプトビルダー兼データベース。v2.0 では加えて、**背景と行為を別々に入れ替えて組み合わせられる**ようにする。

- 例：射命丸文のシーンを犬走椛に差し替える／同じ「談笑」の行為を、海辺から教室へ背景だけ差し替える
- アプリ上でキャラ・背景・行為を変更すると、**MOSS用プロンプトとAnima用プロンプトの両方**が自動で再生成される
- データベースは人間が読めて手で編集できること（YAMLファイル群）

## 2. スコープ

### やること

- 差し替え可能なのは **Background、Action、キャラクター、服装** の4つ
- テキスト断片は「MOSS用文章」と「Anima用タグ」をそれぞれブロック（文字列）として保持する
- ビルダーの画面は「シーン組み立て画面」1つ（10章）。マスターデータの編集は別アプリ（エディタ。EDITOR_SPEC.md、15章）か、YAMLの手編集で行う

### やらないこと（データ構造には将来の余地を残す）

- キャラの部位・項目単位でMOSS文章とAnimaタグを対応付ける変換
- キャラの「形態」を1レコード内の分類として扱うこと ※形態違いは**別キャラクターとして登録**する
- シーン単位の自由文の差し込み（旧 `extraMoss` / `extraAnima`）
- 背景と行為の相性の自動判定（旧案の `provides` / `requires`。12章）

## 3. 用語とエンティティ

| エンティティ | 意味 | ルート |
|---|---|---|
| Character | キャラクターのマスターデータ。1キャラ（1形態）につき1ファイル | `characters/` |
| Outfit | 服装データ。1服装につき1ファイル。キャラとは独立 | `outfits/` |
| Background **[v2]** | 背景。解像度、背景のキャプション、情景オブジェクト、情景タグを持つ。キャストは持たない | `backgrounds/` |
| Action **[v2]** | 行為。スロット、行為のキャプション、キャラのアイテム（bbox）、Animaテンプレートを持つ | `actions/` |
| Scene | 生成単位。Background＋Action＋キャスト割当を確定させたもの。コンパイル結果を保持する | `scenes/` **[v2]** |
| Cast | Scene内でスロットに割り当てられたキャラと服装。Sceneの `cast` がその実体で、独立ファイルは持たない | — |

- Background は再利用できる。複数の Action / Scene から同じ Background を使ってよい（多対多）
- Situation（v1.x）は廃止。v1.x のデータは14章のツールで移行する

## 4. データモデル

すべてのエンティティに共通：

- `id` は必須。`[a-z0-9_]+`。ファイル名は `<id>.yaml`。**同じルートの中で**一意（ルートが違えば同じ id でもよい）
- 複数行の文字列は YAML の `|` ブロックで書いてよい（前後の空白はコンパイル時に除去する。5.3章）
- スキーマは `schemas/*.schema.json`（JSON Schema 2020-12）

### 4.1 Character

```yaml
id: elesa_bw            # 必須
name: カミツレ（BW版）   # 必須。日本語の表示名
group: elesa            # 任意。同一人物の形態違いなどをUIでまとめるための緩い分類
tags: [ポケモン, BW]     # 任意。検索用キーワード
sex: female             # 任意 [v2]。female | male。省略時 female。人数タグの集計に使う（7.4章）
defaultOutfit: elesa_bw_summer  # 任意。Outfitのid、または none（5.2章）
moss: |                 # 必須。MOSS用の容姿文章
  （文章）
anima: |                # 必須。Anima用のタグ
  （タグ）
bodyHairMoss: |         # 任意。体毛（量＋色を含む完全な文）。{{slot.bodyHair}} の差し込み先（MOSS用）
  She has sparse thin brown pubic hair.
bodyHairAnima: |        # 任意。体毛のタグ（Anima用）
  sparse pubic hair, brown pubic hair
negativeMoss: |         # 任意。このキャラ固有のネガティブ（MOSS用）
negativeAnima: |        # 任意。このキャラ固有のネガティブ（Anima用）
```

記載ルール：

- **頭部アクセサリーはキャラ側に含める**（例：カミツレのヘッドホン）。頭部以外のアクセサリーは服装側
- Animaのキャラ名タグによって自動で発生してしまうアクセサリー等は**許容する**（排除のために戦わない）
- **`bodyHairMoss` は完全な文で書く**（主語を含む）。Action 側は `{{slot.bodyHair}}` を単独の文の位置に置くだけにする。
  体毛ありでは文として溶け込み、なしでは文ごと消える。場面固有の量にしたいときは Action 側に直書きする
- キャラ固有ネガティブの主な用途は「別バリアントの排除」（違う髪型・違う色など）と「身体特徴の保護」（肌色など）
- **人数タグ（`1girl` など）は書かない**。性別は `sex` で表す

### 4.2 Outfit

```yaml
id: guilty_pink         # 必須。none という id は不可
name: ギルティ衣装（ピンク） # 必須
tags: []                # 任意
moss: |                 # 必須。「She wears 〜.」まで含めた完全な文
anima: |                # 必須。服装のタグ
negativeMoss: |         # 任意
negativeAnima: |        # 任意
```

記載ルール：

- **moss は完全な文で書く**（主語は着用者に合わせて著者が書く）。Action 側は `{{slot.outfit}}` だけを置く。
  これにより、服装が `none`／空でも壊れた文が残らない
- 色違いの排除ネガティブ（例：ピンク枠なら他色のスーツを排除）は、キャラではなく**服装側**に持つ

### 4.3 Background [v2]

```yaml
id: beach               # 必須
name: 海辺              # 必須
tags: [海辺]            # 任意
rating: safe            # 任意。safe | sensitive | explicit。省略時 safe（7.2章）
canvas: { w: 1536, h: 1024 }   # 必須。生成解像度（ピクセル）
metaTags: [beach scene] # 任意。MOSS summary.meta に入れる語（7.3章）
mossCaption: |          # 必須（空文字可）。MOSS summary の背景部分。背景だけを完全な文で書く
  A sunny beach in the daytime, with the blue sea stretching to the horizon, gentle waves along the shoreline, wet sand and a clear sky with a few white clouds.
mossItems:              # 任意。情景オブジェクト（MOSS items）。省略時は空
  - label: object
    caption: The sea and the shoreline, with gentle waves, wet sand and the horizon under a clear blue sky.
    bbox: [-512, -768, -80, 768]   # [y_min, x_min, y_max, x_max]（9.3章）
anima: |                # 必須（空文字可）。情景タグ
  beach, ocean, sand, blue sky, outdoors, daytime
negativeMoss: |         # 任意
negativeAnima: |        # 任意
```

記載ルール：

- **プレイスホルダーは一切使えない**（mossCaption、mossItems、anima のどこにも）。使っていたらエラー
- mossCaption には**背景だけ**を書く：場所・時間・光・情景オブジェクト。キャストのキャラの名前・姿・行為は書かない。
  不特定のモブや群衆は背景として書いてよい（人数タグには数えない。7.4章）
- anima も同じ分担（情景タグのみ）。品質タグ・rating タグ・人数タグは書かない

### 4.4 Action [v2]

```yaml
id: beach_chat          # 必須
name: 海辺で談笑（3人） # 必須
tags: [日常, 3人]       # 任意
rating: safe            # 任意。省略時 safe（7.2章）
background: beach       # 任意。推奨背景のid。Scene 作成時の初期値に使うだけ（4.5章）
slots: [left, center, right]   # 必須。スロット名の宣言（1つ以上、一意、[a-z0-9_]+）
outfits:                # 任意。スロットごとの服装指定（Outfitのid または none。キー省略＝委譲。null は不可）
  left: guilty_pink
extras: { male: 1 }     # 任意。キャスト以外に描く人物の人数（female / male / others、0以上の整数）（7.4章）
metaTags: []            # 任意。MOSS summary.meta に追加する語（7.3章）
mossCaption: |          # 必須（空文字可）。MOSS summary の行為部分。プレイスホルダー可
  Three adult women stand in a row on the sand, chatting and laughing together in a friendly conversation.
mossItems:              # 必須。キャラ等のアイテム（MOSS items）。caption にプレイスホルダー可
  - label: object
    caption: "{{left}} {{left.outfit}} She laughs with her mouth open, one hand raised mid-gesture as she talks."
    bbox: [-380, -640, 320, -260]
anima: |                # 必須。Animaテンプレート。プレイスホルダー可
  {{left}}, {{center}}, {{right}}, {{left.outfit}}, {{center.outfit}}, {{right.outfit}}, standing, talking, laughing, smile, open mouth
negativeMoss: |         # 任意
negativeAnima: |        # 任意
```

記載ルール：

- mossCaption には**行為だけ**を書く：ポーズ・行為・表情・キャラ同士の関係。背景の描写はしない
  （"against the wall" のような最小限の言及は可）。完全な文（ピリオド終わり）で書く
- **特定の人物として描くモブ**（例：説明を聞いている男の子）は Action に書き、`extras` で人数に数える。
  そのモブ用のアイテム（bbox）も Action の mossItems に置いてよい
  - `female` / `male`：人数タグ（`1boy` など）に数える
  - `others`：性別のタグを付けない人物（例：`3children` と書きたい子どもたち）。人数タグは作らず、**solo の判定にだけ数える**。
    その人物を表すタグ（`3children`、`children` など）はテンプレートに自分で書く（7.4章の除去対象ではない）
- anima には**品質タグ・rating タグ・人数タグ・`solo` を書かない**（書いてあればコンパイル時に除去して警告。7.4章）
- bbox は Background の canvas を前提にピクセルで書く。キャンバスの縦横比が近い Background なら、そのまま使い回せる

### 4.5 Scene

```yaml
id: beach_chat__trio    # 必須。scenes/ 内で一意。命名の目安は <action id>__<バリアントやキャストを表す語>
name: 海辺で談笑（ポケモントリオ）  # 任意。表示名
background: beach       # 必須 [v2]。Backgroundのid
action: beach_chat      # 必須 [v2]。Actionのid
cast:                   # 必須 [v2]。スロット名 → { character, outfit? }
  left:
    character: skyla_bw
  center:
    character: elesa_bw
    outfit: none        # 省略＝委譲 / none＝着せない / id＝上書き（5.2章）
  right:
    character: hex_maniac_xy
    outfit: hex_summer
applyNegative: false    # 任意。既定 false（6章）
gen:                    # 任意。書いた項目だけワークフローの値を上書きする（13.5章）
  moss:  { steps: 50, cfg: 3.0 }
  anima: { steps: 14, cfg: 1.8, denoise: 0.45 }
compiled:               # アプリが書き込むスナップショット（7.6章、13.4章）
  background: beach     # コンパイル時に使った Background / Action の id
  action: beach_chat
  canvas: { w: 1536, h: 1024 }
  rating: safe          # 実効 rating
  mossPrompt: （最終文字列）
  animaPrompt: （最終文字列）
  negativeApplied: false
  gen: {}               # 生成時に実際に使った値（13.5章）
  seed: null            # 生成時のシード（13.2章）
  images: []            # 生成画像のパス（MOSS段 → Anima段の順）
  partial: true         # 任意。MOSS段だけの部分結果のとき true（13.4章）
```

- 置き場：`scenes/` 配下の任意のフォルダ。ファイル名 `<id>.yaml`
- `cast` のキーは Action の `slots` と過不足なく一致すること（9.2章）
- v1.x の `situation` キー、別マップの `outfits`、旧 `denoise` は v2.0 では**スキーマエラー**（14章のツールで移行する）

### 4.6 フォルダメタ `_folder.yaml`

各ルート配下のフォルダに任意で1つ置ける。なくても動作する。

```yaml
name: 海辺               # 任意。フォルダの日本語表示名
description: （任意）
order:                  # 任意。子要素の並び順（ファイル名／フォルダ名のリスト）
  - beach.yaml
```

## 5. プレイスホルダーと服装

### 5.1 プレイスホルダー

- **Action の mossCaption、mossItems[].caption、anima の中だけ**で使える。Background では使えない（エラー）
- 書式は3種のみ（接尾辞は大文字小文字を区別する。`{{ left }}` のような内側の空白は許す）：
  - `{{スロット名}}` … そのスロットのキャラクター（`moss` / `anima`）
  - `{{スロット名.outfit}}` … そのスロットに解決された服装（`moss` / `anima`）
  - `{{スロット名.bodyHair}}` … そのスロットのキャラの体毛（`bodyHairMoss` / `bodyHairAnima`）
- MOSS/Animaの区別はプレイスホルダーに書かない。mossCaption / mossItems では MOSS 側、anima では Anima 側のブロックに解決される
- 同一プレイスホルダーの複数回出現を許す（mossCaption と mossItems の両方に同じキャラを書くため）
- `{{` で始まり `}}` で終わるのに上の3種に当てはまらないもの（例 `{{left.foo}}`）はエラー

### 5.2 服装の解決規則

服装指定は、どのレイヤーでも**三値**：

| 書き方 | 意味 |
|---|---|
| キーを書かない | 未指定。下のレイヤーに委譲する |
| `none` | 明示的に着せない（服装テキストなし） |
| Outfitのid | その服装を使う |

- **`null` は不正**（スキーマエラー）[v2]。「委譲」はキーを書かないことで表す
- 優先順位：
  1. Scene の `cast.<slot>.outfit`
  2. Action の `outfits.<slot>`
  3. Character の `defaultOutfit`
  4. いずれもなければ服装テキストなし
- Background は服装を持たない
- 解決結果と「どのレイヤー由来か」（scene / action / character / なし）を CompileResult に含め、UIに表示する

### 5.3 空白と空要素の整形

- 各ブロック（キャラ／服装／体毛の moss・anima、Background・Action の mossCaption・anima）は、**前後の空白（改行含む）を除去してから**使う
- MOSS の文字列は、置換後に**連続する空白を1つにまとめ、前後を除去**する
- Anima は、カンマで分割 → 各要素の前後空白を除去し内部の連続空白を1つに → **空の要素を捨てて** `, ` で結合する
- これにより、服装が `none`、体毛なし（空）でも、壊れた文・空タグ・連続カンマが残らない（空の体毛はエラーにしない）
- **Animaポジティブの重複タグは除去しない**（`huge breasts` をキャラごとに繰り返して各キャラへ行き渡らせるため）

## 6. ネガティブプロンプト

- Character / Outfit / Background / Action がそれぞれ MOSS用・Anima用を持つ
- 最終ネガティブは**層の連結**で合成する：
  `全体ベース ＋ キャスト全員（スロット宣言順） ＋ 解決された服装（スロット順） ＋ Background ＋ Action` [v2]
- 全体ベース（アプリの定数）：
  - MOSS用：`photorealistic, realistic, blurry, low quality, worst quality, text, watermark, deformed hands, extra fingers, extra limbs, censored, mosaic`
  - Anima用：`worst quality, low quality, score_1, score_2, score_3, artist name, blurry, jpeg artifacts, chromatic aberration, censored, mosaic censoring`
- 重複除去：カンマで分割し、前後の空白を除いたうえで**大文字小文字を区別せずに**比較し、最初に出現したものを残す
- **適用はオプションで既定OFF**（`Scene.applyNegative`）。OFFでも合成済みネガティブはUIに無効状態で表示する
- 送信ペイロード：OFFなら空文字。ComfyUI連携ではOFFのときネガティブノードに触れない（13.3章）
- ON/OFFは `compiled.negativeApplied` に記録する

## 7. コンパイル

### 7.1 手順

1. Scene から Background と Action を引く。`cast` の各スロットに Character を解決する
2. 5.2章の規則で各スロットの服装を解決する
3. 検証（9章）。エラーがあればコンパイル結果を出さない（警告は出力と一緒に返す）
4. 実効 rating（7.2）、meta（7.3）、人数タグ（7.4）を求める
5. MOSS JSON を組み立てる（7.5）
6. Anima を組み立てる（7.5）
7. ネガティブを合成する（6章）
8. 保存時に結果一式を `Scene.compiled` にスナップショットとして書く（7.6）

### 7.2 rating

- Background と Action の `rating`（省略時 safe）のうち**厳しい方**を実効 rating とする。順序は `safe < sensitive < explicit`
- 実効 rating は MOSS の `summary.meta` と Anima の先頭（品質タグの直後）にコンパイラが入れる。ファイル側に rating タグを書かない

### 7.3 summary.meta

- ファイルに保存せず、コンパイラが組み立てる：
  `["anime illustration", 実効rating] ＋ Background.metaTags ＋ Action.metaTags`
- 重複は先勝ちで除去（大文字小文字を区別する完全一致）。順序はこの通り

### 7.4 人数タグと solo

- キャストの各キャラの `sex` を集計し、Action の `extras` を足す：
  `girls = female のキャスト数 + extras.female`、`boys = male のキャスト数 + extras.male`
- タグの書式：1人なら `1girl` / `1boy`、2〜5人なら `2girls` 〜 `5girls`（boys も同様）、6人以上なら `6+girls` / `6+boys`。
  0人の性別は出さない。並びは **girls → boys**
- **girls ＋ boys ＋ `extras.others` がちょうど1人なら `solo` を付ける**（`1girl, solo` / `1boy, solo` の形で必ず両方）。
  モブを `extras` に数えた場合は solo にならない
  （例：キャスト1人＋`extras: {male: 1}` → `1girl, 1boy`。キャスト1人＋`extras: {others: 3}` → `1girl`。`others` は人数タグを作らない）
- Background に書いた不特定のモブ・群衆は数えない
- `extras` が影響するのは **Anima の人数タグと solo だけ**。MOSS には影響しない（MOSS ではモブを mossCaption / mossItems の文章で描く）
- 言い換えると、**solo は「キャスト＋extras（female・male・others）の総人数がちょうど1」のときだけ付く**
- `3children` のような girl/boy 以外の人数表現は**除去の対象外**で、生成された人数タグと併存してよい（除去パターンを広げて「直す」ことはしない）
- **テンプレート側の人数タグの除去**：Action の anima と Background の anima について、**プレイスホルダー展開前**に、
  カンマ区切りの各要素のうち、`^(\d+\+?)(girl|girls|boy|boys)$`（大文字小文字を区別しない）に一致するものと、
  単独の `solo` を除去し、要素ごとに警告を出す。キャラ・服装・体毛ブロックの中身は対象外。ファイル自体は書き換えない

### 7.5 組み立て

**MOSS**（キー順もこの通り。`indent=2`、`ensure_ascii=False` の JSON 文字列）：

```json
{
  "summary": {
    "meta": [ 7.3章 ],
    "canvas": "single",
    "caption": "Background.mossCaption ＋ 半角スペース1つ ＋ Action.mossCaption（プレイスホルダー展開後）"
  },
  "items": [ Background.mossItems …, Action.mossItems …（それぞれ label, caption, bbox の順）],
  "bbox_coord_space": "abs_centralized",
  "bbox_format": "yxyx"
}
```

- `"single"`、`"abs_centralized"`、`"yxyx"` は**コンパイラだけが持つ固定値**で、どのスキーマにも含めない
- caption は5.3章の空白整形をする。どちらかの mossCaption が空なら、もう一方だけになる（余分な空白は残らない）
- bbox は書かれた数値をそのまま出す（換算しない）

**Anima**（この順に連結してから5.3章の整形）：

1. 品質タグ（固定）：`masterpiece, best quality, score_7`
2. 実効 rating：`safe` / `sensitive` / `explicit`
3. 人数タグ（7.4章）
4. Action の anima（人数タグ除去 → プレイスホルダー展開）
5. Background の anima（人数タグ除去）

### 7.6 スナップショット

- 保存時に `compiled` へ、使った `background` / `action` の id、`canvas`、実効 `rating`、`mossPrompt`、`animaPrompt`、`negativeApplied` を書く
- マスターデータを後で編集しても、保存済み Scene は当時の内容を再現できること

## 8. ファイル配置

```
data/
  characters/     # 任意の深さのフォルダ可
  outfits/
  backgrounds/    # [v2]
  actions/        # [v2]
  scenes/         # [v2]
```

- フォルダ階層は整理のための棚であり、**意味はフォルダ位置ではなくファイル内容（id）で決める**
- 参照はすべて id で行う。**エクスプローラー等の外部ツールでファイルやフォルダを移動・改名しても参照は壊れない**
- アプリは起動時に各ルートを再帰スキャンし、ルートごとに id→パスの索引を作る
- 並び順はファイル名の自然順（数字を数として読む）。`_folder.yaml` の `order` があればそれを優先
- 名前が `_` または `.` で始まるフォルダはスキャンしない（`_folder.yaml` は除く）

## 9. 検証

### 9.1 スキーマ検証

- ルートでスキーマを決める：`characters/`→Character、`outfits/`→Outfit、`backgrounds/`→Background、`actions/`→Action、`scenes/`→Scene、`_folder.yaml`→Folder
- スキーマ違反のファイルはエラーを出して索引から外し、読み込みは続ける
- YAMLの読み書きは **ruamel.yaml**（YAML 1.2。`none` は文字列）。書き戻しはコメントとキー順を保持し、一時ファイル→置き換えで書く

### 9.2 意味チェック

| 内容 | 扱い |
|---|---|
| id の重複（同じルート内）、id とファイル名の不一致 | エラー |
| 参照先が存在しない（defaultOutfit、Action.outfits、Action.background、Scene の background / action / character / outfit） | エラー（Action.background は警告） |
| Background にプレイスホルダーがある | エラー |
| 書式が不正なプレイスホルダー、宣言されていないスロットの使用 | エラー |
| `cast` に割り当てのないスロット、`slots` にない `cast` のキー、Action.outfits の未宣言キー | エラー |
| 宣言されたが mossCaption / mossItems / anima のどこにも使われていないスロット | 警告 |
| テンプレートの人数タグ・solo（7.4章） | 警告（除去してコンパイル） |
| bbox のはみ出し（9.3章） | 警告 |
| bbox の大小逆転 | エラー |

### 9.3 bbox のはみ出し判定

- 座標はキャンバスの**ピクセル単位の絶対座標**。原点はキャンバス中心、x は右が＋、y は下が＋。bbox は `[y_min, x_min, y_max, x_max]`
- キャンバス幅 W・高さ H（Background の canvas）のとき、有効範囲は x ∈ [−W/2, +W/2]、y ∈ [−H/2, +H/2]
- `x_min < −W/2`、`x_max > +W/2`、`y_min < −H/2`、`y_max > +H/2` のいずれかが真ならその item は**はみ出し（警告）**。
  警告文には item の位置（Background / Action の何番目か）、label、どの辺から何px はみ出したか（例：左 = `−W/2 − x_min`）を含める
- キャンバスとまったく重ならない item も警告どまり（文言で「完全にはみ出し」と区別する）
- `y_min > y_max` または `x_min > x_max` はエラー

## 10. 画面（ビルダー）

ビルダー（`app.py`、既定ポート 7860）は「シーン組み立て画面」1つ。Python＋Gradio。

- **選択方法はプルダウンにしない**。共有ブラウザ（パンくず＋一覧で1階層ずつ降りる方式）と、ファイル選択ダイアログで選ぶ。
  ダイアログで選んだファイルは、内容の id から索引で元ファイルを特定する
- 選択中の項目は表示名（`name`）とパスで表示する

操作の流れ：

1. **Scene を開く**か、**Action を選んで新規作成**する
2. Action を選ぶと、Action の `background`（推奨背景）が Background の**初期選択**になる。推奨背景がなければ Background は未選択
3. **Background を選ぶ**（いつでも選び直せる。Scene 側の選択が常に優先）
4. Action の `slots` の数だけ**キャスト枠を動的に作る**。各枠：
   - スロット名
   - キャラ（name＋パス）と「キャラを選ぶ」ボタン
   - 服装：「未指定（継承）」「none」「選択中の Outfit」と「服装を選ぶ」ボタン。
     実際に使われる服装と出どころを表示（例：「未指定（アクション指定：guilty_pink）」「未指定（キャラ既定：elesa_bw_summer）」）
5. コンパイル結果を表示（コピーボタン付き）：MOSS JSON、Anima、MOSSネガ、Animaネガ。人数タグと実効 rating も確認できること
6. 検証のエラー・警告の一覧（bbox はみ出し、人数タグ除去を含む）。**ビルダーは Background / Action を編集しない**。直すのはエディタの仕事
7. 「ネガティブ適用」チェック（既定OFF）、生成パラメータ（13.5章）、生成（13章）
8. 保存：画面の一番下の「シーンを保存」ブロックで行う（10.2章）。共通ピッカーでは保存先を選ばない
9. 「再読み込み」ボタン（YAMLを外部で編集した後に使う）

- 状態が変わるたび（Background / Action / キャスト / 服装の変更）に自動で再コンパイルする
- 起動オプション：`--data <フォルダ>`、`--port`、`--host`（Tailscale 等で他端末から開くとき `0.0.0.0`）
- プロンプト欄（MOSS JSON、Anima、ネガティブの MOSS／Anima）と差分欄の文字サイズは、`config.yaml` の `builder.promptFontSize`（px。既定 16。8〜48 の数値で、外れたときは既定）で変えられる（10.1章）

### 10.1 クイック編集（セッション限定）

**目的**：ビルダーに表示されるコンパイル済みのプロンプトを、向きや汗など一語〜一文だけ手早く直して試し打ちする。エディタを開かずに済ませる。

**クイック編集**

- 表示中のプロンプト（MOSS JSON、Anima、合成済みネガティブの MOSS／Anima）を編集できる。生成時は、表示中の文字列をワークフローに送る（送信時に差し替える）
- 保存は一切しない。Scene の YAML も `compiled` も変更しない
- 編集した文字列で生成した結果は `compiled` に記録しない（13.4章の照合では不一致として扱い、試し打ちとする）
- 編集した欄には「編集済み」の表示と［コンパイル結果に戻す］ボタンを付ける
- MOSS の JSON がパースできない場合は、生成を止めてエラーを表示する
- ネガティブ：`applyNegative` が ON のときだけ、編集したネガティブを送る。OFF のときは編集しても送らない。欄に「適用OFFのため送信されません」と注記する
- 再コンパイル（キャスト、服装、Background、Action の変更など）で編集はリセットする。確認のモーダルは出さない。
  その代わり、リセットする前に、保留中の差分を差分ボックスへ自動で保存し、「破棄された編集の差分」と明示する
  - リセットするのは、新しいコンパイル結果の4つの文字列、または scene / action / background の id が変わったときだけ。
    ネガティブ適用の切り替え、保存、フォルダ移動、内容が変わらない再読み込みではリセットしない
  - 破棄された差分は最新の1件だけを保持し、差分ボックスの現在の差分の下に表示する（次の破棄で置き換える）
- 編集の反映（差分の更新）は、欄からフォーカスが外れたときと［差分を更新］ボタンのときだけ行う。キー入力ごとの処理はしない
- Anima 段だけの再試行：編集した文字列で生成していた場合は、再試行でも最初の生成で送った文字列を使う（その後に欄を編集していても、再試行には使わない）

**ソースマップ（出どころの記録）**

- コンパイラは、出力の各部分がどこから来たかの記録を持つ。MOSS は各文字列フィールドの区間ごと、Anima はタグごとに、
  エンティティの種類、id、項目のパスを持つ（例：Action `beach_chat` の `mossItems[1].caption`、Character `elesa_bw` の `anima`、Background `beach` の `mossCaption`）
- コンパイラが付けた部分（品質タグ、rating、人数タグ、solo、meta、固定値）は「生成」として記録する
- 追加したタグの出どころは、挿入した位置の所有者とする
- ネガティブも、層（全体ベース／キャラ／服装／Background／Action）ごとにタグの出どころを持つ。全体ベースは「生成（negativeBase）」とする
- 既存の `compile_scene` の出力（文字列）は変えない

**差分**

- 編集前（コンパイル結果）と編集後の差分を作り、読み取り専用のテキスト欄に表示してコピーできるようにする。標準ライブラリ（difflib）だけで作る
- 形式は次で固定する：

```
=== MOA quick-edit diff ===
scene: <scene id> / action: <action id> / background: <background id>
[Anima]
- <削除したタグ>        ← <出どころ>
+ <追加したタグ>        ← <出どころ>
[MOSS] items[1].caption
  ...[-旧の単語-]{+新しい単語+}...   ← <出どころ>
[MOSS] items[0].bbox
  [旧の4数値] → [新の4数値]        ← <出どころ>
[Negative MOSS] / [Negative Anima]
  ...
```

- Anima はタグ単位で、並び順で比較する（集合では比べない。同じタグが繰り返されても、どの位置かを区別する）
- MOSS は JSON の文字列フィールドごとの単語差分。bbox は数値の旧→新で表示する。item の追加・削除は item 単位で表示する。
  JSON がパースできない場合は、行単位の差分に切り替える（元の行の出どころは、コンパイル結果の JSON の同じ行から付ける）
- コンパイラが付けた部分を編集した行には「エディタでは直せない。rating／extras／sex で変える」と注記する
- 未保存の Scene（新規作成中）では、scene の欄は `(未保存)` とする
- 破棄された編集の差分は、先頭に `# 破棄された編集の差分` の1行を置き、そのあとは同じ固定形式にする

**反映方法**

- 今回は (a)：差分は出どころ付きの参照メモとして使い、エディタでは人が手で直す。エディタ側は変更しない
- 将来の (b)（エディタに貼ると該当欄へジャンプしてハイライト）と (c)（出どころが一意で、Action テンプレートへの追加だけを確認付きで自動適用）のために、差分の形式は上で固定したものから変えない

**表示（文字サイズ）**

- プロンプトの表示・編集欄（MOSS JSON、Anima、ネガティブの MOSS／Anima）と差分欄の文字を大きくし、行間も少し広げる
- 専用の CSS クラス `moa-prompt` で指定し、ほかの部品には影響させない
- 文字サイズは `config.yaml` の `builder.promptFontSize`（既定 16px）で変える

**色付きの差分表示**

- 編集した欄のすぐ下に、読み取り専用の差分表示欄（`gr.HTML`）を置く。編集欄そのものには色を付けない
- 追加した部分は緑の背景（太字）、削除した部分はグレーの文字に取り消し線を付け、元の位置に表示する（色だけに頼らない）
- 差分の計算は、上の「差分」の生成をそのまま再利用する（Anima はタグ単位で並び順の比較、MOSS は文字列フィールドごとの単語差分と bbox の数値の変化、
  item の追加・削除）。MOSS の JSON がパースできないときは、行単位の差分で表示する。新しい差分処理は作らない
- 更新するのは、欄からフォーカスが外れたときと［差分を更新］ボタンを押したときだけ（キー入力ごとにはしない）
- 編集していない欄では、差分表示欄を出さない。［コンパイル結果に戻す］や再コンパイルで編集がなくなったら消す
- 表示するとき、HTML として特殊な文字（`<` `>` `&` `"` `'`）はエスケープする
- 変更箇所（追加・削除の部分）をクリックするか、行末の［移動］ボタンを押すと、該当する編集欄にフォーカスし、カーソルを
  その変更の最後尾（削除は削除した箇所）に置く。欄の中も、ページも、その行が真ん中に来るようにスクロールする。
  位置は、差分を作った時点（フォーカスを外した時点）の編集欄の文字列に対するもの
  - Anima・ネガティブはタグ単位、MOSS は JSON の文字列フィールド内の単語単位（エスケープを数え直す）、bbox・item は値の末尾、JSON が壊れているときは行末
- コピー用の固定形式のテキスト差分（上の「差分」）は変えない

### 10.2 シーンの保存（保存ブロック）

保存は「開く」とは別の操作（行き先と ID を決めて書き込む操作）で、画像を生成した後に使う。そのため共通ピッカーとは切り離し、
画面の一番下に保存専用のブロックを置く。Scene を開く操作は共通ピッカーのまま変えない。エディタは変更しない。

**配置**

- 画面の一番下に「シーンを保存」ブロックを置く。保存先の指定と保存ボタンはここに移し、元の場所（共有ブラウザの保存先モード、保存セクション）からは無くす
- ブロックの中身：
  - パンくず＋フォルダ一覧（`scenes/` 配下を1階層ずつ移動。Gradio にツリー部品は無いので、ピッカーと同じ形でよいが、開く用のピッカーとは別の保存専用のインスタンスにする）
  - そのフォルダにある既存 Scene の一覧
  - id 入力欄、表示名（`name`）の入力欄
  - ［上書き保存］［別名で保存］
  - ［＋フォルダ作成］（EDITOR_SPEC 12章-3。削除せずこのブロックへ移す。フォルダ名の検証は変えない）

**初期値**

- フォルダ：セッションの最初は、今開いている Scene のフォルダ（開いていなければ、直前に開いた Scene のフォルダ。それも無ければ `scenes/` 直下）。
  **一度保存したあとは、最後に保存したフォルダ**（別の Scene を開いても、保存先の初期値は変わらない）。
  画面（セッション）の状態として持つだけで、ファイルには書かない。セッションが切れる（ページを開き直す、ビルダーを再起動する）とリセットされる
- 別名保存の id：Scene を開いていれば `<元の id>_copy`、新規作成中なら `<action id>__default`。その id が使われていれば、空いている最小の番号まで進める
  （`_copy2`、`_copy3` … ／ `__default2`、`__default3` …）。「使われている」は、索引にある id、または `scenes/` のどこかに同名のファイルがあること

**［上書き保存］**

- 今開いている Scene のファイルに、id の入力なしで保存する。確認は出さない
- Scene を開いているときだけ押せる（新規作成中は押せない）

**［別名で保存］**

- id の形式（`^[a-z0-9_]+$`）と、`scenes/` 全体での重複を確認する。エラーなら保存せず、理由を表示する
- 一覧で既存 Scene を選ぶと、その id が入力欄に入り、フォルダもそのファイルのフォルダに切り替わる
- 上書きが許されるのは、一覧で選んだそのフォルダのそのファイルだけ。確認（［上書きする］［キャンセル］）を出してから書く。
  別のフォルダに同じ id がある場合は重複エラー
- 保存したら、開いている Scene を保存したものに切り替え、そのフォルダを「最後に保存したフォルダ」にする（［上書き保存］も同じ）

**保存する内容**（7.6章・13.4章のとおり）

- Scene の定義（`background`、`action`、`cast`、`gen`、`applyNegative`、`name`）を書く。クイック編集の内容は一切含めない
- `compiled` のスナップショット（`mossPrompt`、`animaPrompt`、`background`、`action`、`canvas`、`rating`、`negativeApplied`）は、保存のたびに毎回書く。不一致でも省略しない
- 生成の記録（`seed`、`images`、`gen`、`partial`）は、保存する内容が直近の生成に使った内容と一致するとき（13.4章の記録する条件）だけ書く。
  一致しないとき（生成の後にキャストや Background・Action を変えた、クイック編集した文字列で生成した、など）は、生成の記録を書かずに保存し、
  違った項目を**警告**として表示する（保存は止めない）
- 内容を変えて既存のファイルに上書きするとき、そのファイルにあった古い生成の記録は消す（13.4章の最後の項目）。今回の生成と一致していれば、新しい生成の記録を書く
- 外部変更の検知（更新時刻の比較と2択。EDITOR_SPEC 12章-4）は、［上書き保存］［別名で保存］の両方に適用する
- 保存したら「保存しました：<フルパス>」を表示する。検証エラーがあるときは保存しない

## 11. 受け入れテスト

`samples/` のデータと `samples/examples/` の期待出力を使う（pytest で自動化）。

1. **4つのサンプル Scene の出力が `samples/examples/<scene id>_*` と一致**すること（MOSS は末尾改行を除いて文字列で完全一致）。各サンプルの見どころ：
   - `beach_chat__trio`：服装解決の3パターン（left・right は省略→defaultOutfit、center は Scene の `none` が Action の指定に勝つ）。Background の mossItems（海と波打ち際）が先頭の item になる
   - `beach_solo__elesa`：Action の服装指定（elesa_sling）が defaultOutfit に勝つ。rating は Background safe × Action sensitive → `sensitive`。`1girl, solo`。`{{solo.bodyHair}}` の展開
   - `guilty_lineup__trio`：Action のスロット別服装指定。meta に Action の metaTags（idol 等）が入る。白背景（mossItems なし）
   - `beach_wave_boy__elesa`：`extras: {male: 1}` で `1girl, 1boy`（solo なし）。Scene の服装上書き（hex_summer）が defaultOutfit に勝つ。applyNegative: true
2. （欠番。v1.5 との比較は15で行う）
3. （欠番）
4. `center` を別キャラに差し替えると、MOSS はそのスロットの item の caption だけ、Anima はそのキャラと服装のタグだけが変わること
5. 服装の三値：Scene で `outfit` 省略 → Action → Character の順に委譲、`none` で服装テキストなし（壊れた文・空タグ・連続カンマなし）。`outfit: null` はスキーマエラー
6. キャスト1人・extras なし → `1girl, solo`。`sex: male` 1人 → `1boy, solo`。female 2＋male 1 → `2girls, 1boy`。6人 → `6+girls`。
   キャスト1人＋`extras: {others: 3}` → `1girl`（solo なし、others のタグは生成しない）
7. テンプレートに `3girls`、`solo`、`1boy` を書くと、除去されて要素ごとに警告が出ること。キャラブロック内のタグは除去されないこと
8. rating：Background が safe、Action が explicit → 実効 explicit が meta と Anima の両方に入ること
9. bbox：はみ出し量つきの警告、完全にはみ出しの区別、大小逆転のエラー
10. Background にプレイスホルダーがあるとエラー
11. `applyNegative: false` で送信ネガティブが空、`true` で6章どおりの合成（層の順：base→キャラ→服装→Background→Action）
12. Animaの重複タグが保持されること（`huge breasts` 3回、`pokemon` 3回）
13. 体毛：`{{slot.bodyHair}}` が体毛なしのキャラで空になり、文・タグが壊れないこと
14. 保存：コメントが残ること、`compiled` に background / action / canvas / rating が入ること
15. 移行（14章）：v1.5 の `spec/samples/data` を移行ツールにかけると、v2 スキーマで全ファイルが通り、
    移行後の `beach_chat__trio` のコンパイル結果が `samples/examples/v1.5/` と **MOSS・Anima・ネガティブとも完全一致**すること
    （Action に残った `3girls` は除去され、同じ `3girls` がコンパイラから生成されるため。警告は1件出る）

16. クイック編集（10.1章）：編集した文字列が生成リクエストに載る（ネガティブは適用ON のときだけ）／編集した生成は記録されない／壊れた MOSS JSON で生成が止まる／差分（タグの追加・削除、単語差分、bbox、item の追加・削除、行差分への切り替え、出どころと「生成」の注記）／再コンパイルでのリセットと破棄差分／Anima 段の再試行で最初の文字列を使う／4つのサンプル Scene で、すべての Anima タグと MOSS の文字列に出どころが付く

## 12. 将来拡張

- 背景と行為の相性チェック（`provides` / `requires`）。導入する場合は、使えるタグを語彙ファイル（例 `vocab/background_features.yaml`）で一元管理し、
  照合は「要素があるかどうか」だけで、背景の物とキャラ bbox の**位置関係までは検証しない**ことを明記する
- 縦横比の違う Background への bbox の自動換算
- ネガティブの層ごとの個別ON/OFF、キャストセット（複数スロットの割当プリセット）、シリーズ（Sceneの順序付きグループ）

## 13. ComfyUI連携

### 13.1 設定ファイル `config.yaml`

- `comfyui.url`（既定 `http://127.0.0.1:8188`）、`comfyui.workflow`（API形式のワークフローJSON。config.yaml からの相対パス）、
  `comfyui.timeoutSec`（**段ごと**の待ち時間上限）、`comfyui.outputDir`（任意）。起動時の `--comfy-url` は設定より優先
- `nodes`：`{node: "ID", input: 入力名}` で差し込み先を指定する：
  `mossPositive` / `animaPositive`（必須）、`mossNegative` / `animaNegative`（任意）、
  `seed`（必須。複数可）、`width` / `height`（任意。両方指定か両方省略）、
  `samplers`（任意。`moss` / `anima` の KSampler ノード。`gen` の対象。13.5章）
- `outputs.moss` / `outputs.anima`：各段の画像を出力する保存ノードのID（必須）
- 読み込み時に、ノードIDと入力名がワークフローに実在し、値を差し込める入力（他ノードへの接続でない）であることを検証する。不正なら生成ボタンは押せない
- `python -m moa.comfy.suggest <workflow.json>` で割り当ての候補を推定して出力する

### 13.2 送信する内容

- MOSS用プロンプトは `mossPrompt`、Animaは `animaPrompt`（7章の結果そのもの。クイック編集中は表示中の文字列。10.1章）
- `width` / `height` には **Background の canvas** を入れる [v2]。16の倍数でない場合は警告
- シードは**生成ごとにランダムな値を1つ**決め、`nodes.seed` の**すべて**に同じ値を入れる（MOSS段とAnima段で共通）
- シードの値域は `0 〜 4294967295`（2^32−1）。ワークフロー側の seed 入力により小さい上限があれば小さい方を上限とし、
  送信前に範囲を確認して、範囲外なら生成を開始しない（画像が半分だけできてから失敗しないため）。
  範囲外のシードが `compiled.seed` に残っていた場合は、再生成時に新しいシードを振り直す

### 13.3 ネガティブ

- 適用ON：6章で合成したネガティブで `mossNegative` / `animaNegative` のノードを置き換える
- 適用OFF：これらのノードには**触れない**（ワークフロー既定のネガティブがそのまま使われる）

### 13.4 生成の失敗と結果の記録

- 生成は MOSS段 → Anima段 の順。`timeoutSec` は段ごとの制限で、超えたらその段の失敗とする
- **MOSS段が失敗**：画像はなし。`compiled.seed` / `images` には何も書かない。選んだシードは破棄し、再生成では新しいシードを振る
- **Anima段が失敗（MOSS段は成功）**：MOSS画像は表示したまま残し、Anima枠は「失敗」と表示する。
  MOSS画像を入力に **Anima段だけ再試行**できる（シードは最初の生成と同じ値。MOSS段は再実行しない）
- 完了したら両段の画像を表示する
- 画像の取得は ComfyUI の `/view`。`compiled.images` に書くパス：
  - 既定：ComfyUI側のパス（`outputDir` があればその下の絶対パス、なければ `output/<subfolder>/<ファイル名>`）
  - UIでコピー先フォルダを指定したときは、そこへ保存して**コピー先のパス**を記録する（同名があれば `_1` などを付けて上書きしない）
  - 並びは MOSS段 → Anima段
- 生成しただけでは YAML に書かない。**保存ボタンを押したとき**に `compiled.seed` / `images` / `gen` を書く
- **クイック編集で生成した結果は記録しない**（10.1章）。保存時に「クイック編集（試し打ち）」として画像を記録せず、その旨を表示する
- **記録する条件**：生成時と保存時で、次がすべて一致すること。1つでも違えば画像を記録せず、違った項目名を画面に列挙する：
  - `mossPrompt`、`animaPrompt`、ネガティブ適用（ONなら合成ネガティブも）
  - Background / Action の id、canvas
  - `gen` の実効値（MOSS steps/cfg、Anima steps/cfg/denoise）
- **部分結果**：MOSS段の画像だけを記録するときは `compiled.partial: true` を書く。両段がそろったら次の保存で `partial` を消す。
  部分結果でも、上の条件が崩れた後の保存では記録しない（部分結果も破棄する旨を表示）
- 保存時に生成結果がなく、`compiled` に保存済みのプロンプトが今回の内容と異なる場合は、食い違った画像を残さないよう
  `seed` を null、`images` を空にし、`gen` と `partial` を削除する。内容が同じなら既存の値を残す

### 13.5 生成パラメータ（`gen`）

```yaml
gen:
  moss:  { steps: 50, cfg: 3.0 }
  anima: { steps: 14, cfg: 1.8, denoise: 0.45 }
```

- 対象：MOSS段は `steps` / `cfg`、Anima段は `steps` / `cfg` / `denoise`。`gen.moss.denoise` はエラー。
  対象のサンプラーは `nodes.samplers.moss` / `anima`（入力名は `steps` / `cfg` / `denoise` 固定）
- 値の範囲：`steps` は1以上の整数、`cfg` は0より大、`denoise` は0〜1
- 解決順：Scene.gen → ワークフローJSONの値。**書いた項目だけ**を上書きする
- 記録：生成時に実際に使った値を、継承分も含む全項目で `compiled.gen` に記録する（Scene.gen を変えて保存しても書き換えない）
- UI：項目ごとにスライダーと出どころ（Scene / ワークフロー）。動かした項目だけ Scene.gen に入る。項目ごとに「ワークフローの値に戻す」。
  Scene を開くとその Scene.gen が入る。Background / Action を選び直しただけでは操作中の値を保つ。`nodes.samplers` が未設定の段は操作不可

### 13.6 Anima段の解像度倍率（画面だけ・保存しない）

- UI のスライダー（0.1〜2.0、step 0.05、既定 1.0）。canvas の縦横比を保ったまま、Anima 段の解像度を変える
- **Scene.gen にも `compiled`（`gen` / `images`）にも書かない**。13.4章の記録条件にも含めない。生成結果の表示に、使った解像度を出すだけ
- サイズ：`w' = max(64, round(canvas.w × 倍率 / 16) × 16)`、`h'` も同様（それぞれ最も近い16の倍数。四捨五入は 0.5 を切り上げ）。
  丸めで縦横比が最大1%弱ずれるが、画像全体を `w'×h'` に拡大縮小する（crop しない）。いずれかの辺が512px未満になるときは警告する
- 実装：倍率が 1.0 以外のときだけ、送信するワークフローの MOSS→Anima の接続（`stage_boundary()` が見つける入力。VAEEncode の `pixels` など）の手前に
  `ImageScale`（`lanczos`、`crop: disabled`、`w'`×`h'`）を挟む。ワークフロー JSON と `config.yaml` は変更しない。1.0 のときは何も挟まない
- 使える条件：`nodes.width` / `nodes.height` の指定があり、MOSS→Anima の接続が1つに決まること。満たさないときはスライダーを操作不可にする
- 「Anima段だけ再試行」（13.4章）は、再試行時点の倍率を使う（MOSS 画像 → `ImageScale` → Anima 段）

## 14. v1.x からの移行

`python -m moa.migrate_v2 --src <v1.xのdataフォルダ> --out <新しいフォルダ>` を用意する。

- **元のフォルダは一切変更しない**。結果は `--out` に新しく書く（`--out` が空でなければ中止）
- `characters/` `outfits/` はそのままコピーする（`sex` は書き足さない＝female 扱い）
- **Situation 1つ → Background 1つ＋Action 1つ**に機械的に分割する（元の相対フォルダ構成と `_folder.yaml` を両方のルートに複製）：
  - Background：id は `<situation id>_bg`。`name` は「<元のname>（背景）」、`rating`、`canvas`、
    `metaTags` は元の `summary.meta` から `anime illustration` と rating 語（safe / sensitive / explicit / nsfw）を除いたもの、
    `mossCaption` は空、`mossItems` は空、`anima` は空
  - Action：id は元の situation id（**Scene の id 命名の目安とそのまま一致する**）。`name`、`tags`、`rating`、`slots`、
    `outfits`（null の値は捨てる）、`background: <situation id>_bg`、
    `mossCaption` は元の `summary.caption`、`mossItems` は元の `items`、
    `anima` は元の `animaPrompt` から**先頭に並ぶ**品質タグ（`masterpiece` / `best quality` / `score_*`）と rating 語を除いたもの、
    ネガティブは元のものをそのまま Action に移す
- **Scene**：`situations/` 配下の Scene を、同じ相対フォルダで `scenes/` に移す。`situation` → `background: <id>_bg` と `action: <id>`、
  `cast` と `outfits` をネスト形式に統合（null は省略）、旧 `denoise` は `gen.anima.denoise` に。`compiled` はそのまま保持する
- **移行レポート**（`--out/MIGRATION_REPORT.md`）を出す。Situation ごとに：
  - Action の anima に残った人数タグ・solo（v2 ではコンパイル時に除去される）。**特定のモブを表す人数タグ（例 `1boy`）は `extras` を手で設定する必要がある**と明記
  - 人数を表していそうな他のタグ（`^\d+\+?[a-z]+$` に一致するもの。例 `3children`）。キャスト1人の Action でこれがあると、
    `extras` を設定しない限り誤って `solo` が付くので、`extras.others` の設定を促す
  - v1.x で人数タグを書いていなかった Action では、v2 で `1girl` などが新しく付くこと
  - summary.caption を背景と行為に書き分ける作業が残っていること（機械移行では全文が Action 側に入る）
  - 移行前後のコンパイル結果の差分（Anima のタグ列の差など）
- 移行の結果、背景の書き分けをしなくても、v1.x と同じ文面・同じタグの組で生成できる（Anima の人数タグ・solo の自動付与と、除去された人数タグを除く）。
  `spec/samples` の海辺は、移行前後で MOSS・Anima・ネガティブとも完全一致することを確認済み
- 移行後、ユーザーはレポートを見て `extras` の設定と、summary.caption の背景／行為への書き分けを行う（書き分けは任意。しなくても動く）

## 15. エディタ仕様（EDITOR_SPEC.md）への影響

エディタは別アプリ（`editor.py`、ポート 7863）。v2.0 に合わせて次を反映する：

- 編集対象は Character / Outfit / Background / Action（Scene はビルダーのみ）
- Character のフォームに `sex`（female / male）、Action のフォームに `extras`（female / male / others）、`metaTags`、推奨背景 `background`（未設定可）、Background のフォームに `metaTags` を追加
- 服装指定のコントロールは三値（未指定＝キーを書かない／none／Outfit）。null は書かない
- **Background のコピーオンライト**：Scene から参照されている Background を保存すると、上書きせず新しい id の複製として保存する（新 id の入力必須）。
  「使用中」の判定は Scene の `background` 参照のみ（Action の推奨背景は含めない）。保存前に参照 Scene の一覧を表示する
- **スロット保護**（EDITOR_SPEC §1b）は Action に適用する：参照中の Scene がある Action ではスロットの削除・改名を禁止。追加は許すが、影響を受ける Scene を警告表示する
- 保存時の検証に、9.2章の内容（Background のプレイスホルダー禁止、人数タグの警告、bbox のはみ出し警告と大小逆転エラー）を含める
- プレビューは「Background＋Action＋キャラ」を選ぶ（Action の推奨背景を初期値に）。選択は名前で絞り込める方式

## 付属物

- `schemas/` … character / outfit / background / action / scene / folder の JSON Schema
- `samples/data/` … サンプル一式（キャラ6、服装7、Background 2、Action 4、Scene 4。フォルダは平置き）
- `samples/examples/` … サンプル Scene のコンパイル結果（`<scene id>_moss.json` / `_anima.txt` / `_negative.txt`）
- `samples/examples/v1.5/` … v1.5 の `beach_chat__trio` の期待出力（受け入れテスト15の比較用）
