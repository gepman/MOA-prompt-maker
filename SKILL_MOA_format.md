---
name: moa-data-craft
description: MOA形式のYAMLデータ（キャラクター／衣装／背景／アクション／シーン）を作成・修正するためのスキル。MOAプロンプトビルダー用のデータを書くとき、MOSSレイアウトプロンプトとAnimaタグプロンプトの作法ごと適用する。
---

# MOAデータ作成スキル

MOAは画像生成プロンプトの部品化フォーマットである。生成は2段構え:
**MOSS-Image がレイアウトJSONで構図を固定 → Anima が img2img で仕上げる。**
データを作る側の仕事は、(1) スキーマ通りのYAMLを書くこと、(2) MOSSとAnimaの両方で「描かれる」文章を書くこと。この2つは別の技能である。

by ミュゼエロモン

## 1. エンティティモデル

5種類。参照はすべて **id** で行い、**ファイル名 = id + `.yaml`**。フォルダ階層は自由(参照はid索引で解決される)。

| 種類 | 役割 |
|---|---|
| Character | 人物そのもの(容姿・体)。衣装は持たない |
| Outfit | 衣装。着る物だけを書く |
| Background | 場所とキャンバスサイズ。人物は書かない |
| Action | 状況・ポーズ・行為。プレースホルダを持てるのはこれだけ |
| Scene | Background + Action + 配役(cast)を束ねた完成単位 |

### idの規則(最重要の罠)
- idは `^[a-z0-9_]+$`。
- **数字とアンダースコアだけのid(例 `09_01`)はYAMLで整数として読まれる。必ずクォートする**こと。参照側のフィールドでも同様。これはバリデーションで弾かれる実在の事故パターンである。

## 2. フィールド一覧

必須は **太字**。未知フィールドは不可(スキーマは additionalProperties: false)。

### Character
**id, name, moss, anima** / group, tags, sex, defaultOutfit, bodyHairMoss, bodyHairAnima, negativeMoss, negativeAnima
- `moss`: 人物の完全な描写文(英語の文)。例: `An adult woman, about 157cm tall ..., with huge breasts.`
- `anima`: カンマ区切りタグ。
- `defaultOutfit`: Outfitのid。

### Outfit
**id, name, moss, anima** / tags, negativeMoss, negativeAnima
- `moss`: **完全な文**で書く(`She wears ... .`)。断片の羅列にしない。

### Background
**id, name, canvas, mossCaption, anima** / tags, rating, metaTags, mossItems, negativeMoss, negativeAnima
- `canvas: {w, h}` — 解像度はここにだけ置く。
- `rating`: `safe` / `sensitive` / `explicit`。

### Action
**id, name, slots, mossCaption, mossItems, anima** / tags, rating, background(推奨背景), outfits, extras, metaTags, negativeMoss, negativeAnima
- `slots`: 役の枠名の配列(例 `[left, right]`)。
- `outfits`: スロット→Outfit id の対応表(Action側のデフォルト衣装)。
- `extras`: `{female?, male?, others?}` の人数加算。Action専用で、Anima側の人数計算にだけ効く。
- **プレースホルダはActionの中でのみ使える**(mossCaption / mossItems / anima):
  - `{{slot名}}` → キャラのmoss/anima
  - `{{slot名.outfit}}` → 衣装のmoss/anima
  - `{{slot名.bodyHair}}` → bodyHair差し込み位置

### Scene
**id, background, action, cast** / name, applyNegative, gen, compiled
- `cast`: スロット名 → `{character: id, outfit?: ...}`。
- **outfitの三態**: id文字列=その衣装 / `"none"`=裸(明示) / キー省略=優先順位に任せる。**`null` は禁止**。
- 衣装の優先順位: **Scene > Action > Character の defaultOutfit**。
- `compiled` はビルダーが書く欄。手で書かない。
- `gen`(生成パラメータ)は原則書かない。既定値は生成ワークフロー側が持つ。

## 3. コンパイラがやること(データ側で書くな)

- **人数タグとsolo判定は自動生成**される(cast + extras から計算。合計1名で solo)。情報源のanimaに人数タグが混ざっていたらコンパイル時に除去され警告が出る。最初から書かない。(`3children` のような語は対象外なので消えない点に注意)
- Animaの品質接頭辞(`masterpiece, best quality, score_7, ...`)と評価タグはコンパイラ定数。データ側に書かない。
- MOSSの summary caption は Background.mossCaption + Action.mossCaption を1スペースで連結したものになる。各captionは単独でも意味が通るように書く。
- rating は Background と Action の厳しい方が効く。
- ネガティブは base→キャラ→衣装→背景→アクションの順に合成され、大文字小文字を無視して重複排除される。`applyNegative` はデフォルトOFF(シーン側で有効化)。
- bboxがキャンバスをはみ出しても**警告止まり**(エラーではない)。意図的なはみ出しはあり得る。

## 4. MOSS作法

MOSSはレイアウトJSONを読む。構造:

```json
{
  "summary": {"meta": ["anime", "illustration", "sensitive"], "canvas": "single", "caption": "場面全体の要約"},
  "items": [{"label": "object", "caption": "対象の完全な描写", "bbox": [y_min, x_min, y_max, x_max]}],
  "bbox_coord_space": "abs_centralized",
  "bbox_format": "yxyx"
}
```

- 座標は**キャンバス中心原点の絶対ピクセル**。x右+、y下+。例: 1024×1536なら x −512…512、y −768…768。
- **captionの散文が本体**であり、bboxは大まかな配置ヒントにすぎない。itemのlabel名はほぼ飾り。
- キャラを描くitemのcaptionには、**そのキャラの登録moss文を必ず含める**。省くと装備品(ヘルメット等)が落ちる。
- **mossCaption(要約文)でのキャラ参照**: 配役キャラを髪色・体型などの特徴語で呼ばない(特徴はプレースホルダ経由で入る)。単独場面では一般語(a woman 等)でよい。**複数人場面では `{{slot}}` を文の主語にして参照する**。所有格(「彼女の〜」)は独立したプレースホルダ主語の文に分ける。キャプション内では `{{slot.outfit}}` は使わない(衣装はitem側だけ)。理由: 結びつきの鍵は登録文の「シルエット」(輪郭+色の塊)であり、場面用の固有名や位置だけの参照は結びつかず、複数人の取り違えが起きる(2026-11-07検証: 名前方式・位置のみ方式は両シードで入れ替わった)。Anima側の散文補足では位置参照(the left woman 等)だけを使う。
- **状態を書け、過程を書くな**。「めくれている」ではなく「すでにめくれ上がっている」。動作の説明ではなく見た目の記述にする。主語中心の言い回しが効く(“a penis is inserted”より“the man thrusts himself deep into her”)。
- **色分離**: 接する物の色は、肌・衣装・髪・背景と色相でも明度でも離す。ベタつく組み合わせは描画が溶ける。接触境界は暗い影のラインで分けると読める。
- **bboxで人を消すことはできない**。小さく・端に・画面外に置いても、captionに書かれた人物はフルサイズで画内に出る。人物なしの静物を描きたいなら**itemごと書かない**。
- **専用bboxテクニック**: 特定の場所に当てたい小物(当て布、道具、手)は、キャラのitemに混ぜず**それ専用のitem+bbox**を切る。bbox中心を当て先に合わせ、最も近い画面端から入れる。
- 手BBOXの境界（2026-11-04検証）：小物・画面外の手など独立した要素の専用bboxは配置を安定させるが、画内人物の自分の手など身体部位のbboxは、その部位だけを動かせず構図全体を引っ張る。姿勢指示・MOA2の部位単位レイアウトには未検証。
- **ワイプ(インセット)**: 直接描けない行為は、メイン画面では接触部を隠し、行為自体を絵中絵の別itemに描く。インセットの形はbboxのアスペクト比で決まる。中身は解剖学的にアンカーする(何のアップか分かる要素を入れる)。入れないとモデルが別の部位(乳首等)に化けさせる。**ワイプ内の体毛は色・量を直書きせず、`{{slot.bodyHair}}` プレースホルダで差し込む**。文側に残すのは位置の語だけ(例: パネルの縁に体毛)。直書きするとキャラ登録と二重定義になり、色がずれたときに競合する。
- **語の経済**: 長く細かい指定は薄まる。短く、モデルがよく学習した一般語で書く。「何を避けたいか」から逆算して最も単純な語を選ぶ(粗いペンチを避けたいなら試験管トングではなく `long clip`)。語が含意する事は重ねて書かない(`rusty` なら `metal` は落とす)。
- 否定したい物は物体ワードをポジティブから外し、形状の記述に置き換える。ただし実在の学習済み物体が欲しいときは名前で呼ぶ方が当たる。

## 5. Anima作法

- タグは小文字、区切りはカンマ+スペース、アンダースコアでなくスペースを使う(`score_7` 等の例外あり)。
- 並び: [人数(自動生成なので書かない)] [キャラ] [衣装・外見] [行為・ポーズ] [場所・画角]。文の形で書きたい補足はタグ列の末尾に散文で足せる(MOSS由来の状況文がそのまま効く場合がある)。
- 重み付けはSDXLより強く効く。`(huge breasts:2)` 級も使う。
- **ネガティブ走査ルール(厳守)**: 欲しい物の名前をネガティブに入れるな。代表例: `no urine` は尿そのものを消す。欲しい物はポジティブ側で強く言う(`(peeing:1.4), (peeing herself:1.3), (urine stream:1.4)`)。逆に、要らない物はポジティブ側からも完全に抜く。
- **キャラ側ネガティブは同一性ガードだけ**(duplicate、色違い、flat chest、年齢ガード等)。人数関連(`2girls`, `multiple girls`, `man` 等)はキャラに置くな。全シーンに混入して共演者と喧嘩する。人数ガードが必要ならAction側に置く。
- ポーズの方向: 「反らせる(arched back)」は腰が後ろに逃げる。腰を前に出したい場面では pelvis / crotch を前に出す言い回しにする。

## 6. bodyHairルール

- sensitive(成人向け)キャラのデフォルト: **髪と同色で sparse / thin**。
- `bodyHairMoss` は**完全な文**、`bodyHairAnima` はタグ。
- **safeキャラには bodyHair フィールド自体を定義しない**。

## 7. ハウスルール(内容)

- エロ用途で使ってよいのは、**公式に成人として描かれたキャラクターのみ**。未成年キャラの年齢改変(○年後設定など)は不可。年齢設定がないキャラは「成人として描かれているか」で判断する。
- safe / sensitive / explicit の rating は内容に正直につける。
- **(お約束) 成人女性は全員爆乳にすること。**

## 8. 例

### Outfit(実在の登録例)

```yaml
id: taimanin_suit
name: 対魔忍スーツ
tags:
- taimanin
- ninja
- bodysuit
moss: |-
  She wears a Taimanin ninja bodysuit: a purple bow tie at her neck, the chest section of the suit in white covering her whole chest, spilling out at the sides as well, sideboob and underboob, the suit turning navy blue from her stomach down over her lower abdomen as a high-cut leotard bottom, a tiny purple side skirt, short panels hanging only at the sides of her hips, open at the front and back, and black thigh-high boots.
anima: bodysuit, ninja suit, taimanin suit, purple bowtie, bow tie, white chest, underboob, sideboob, navy blue bodysuit, high-cut leotard, tiny skirt, side skirt, purple skirt, open front, thigh boots, black thigh boots
negativeAnima: full skirt, long skirt, dress, pants, shorts, sleeves, gloves, mask, hood
```

### Scene

```yaml
id: s1_climax__default
name: 絶頂（足ピン・乳首引っ張り・潮吹き）
background: fighting_stage
action: s1_climax
cast:
  aya:
    character: aya
applyNegative: true
```

### Actionの骨格

```yaml
id: example_action
name: 例
rating: explicit
background: example_background
slots: [left, right]
outfits:
  left: example_outfit
extras:
  male: 2
mossCaption: '場面全体の要約文。'
mossItems:
- label: object
  caption: '左に {{left}} {{left.outfit}} 右に {{right}} {{right.outfit}} ...'
  bbox: [-470, -740, 500, 740]
anima: '{{left}}, {{left.outfit}}, {{right}}, {{right.outfit}}, ...'
```

## 9. 納品前チェックリスト

- [ ] idが規則通りか。数字だけのidをクォートしたか(参照側も)
- [ ] 必須フィールドが揃い、未知フィールドがないか
- [ ] 参照id(背景・アクション・キャラ・衣装)が全部実在するか
- [ ] animaに人数タグ・品質接頭辞を書いていないか
- [ ] ネガティブに「欲しい物の語」が混入していないか(両方向に走査)
- [ ] キャラ側ネガティブが同一性ガードだけになっているか
- [ ] outfit の moss が完全な文になっているか
- [ ] プレースホルダがAction以外に紛れていないか / outfit が `null` になっていないか
- [ ] mossCaptionで配役キャラを特徴語で呼んでいないか / 複数人場面は `{{slot}}` 主語参照になっているか / キャプションに `{{slot.outfit}}` がないか
- [ ] ワイプ内の体毛が `{{slot.bodyHair}}` になっていて、色・量の直書きがないか
- [ ] 色分離: 接する要素同士の色が色相・明度で離れているか
