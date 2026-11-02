"""インポートの画面（EDITOR_SPEC 15章）。処理は moa.editor.logic.importing、ここは部品と配線だけ。

方針（CLAUDE.md）：部品の個数は変えない。可変個の一覧（競合・種類・description・表示名・結果）は、それぞれ1つの表
（Dataframe）に入れる。固定個数（5種類の配置先）は表示／非表示で切り替える。キー入力ごとの処理はしない（ボタンのときだけ）。
インポートの部品は専用の dict（imp）に持ち、エディタ全体の outputs（OUT）には入れない。
"""
from __future__ import annotations

from functools import partial
from pathlib import Path

import gradio as gr

from moa.core.repository import KINDS, Repository
from moa.editor.logic import picker as PK
from moa.editor.logic.importing import (Choices, Inspection, apply_plan, choices_from_tables, folder_dialog_blocked,
                                        inspect_batch, read_batch, resolve, result_rows, summary_md)
from moa.editor.logic.importing import report as R
from moa.ui.dialogs import pick_folder

ROOT_DIR = PK.ROOT_DIR
LABEL = PK.IMPORT_LABEL
INTRO = ("外部で作った MOA ファイル集（Character / Outfit / Background / Action / Scene と `_folder.yaml`）を、既存のフォルダ階層に**追加**します。"
         "検査を通ったものだけを取り込み、**既存のファイルは上書きしません**。手順は 検査（書き込まない）→ 確認 → 取り込み です。")


def _table(headers: list[str], datatype: list[str], static: list[int], label: str, **kw) -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=headers, datatype=datatype, type="array", interactive=True,
                        static_columns=static, label=label, visible=False, wrap=True, **kw)


def build_import_section() -> dict:
    """部品を作る（gr.Blocks の中で呼ぶ）。"""
    imp: dict = {}
    with gr.Accordion("📥 インポート（外部の MOA ファイル集を取り込む）", open=False):
        gr.Markdown(INTRO)
        imp["source"] = gr.State(None)     # {"kind": "upload" | "folder", "path": ...}
        imp["insp"] = gr.State(None)       # 検査の結果（確認画面を出している間だけ持つ）
        imp["plan"] = gr.State(None)       # 確認画面に出している計画（書き込み直前に、作り直した計画と比べる）
        with gr.Row():
            imp["file"] = gr.File(label="アップロード（.yaml または .zip）", file_types=[".yaml", ".zip"], file_count="single",
                                  type="filepath")
            with gr.Column():
                imp["dir_btn"] = gr.Button("📁 フォルダを選ぶ")
                imp["dir_show"] = gr.Textbox(label="選んだフォルダ（読み取り専用）", interactive=False)
                imp["dir_note"] = gr.Markdown()
        imp["inspect_btn"] = gr.Button("🔍 検査（書き込みません）", variant="primary")
        imp["msg"] = gr.Markdown()
        imp["summary"] = gr.Markdown()
        with gr.Group(visible=False) as box:   # 確認画面
            gr.Markdown("### 確認\n選ぶ必要があるものだけが表示されます。選んだら［確認を更新］で結果を作り直せます。")
            imp["kinds_df"] = _table(R.KINDS_HEADERS, ["str"] * 3, [0, 1],
                                     "種類を選ぶ（複数の種類に適合するファイル）：「取り込む種類」に候補のどれかを入力。空のままなら取り込みません")
            imp["conf_df"] = _table(R.CONFLICT_HEADERS, ["str", "str", "str", "str", "bool", "str"], [0, 1, 2, 3],
                                    "同じ id が既にあるファイル：既定はスキップ。「別IDで取り込む」にチェックし、新しい ID を入力")
            for k in KINDS:
                with gr.Row(visible=False) as row:
                    imp[f"dest_show_{k}"] = gr.Markdown()
                    imp[f"dest_btn_{k}"] = gr.Button(f"{LABEL[k]}の取り込み先を選ぶ", size="sm", scale=0, min_width=220)
                imp[f"dest_row_{k}"] = row
                imp[f"dest_{k}"] = gr.State("")
            imp["desc_df"] = _table(R.DESC_HEADERS, ["str", "str", "str", "bool"], [0, 1, 2],
                                    "フォルダの description が違う：既定は既存。取り込み側を使うならチェック")
            imp["label_df"] = _table(R.LABEL_HEADERS, ["str", "str", "bool"], [0, 1],
                                     "既存のフォルダに _folder.yaml が無い：表示名を付けるならチェック（既定は付けない）")
            with gr.Row():
                imp["refresh_btn"] = gr.Button("確認を更新")
                imp["apply_btn"] = gr.Button("📥 取り込む", variant="primary")
                imp["cancel_btn"] = gr.Button("キャンセル")
        imp["box"] = box
        imp["result_df"] = gr.Dataframe(value=[], headers=R.RESULT_HEADERS, datatype=["str"] * 3, type="array",
                                        interactive=False, wrap=True, visible=False, label="ファイルごとの結果")
    return imp


def view_components(imp: dict) -> list:
    """確認画面の表示・状態に関わる部品（イベントの outputs に使う）。"""
    names = ["summary", "box", "kinds_df", "conf_df", "desc_df", "label_df", "result_df", "insp", "plan", "msg"]
    comps = [imp[n] for n in names]
    for k in KINDS:
        comps += [imp[f"dest_row_{k}"], imp[f"dest_show_{k}"], imp[f"dest_{k}"]]
    return comps


def dest_text(kind: str, rel: str) -> str:
    return f"**{LABEL[kind]}の取り込み先**：`{rel or ROOT_DIR[kind] + '/（直下）'}`"


def render(repo: Repository, imp: dict, insp, plan, choices: Choices, *, done: bool, reset_dest: bool = False) -> dict:
    """検査・確認の結果を、画面の更新（{部品: 値}）にする。"""
    tables = {"kinds_df": R.kinds_rows(plan, choices), "conf_df": R.conflict_rows(plan, choices),
              "desc_df": R.desc_rows(plan, choices), "label_df": R.label_rows(plan, choices)}
    out = {imp["summary"]: summary_md(plan, done),
           imp["box"]: gr.update(visible=insp is not None and plan.error is None and not done),
           imp["result_df"]: gr.update(value=result_rows(repo, plan, done), visible=plan.error is None),
           imp["insp"]: None if done else insp, imp["plan"]: None if done else plan}
    for name, rows in tables.items():
        out[imp[name]] = gr.update(value=rows, visible=bool(rows) and not done)
    for k in KINDS:
        rel = "" if reset_dest else choices.dest.get(k, "")
        out[imp[f"dest_row_{k}"]] = gr.update(visible=k in plan.needs_dest and not done)
        out[imp[f"dest_show_{k}"]] = dest_text(k, rel)
        out[imp[f"dest_{k}"]] = rel
    return out


def wire_import(demo: gr.Blocks, repo: Repository, imp: dict, c: dict, OUT: list, pk_part, tree_part) -> None:
    """イベントの配線（gr.Blocks の中で、全部品を作り終えたあとに呼ぶ）。"""
    VIEW = view_components(imp)
    dest_states = [imp[f"dest_{k}"] for k in KINDS]
    TABLES = [imp["kinds_df"], imp["conf_df"], imp["desc_df"], imp["label_df"]]

    def choices_of(dests, kinds, conf, descs, labels) -> Choices:
        return choices_from_tables(dict(zip(KINDS, dests)), conf, kinds, descs, labels)

    # ---- 入口 ----
    def on_file(path):
        return ({"kind": "upload", "path": path} if path else None), ""

    def on_dir(request: gr.Request = None):
        why = folder_dialog_blocked(request)   # 画面で無効にしていても、ここでも確かめる
        if why:
            return gr.skip(), gr.skip(), why, gr.skip()
        try:
            path = pick_folder("", title="取り込むフォルダを選んでください（配下の .yaml を再帰的に対象にします）")
        except Exception as e:  # noqa: BLE001（ダイアログを開けない環境など）
            return gr.skip(), gr.skip(), f"⚠ {e}", gr.skip()
        if not path:
            return gr.skip(), gr.skip(), "", gr.skip()
        return {"kind": "folder", "path": path}, path, "", gr.update(value=None)

    def on_load(request: gr.Request = None):
        why = folder_dialog_blocked(request)
        return gr.update(interactive=not why), (f"ℹ {why}" if why else "")

    imp["file"].upload(on_file, [imp["file"]], [imp["source"], imp["dir_show"]], api_name="imp_file")
    imp["file"].clear(lambda: (None, ""), None, [imp["source"], imp["dir_show"]], api_name="imp_file_clear")
    imp["dir_btn"].click(on_dir, None, [imp["source"], imp["dir_show"], imp["dir_note"], imp["file"]], api_name="imp_dir")
    demo.load(on_load, None, [imp["dir_btn"], imp["dir_note"]], api_name="imp_load")   # PC 以外からの接続では、ボタンを無効にする

    # ---- 検査 ----
    def on_inspect(source):
        if not source or not source.get("path"):
            return {imp["msg"]: "⚠ アップロードするか、フォルダを選んでください"}
        batch = read_batch(Path(source["path"]))
        insp = inspect_batch(repo, batch)
        choices = Choices()
        plan = resolve(repo, insp, choices)
        out = render(repo, imp, insp, plan, choices, done=False, reset_dest=True)
        out[imp["msg"]] = ""
        return out

    imp["inspect_btn"].click(on_inspect, [imp["source"]], VIEW, api_name="imp_inspect")

    # ---- 確認の更新 ----
    def on_refresh(insp, *args):
        if insp is None:
            return {imp["msg"]: "⚠ 先に［検査］を押してください"}
        dests, tables = args[:len(KINDS)], args[len(KINDS):]
        choices = choices_of(dests, *tables)
        plan = resolve(repo, insp, choices)
        out = render(repo, imp, insp, plan, choices, done=False)
        out[imp["msg"]] = "確認を更新しました"
        return out

    imp["refresh_btn"].click(on_refresh, [imp["insp"]] + dest_states + TABLES, VIEW, api_name="imp_refresh")

    # ---- 書き込み ----
    def on_apply(insp, confirmed, st_v, *args):
        if insp is None:
            return {imp["msg"]: "⚠ 先に［検査］を押してください"}
        dests, tables = args[:len(KINDS)], args[len(KINDS):]
        choices = choices_of(dests, *tables)
        plan = apply_plan(repo, insp, choices, confirmed=confirmed)   # 書き込み直前に、索引を読み直して再確認する
        out = render(repo, imp, insp, plan, choices, done=True)
        out[imp["msg"]] = "📥 取り込みを実行しました。結果は下の表を確認してください（既存のファイルは上書きしていません）"
        return out | tree_part(st_v)

    imp["apply_btn"].click(on_apply, [imp["insp"], imp["plan"], c["st"]] + dest_states + TABLES,
                           VIEW + [c["section"], c["crumbs"], c["lst"]], api_name="imp_apply")

    def on_cancel():
        plan = resolve(repo, Inspection(), Choices())   # 空の計画
        out = render(repo, imp, None, plan, Choices(), done=False, reset_dest=True)
        out[imp["result_df"]] = gr.update(value=[], visible=False)
        out[imp["summary"]] = ""
        out[imp["msg"]] = "キャンセルしました（何も書き込んでいません）"
        return out

    imp["cancel_btn"].click(on_cancel, None, VIEW, api_name="imp_cancel")

    # ---- 取り込み先の選択（共通ピッカーのフォルダ選択モード）----
    def on_open_dest(kind, pk_v, cur):
        return pk_part(PK.open_picker(repo, pk_v, f"{PK.FOLDER_PREFIX}{kind}", cur or None))

    for k in KINDS:
        imp[f"dest_btn_{k}"].click(partial(on_open_dest, k), [c["pk"], imp[f"dest_{k}"]], OUT, api_name=f"imp_pick_{k}")

    def on_pk_here(pk_v):
        """［このフォルダを選ぶ］：ピッカーが今いるフォルダを、その種類の取り込み先にして閉じる。"""
        if not PK.is_folder_mode(pk_v):
            return gr.skip()
        kind = pk_v["target"].removeprefix(PK.FOLDER_PREFIX)
        rel = pk_v["cwd"] if pk_v["cwd"] != ROOT_DIR[kind] else ""   # ルート直下は「選ばない」と同じ
        out = pk_part(PK.close(pk_v))
        out[imp[f"dest_{kind}"]] = rel
        out[imp[f"dest_show_{kind}"]] = dest_text(kind, rel)
        return out

    c["pk_here"].click(on_pk_here, [c["pk"]], OUT + dest_states + [imp[f"dest_show_{k}"] for k in KINDS],
                       api_name="imp_pk_here")
