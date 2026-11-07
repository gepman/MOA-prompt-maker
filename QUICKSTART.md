# クイックスタート

MOA-prompt-maker を、**サンプルデータで動かして1枚生成する**までの手順です。上から順に進めてください。

```
① 必要なものを用意    Python 3.11 と ComfyUI
② モデルを置く        MOSS 用 3 ファイル + Anima 用 3 ファイル を ComfyUI に置く
③ SETUP.bat           1 回だけ。.venv を作って依存を入れる
④ ComfyUI を起動      http://127.0.0.1:8188
⑤ QUICKSTART.bat      ビルダー（7860）とエディタ（7863）が開く
⑥ 1 枚生成            Scene を開いて［🎨 生成］
```

このツールは、プロンプト（MOSS 用の JSON と Anima 用のタグ）を組み立てて、**ComfyUI に送って MOSS → Anima の 2 段で画像を作ります**。
そのため、画像を作るには ComfyUI と下のモデルファイルが必要です（プロンプトを組み立てて見るだけなら、ComfyUI とモデルが無くても動きます）。

---

## ① 必要なもの

| もの | 内容 |
|---|---|
| OS | Windows（`.bat` で起動します） |
| Python | **3.11 以上**。3.11 で動作確認しています。入れるときは、インストーラーの「Add python.exe to PATH」にチェックを入れてください。<https://www.python.org/downloads/> |
| ComfyUI | 画像を作る本体。**v0.43.0 で動作確認**しています。**カスタムノードは使いません**（標準のノードだけで動きます） |
| GPU | VRAM 16GB（RTX 5070 Ti）で動作確認しています。VRAM 12GB でも動く可能性がありますが、未確認です |

---

## ② モデルファイルを ComfyUI に置く

クイックスタートが使うワークフロー（`QuickStart/moss_to_anima_i2i_api_v1.1_light.json`）は、次の **6 ファイル**を読み込みます。
**ファイル名はワークフローと完全に同じにしてください**（違うと ComfyUI が見つけられません）。

### MOSS 段（1 段目）

| 役割 | ファイル名 | 置き場所（ComfyUI フォルダの中） |
|---|---|---|
| 拡散モデル | `mossImage_sft_pruned_int8.safetensors` | `models/diffusion_models/` |
| テキストエンコーダ | `mossImage_sft_txt_int4.safetensors` | `models/text_encoders/` |
| VAE | `flux2-vae.safetensors` | `models/vae/` |

入手先：**<https://civitai.com/models/3005406?modelVersionId=3409867>**（MOSS Image。この段のファイルは、このページにひとまとめで配布されています）
【要確認：ページの「Files」に、上の 3 ファイルがすべて含まれているか。含まれていないファイルの入手先】

### Anima 段（2 段目）

| 役割 | ファイル名 | 置き場所（ComfyUI フォルダの中） | 入手先 |
|---|---|---|---|
| 拡散モデル | `anima_turboV11.safetensors` | `models/diffusion_models/` | <https://civitai.com/models/2458426?modelVersionId=3263843> |
| テキストエンコーダ | `qwen_3_06b_base.safetensors` | `models/text_encoders/` | <https://huggingface.co/circlestone-labs/Anima/tree/main/split_files>（`text_encoders/` の中） |
| VAE | `qwen_image_vae.safetensors` | `models/vae/` | <https://huggingface.co/circlestone-labs/Anima/tree/main/split_files>（`vae/` の中） |

- Hugging Face の `split_files` は、`diffusion_models/`、`text_encoders/`、`vae/` に分かれていて、ComfyUI の置き場所と同じ名前です。
- 同じ Hugging Face の `split_files/diffusion_models/` には `anima-turbo-v1.1.safetensors` もありますが、**ファイル名がワークフローの `anima_turboV11.safetensors` と違います**。
  こちらを使う場合は、ファイル名を `anima_turboV11.safetensors` に変えるか、ワークフローの JSON の `unet_name` を書き換えてください。

- **軽量版と通常版**：クイックスタートは、MOSS のテキストエンコーダに **int4**（`mossImage_sft_txt_int4`）を使う軽量版のワークフローです。
  通常版のワークフロー（`workflow/moss_to_anima_i2i_api_v1.1.json`）は同じ名前の **int8**（`mossImage_sft_txt_int8.safetensors`）を使います。ほかの 5 ファイルは共通です。
- 置き場所のフォルダ名は ComfyUI の版によって違うことがあります（古い版では `models/unet/`、`models/clip/`）。お使いの版で、同じ種類のモデルを置いているフォルダに置いてください。
- ファイルを置いたあと、**ComfyUI を起動し直してください**（起動中に置いたファイルは読み込まれません）。

---

## ③ セットアップ（1 回だけ）

プロジェクトのフォルダにある **`SETUP.bat` をダブルクリック**します。

- Python 3.11 以上を探し、`.venv`（このツール専用の Python 環境）を作って、必要なライブラリを入れます。ネットワーク接続が必要で、数分かかります。
- 最後に `Setup finished. Next: start ComfyUI, then double-click QUICKSTART.bat` と出れば成功です。何かキーを押すと閉じます。
- 同じフォルダで **もう一度実行しても安全**です（`.venv` があれば作成を飛ばし、ライブラリだけ確認します）。
- `Python 3.11 or newer was not found.` と出たら、Python を入れ（①）、もう一度 `SETUP.bat` を実行してください。
- `pip install failed` と出たら、ネットワーク接続を確認して、もう一度 `SETUP.bat` を実行してください。

手で行う場合は、PowerShell で次を実行します（`SETUP.bat` と同じ内容です）。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

---

## ④ ComfyUI を起動する

ComfyUI を起動して、<http://127.0.0.1:8188> が開くことを確認します。クイックスタートの設定（`QuickStart/config_quickstart.yaml`）は、この URL に接続します。
別の URL・ポートで ComfyUI を動かしているときは、`config_quickstart.yaml` の `comfyui.url` を書き換えてください。

---

## ⑤ QUICKSTART.bat を起動する

**`QUICKSTART.bat` をダブルクリック**します。

- 黒い窓が 2 つ開き（エディタ・ビルダー）、数秒後にブラウザが 2 つ開きます。
  - **ビルダー** <http://127.0.0.1:7860> … Scene を組み立てて、プロンプトを作り、生成する画面（このあと使います）
  - **エディタ** <http://127.0.0.1:7863> … キャラ・服装・背景・行為のデータを編集する画面
- サンプルデータ（`QuickStart/moa_public_samples`）と、クイックスタート用の設定・軽量ワークフローが使われます。
- 終わるときは、黒い窓を閉じてください。
- `.venv was not found. Run SETUP.bat first.` と出たら、③ がまだです。

---

## ⑥ 1 枚生成する

ビルダー（<http://127.0.0.1:7860>）で行います。

1. 右側の **［📂 Scene を開く］** を押し、左の一覧で `samples` フォルダを開いて、**「普通の立ち絵」** を選びます。
2. 画面の「生成（ComfyUI）」の欄に **「✅ 接続先 `http://127.0.0.1:8188` ／ ワークフロー `moss_to_anima_i2i_api_v1.1_light.json`」** と出ていることを確認します。
   「⚠ ComfyUI連携は無効です」と出ている場合は、下の「困ったとき」を見てください。
   （この表示は設定が読めたことを示します。ComfyUI が起動しているかどうかは、生成したときに分かります。）
3. **［🎨 生成］** を押します。MOSS 段 → Anima 段の順に進み、完了すると画像が並びます。
4. 気に入ったら、下の「シーンを保存」の **［💾 上書き保存］** で Scene を保存します（シードと画像のパスも記録されます）。

> **注意：保存すると、サンプルの YAML ファイルが書き換わります。** 元のサンプルを残したいときは、先に `QuickStart/moa_public_samples` フォルダをコピーして、そのコピーを `--data` で指定して使ってください（git で入手した場合は、`git restore QuickStart/moa_public_samples` でも戻せます）。

ほかのサンプルの Scene（`samples` フォルダの中にあります）も、同じ手順で開いて生成できます。
キャラや服装、背景を差し替えると、MOSS 用と Anima 用のプロンプトが両方とも自動で作り直されます。

---

## 次にやること

- **データを取り込む**：`QuickStart/import_showcase.zip` を、エディタの「📥 インポート」に渡すと、zip の中のキャラ・服装・背景・行為・Scene を既存のデータに追加できます。
- **自分のデータで使う**：`app.py` / `editor.py` に `--data <フォルダ>` を付けて起動します。詳しくは [README.md](README.md) を見てください。
- **Scene を書き出して渡す**：ビルダー下部の「シーンを書き出す（zip）」で、依存するデータごと zip にできます。
- **仕様**：[spec_v2/SPEC.md](spec_v2/SPEC.md)

---

## 困ったとき

| 症状 | 対処 |
|---|---|
| 画面に「⚠ ComfyUI連携は無効です」と出る | 理由が下に出ています。多くは、ワークフローの JSON が `config_quickstart.yaml` の `comfyui.workflow` の場所に無い、または設定のノード番号がワークフローと合っていない場合です。QuickStart フォルダの 2 ファイル（`config_quickstart.yaml` と `moss_to_anima_i2i_api_v1.1_light.json`）を移動・改名していないか確認してください |
| ［🎨 生成］を押すとエラーになる | ComfyUI が起動しているか（<http://127.0.0.1:8188> が開くか）、モデルファイル 6 つが置かれているか（②）、置いたあとに ComfyUI を起動し直したかを確認してください |
| ComfyUI 側で「モデルが見つからない」と出る | ファイル名がワークフローと完全に同じか、置き場所のフォルダが合っているかを確認してください（②）。ファイル名を変えたい場合は、ワークフローの JSON の `unet_name` / `clip_name` / `vae_name` を同じ名前に書き換えてください |
| 黒い窓にエラーが出て、画面が開かない | すでに同じものが起動していて、ポート（7860・7863）が使われている可能性があります。古い黒い窓を閉じてから、もう一度 `QUICKSTART.bat` を実行してください。ポートは `config_quickstart.yaml` の `builder.port` / `editor.port` で変えられます（7861・7862 は別のアプリ用なので使わないでください） |
| `.venv was not found` | `SETUP.bat` を実行してください（③） |
| `SETUP.bat` が失敗する | 画面のメッセージ（Python が見つからない／pip が失敗）を見て、③ の対処を行ってください |
