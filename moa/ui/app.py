"""シーン組み立て画面（10章）。ロジックは moa.ui.logic、コンパイルは moa.core、生成は moa.comfy にある。"""
from __future__ import annotations

import copy
import io
from functools import partial
from pathlib import Path
from typing import Callable

import gradio as gr
from PIL import Image, ImageDraw

from moa.comfy import (ComfyConfig, ComfyError, Generation, differences, effective_gen, iter_retry_anima,
                       iter_run, supports_anima_scale)
from moa.comfy.config import STAGE_LABEL
from moa.core import Repository
from moa.core.export import export_folder, export_scene
from moa.core.constants import SEED_MAX
from moa.core.gen import GEN_PARAMS
from moa.core.models import Issue
from moa.ui import logic as L
from moa.ui import exportblock as EB
from moa.ui import folderedit as FE
from moa.ui import quickedit as Q
from moa.ui import saveblock as SB
from moa.ui.dialogs import pick_folder
from moa.ui.style import DEFAULT_PROMPT_FONT_PX, JUMP_JS, TEXTBOX_ID, prompt_css


def build_ui(repo: Repository,
             comfy_loader: Callable[[], tuple[ComfyConfig | None, list[Issue]]] | None = None,
             prompt_font_px: int | float = DEFAULT_PROMPT_FONT_PX) -> gr.Blocks:
    comfy: dict = {"cfg": None, "issues": []}

    def reload_comfy() -> None:
        if comfy_loader is None:
            comfy.update(cfg=None, issues=[Issue("warning", "ComfyUI連携は設定されていません")])
        else:
            comfy["cfg"], comfy["issues"] = comfy_loader()

    reload_comfy()  # スライダーの初期値（ワークフローの値）を決めるため、組み立ての前に読む
    cfg0 = comfy["cfg"]

    def slider_kwargs(stage: str, param: str) -> dict:
        wf = cfg0.workflow_value(stage, param) if cfg0 and cfg0.has_sampler(stage) else None
        if param == "steps":
            lo, hi, step = 1, max(100, int(wf or 0)), 1
        elif param == "cfg":
            lo, hi, step = 0.1, max(20.0, float(wf or 0)), 0.1
        else:  # denoise
            lo, hi, step = 0.0, 1.0, 0.05
        return {"minimum": lo, "maximum": hi, "step": step, "value": wf if wf is not None else lo}

    with gr.Blocks(title="MOSS/Anima プロンプトビルダー", css=prompt_css(prompt_font_px),
                   head=f"<script>{JUMP_JS}</script>") as demo:
        sc = gr.State(L.new_sc())
        nav = gr.State(L.new_nav())
        gen = gr.State(None)  # 直近の生成結果のスナップショット（保存ボタンで Scene に記録する。再試行にも使う）
        gp = gr.State(L.new_gp())  # 画面で上書きした生成パラメータ（動かした項目だけ。保存で Scene.gen になる）
        sv = gr.State(SB.new_sv())  # 保存ブロックの状態（保存先フォルダ・一覧で選んだ Scene・確認待ち）。共通ピッカーの nav とは別
        qe = gr.State(Q.new_qe())  # クイック編集（10.1章）：編集した欄・差分。保存はしない
        fe = gr.State(FE.new_fe())  # フォルダ情報の編集の状態（10.4章）：一覧・選択・読み込み時の更新情報
        ex = gr.State(EB.new_ex())  # 書き出しブロックの状態（モード・フォルダ一括で表示中のフォルダ）。保存ブロックの sv とは別
        sd = gr.State(L.new_sd())  # シードの固定状態（13.2章）。固定中だけ、欄の値で生成する

        with gr.Row():
            # ---- 左：共有ブラウザ（常時表示。ボタンで選択先を切り替える）----
            with gr.Column(scale=2, min_width=320):
                target_md = gr.Markdown(L.target_label(L.new_nav()))
                crumbs_md = gr.Markdown()
                with gr.Row():
                    query_tb = gr.Textbox(label="名前検索（Enter か 🔍 で実行。空にして Enter でフォルダ表示に戻る）",
                                          placeholder="name・ID・tags で部分一致", scale=4)
                    search_btn = gr.Button("🔍", size="sm", scale=0, min_width=48)
                    clear_q_btn = gr.Button("✕", size="sm", scale=0, min_width=48)
                lst =gr.Radio(choices=[], value=None, label="一覧（クリックで選択／フォルダは移動）")
                browse_msg = gr.Markdown()
                with gr.Row():
                    upload = gr.UploadButton("YAMLファイルを指定", file_types=[".yaml", ".yml"], size="sm")
                    reload_btn = gr.Button("🔄 データ再読み込み", size="sm")

            # ---- 右：Scene 組み立て ----
            with gr.Column(scale=5):
                sel_md = gr.Markdown()
                with gr.Row():
                    pick_scene_btn = gr.Button("📂 Scene を開く", size="sm")
                    pick_action_btn = gr.Button("🎬 Action を選ぶ", size="sm")
                    pick_bg_btn = gr.Button("🏞 Background を選ぶ", size="sm")

                @gr.render(inputs=[sc])
                def render_slots(sc_v):
                    slots = L.slots_of(repo, sc_v)
                    if not slots:
                        gr.Markdown("_Action を選ぶと、スロットの数だけキャスト枠が表示されます。_")
                        return
                    with gr.Row():
                        for slot in slots:
                            with gr.Column(min_width=260):
                                gr.Markdown(f"#### スロット `{slot}`")
                                gr.Markdown(L.character_text(repo, sc_v, slot))
                                pick_c = gr.Button("キャラを選ぶ", size="sm")
                                choices, value = L.outfit_choices(repo, sc_v, slot)
                                outfit_rd = gr.Radio(choices=choices, value=value, label="服装")
                                pick_o = gr.Button("服装を選ぶ", size="sm")
                                pick_c.click(partial(on_pick, "char", slot), [nav], NAV_OUT)
                                pick_o.click(partial(on_pick, "outfit", slot), [nav], NAV_OUT)
                                outfit_rd.input(partial(on_outfit, slot), [sc, nav, outfit_rd, qe], FULL)

                neg_cb = gr.Checkbox(label="ネガティブを適用する（既定OFF。OFFでも合成結果は下に表示）", value=False)
                summary_md = gr.Markdown()
                gr.Markdown("_下の4つの欄は直接編集できます（クイック編集）。編集は生成にだけ使われ、保存されません。"
                            "フォーカスを外すか［差分を更新］で反映します。_")
                moss_tb = gr.Textbox(label="MOSS JSON", lines=16, max_lines=30, show_copy_button=True, interactive=True,
                                     elem_classes=["moa-prompt"], elem_id=TEXTBOX_ID.format("moss"))
                moss_df = gr.HTML(visible=False, elem_classes=["moa-prompt"])
                with gr.Row():
                    moss_st = gr.Markdown()
                    moss_rb = gr.Button("↩ コンパイル結果に戻す", size="sm", visible=False, scale=0, min_width=200)
                anima_tb = gr.Textbox(label="Animaプロンプト", lines=5, show_copy_button=True, interactive=True,
                                      elem_classes=["moa-prompt"], elem_id=TEXTBOX_ID.format("anima"))
                anima_df = gr.HTML(visible=False, elem_classes=["moa-prompt"])
                with gr.Row():
                    anima_st = gr.Markdown()
                    anima_rb = gr.Button("↩ コンパイル結果に戻す", size="sm", visible=False, scale=0, min_width=200)
                with gr.Row():
                    with gr.Column():
                        negm_tb = gr.Textbox(label="ネガティブ（MOSS用）", lines=3, show_copy_button=True, interactive=True,
                                             elem_classes=["moa-prompt"], elem_id=TEXTBOX_ID.format("negm"))
                        negm_df = gr.HTML(visible=False, elem_classes=["moa-prompt"])
                        negm_st = gr.Markdown()
                        negm_rb = gr.Button("↩ コンパイル結果に戻す", size="sm", visible=False)
                    with gr.Column():
                        nega_tb = gr.Textbox(label="ネガティブ（Anima用）", lines=3, show_copy_button=True, interactive=True,
                                             elem_classes=["moa-prompt"], elem_id=TEXTBOX_ID.format("nega"))
                        nega_df = gr.HTML(visible=False, elem_classes=["moa-prompt"])
                        nega_st = gr.Markdown()
                        nega_rb = gr.Button("↩ コンパイル結果に戻す", size="sm", visible=False)
                with gr.Accordion("クイック編集の差分（出どころ付き。コピーしてエディタで手直しする用）", open=True):
                    diff_btn = gr.Button("差分を更新", size="sm")
                    diff_tb = gr.Textbox(label="差分", lines=6, max_lines=30, show_copy_button=True, interactive=False,
                                         elem_classes=["moa-prompt"])
                issues_md = gr.Markdown()
                with gr.Accordion("データ読み込みの検証結果（data/ 全体）", open=False):
                    data_issues_md = gr.Markdown()

                gr.Markdown("---\n### 生成（ComfyUI）")
                comfy_md = gr.Markdown()
                with gr.Row():
                    copy_dir_tb = gr.Textbox(
                        label="画像のコピー先フォルダ", interactive=False, scale=4,
                        placeholder="（未指定：ComfyUI の output 内のパスを記録します）")
                    pick_dir_btn = gr.Button("📁 フォルダを選ぶ", scale=1)
                    clear_dir_btn = gr.Button("クリア", scale=1)
                gr.Markdown(
                    "#### 生成パラメータ\n動かした項目だけが Scene（`gen`）に保存されます。"
                    "動かしていない項目は、ワークフローの値が使われます。")
                items: dict = {}
                with gr.Row():
                    for stage in GEN_PARAMS:
                        with gr.Column(min_width=340):
                            gr.Markdown(f"**{STAGE_LABEL[stage]}**")
                            for param in GEN_PARAMS[stage]:
                                with gr.Row():
                                    sl = gr.Slider(label=L.PARAM_LABEL[param], interactive=False, scale=5,
                                                   **slider_kwargs(stage, param))
                                    src = gr.Markdown("")
                                    rb = gr.Button("↩ ワークフローの値に戻す", size="sm", interactive=False,
                                                   scale=2, min_width=120)
                                items[(stage, param)] = (sl, src, rb)
                            if stage == "anima":   # 画面だけの設定。Scene には保存しない（gp にも入れない）
                                scale_sl = gr.Slider(
                                    label="解像度倍率（画面だけ・保存しない）", minimum=0.1, maximum=2.0, step=0.05,
                                    value=1.0, interactive=False,
                                    info="canvas の縦横比を保ったまま Anima段の解像度を変える。16の倍数に丸めて引き伸ばす")
                                with gr.Row():
                                    scale_md = gr.Markdown("")
                                    scale_rb = gr.Button("↩ 1.0に戻す", size="sm", interactive=False,
                                                         scale=0, min_width=120)
                gr.Markdown("#### シード\n既定はランダム。生成すると、使ったシードが欄に出ます。"
                            "［固定］を押すと、欄の値で生成し続けます（もう一度押すとランダムに戻ります）。"
                            "保存済みのシードがある Scene を開くと、そのシードで固定されます。")
                with gr.Row():
                    seed_nb = gr.Number(label="シード", precision=0, minimum=0, maximum=SEED_MAX, scale=3)
                    seed_btn = gr.Button(L.seed_labels(L.new_sd())[0], scale=2, elem_classes=L.seed_btn_classes(L.new_sd()))
                seed_md = gr.Markdown(L.seed_labels(L.new_sd())[1])
                gen_btn = gr.Button("🎨 生成", variant="primary")
                retry_btn = gr.Button("🔁 Anima段だけ再試行", visible=False)
                gen_status = gr.Markdown()
                gallery = gr.Gallery(label="生成結果（MOSS段 → Anima段）", columns=2, height="auto",
                                     object_fit="contain", interactive=False)

                # 保存は「開く」とは別の操作。誤操作を避けるため、画面の一番下に専用のブロックを置く（10.2章）
                gr.Markdown("---\n### シーンを保存")
                sv_crumbs = gr.Markdown()
                with gr.Row():
                    folder_rd = gr.Radio(choices=[], value=None, label="保存先フォルダ（クリックで移動。scenes/ 配下）")
                    scene_rd = gr.Radio(choices=[], value=None,
                                        label="このフォルダの Scene（選ぶと id 欄に入ります。別名保存で上書きできるのは、ここで選んだ Scene だけ）")
                with gr.Row():
                    newdir_tb = gr.Textbox(label="新しいフォルダ名（上で開いているフォルダの中に作ります）", scale=3)
                    mkdir_btn = gr.Button("＋フォルダ作成", size="sm", scale=1)
                with gr.Row():
                    name_tb = gr.Textbox(label="Scene表示名（任意）", scale=2)
                    slug_tb = gr.Textbox(
                        label="別名で保存する Scene の id（半角英小文字・数字・_）",
                        info="初期値は「<元のid>_copy」（新規作成中は「<Action の id>__default」）。使われていれば番号を進めます",
                        scale=3)
                with gr.Row():
                    over_btn = gr.Button("💾 上書き保存", variant="secondary", interactive=False)
                    saveas_btn = gr.Button("💾 別名で保存", variant="secondary")
                over_md = gr.Markdown()
                save_msg = gr.Markdown()
                with gr.Row(visible=False) as confirm_row:   # 別名保存で、選んだ既存 Scene を上書きするときの確認
                    confirm_md = gr.Markdown()
                    confirm_ok_btn = gr.Button("上書きする", variant="stop", size="sm")
                    confirm_cancel_btn = gr.Button("キャンセル", size="sm")
                with gr.Row(visible=False) as ext_row:   # 外部変更の検知（EDITOR_SPEC 12章-4）
                    ext_reload_btn = gr.Button("再読み込み（編集を破棄）", size="sm")
                    ext_over_btn = gr.Button("外部の変更を捨てて上書き保存", variant="stop", size="sm")

                # 書き出し（zip）。保存とは別の操作で、保存済みのファイルの内容を書き出す（10.3章）
                gr.Markdown("---\n### シーンを書き出す（zip）")
                gr.Markdown("依存する Character / Outfit / Background / Action を集めて zip にします（MOA の YAML だけ）。"
                            "他の人のライブラリのエディタで取り込めます。")
                ex_mode = gr.Radio(choices=[(v, k) for k, v in EB.MODE_LABELS.items()], value="scene", label="書き出す範囲")
                with gr.Group(visible=True) as ex_scene_grp:
                    ex_scene_md = gr.Markdown()
                    ex_scene_btn = gr.Button("📦 このシーンを書き出す", variant="secondary", interactive=False)
                with gr.Group(visible=False) as ex_folder_grp:
                    ex_crumbs = gr.Markdown()
                    ex_folder_rd = gr.Radio(choices=[], value=None, label="書き出すフォルダ（クリックで移動。scenes/ 配下。"
                                            "対象は、そのフォルダの _folder.yaml の order に書かれた直下の Scene だけ）")
                    ex_preview = gr.Markdown()
                    ex_folder_btn = gr.Button("📦 このフォルダを書き出す", variant="secondary")
                    # フォルダ情報（_folder.yaml）の編集（10.4章）。order は並び順であり、書き出し・一括処理の対象の一覧でもある
                    with gr.Accordion("フォルダ情報を編集（_folder.yaml）", open=False):
                        gr.Markdown("上で選んでいるフォルダの表示名・説明・order を編集します。`_folder.yaml` が無ければ保存時に作ります。"
                                    "**order に入るのは ☑ の項目だけ**です（☐ は入らず、書き出しの対象になりません）。")
                        fe_name = gr.Textbox(label="表示名（name）")
                        fe_desc = gr.Textbox(label="説明（description）", lines=2)
                        fe_list = gr.Radio(choices=[], value=None,
                                           label="order（項目を選んで、↑↓で並べ替え／［含める・外す］で ☑・☐ を切り替え）")
                        with gr.Row():
                            fe_up_btn = gr.Button("↑ 上へ", size="sm")
                            fe_down_btn = gr.Button("↓ 下へ", size="sm")
                            fe_toggle_btn = gr.Button("☑／☐ order に含める・外す", size="sm")
                        fe_removed = gr.Markdown()
                        fe_save_btn = gr.Button("💾 フォルダ情報を保存", variant="secondary")
                        fe_msg = gr.Markdown()
                        with gr.Row(visible=False) as fe_ext_row:   # 外部変更の検知（EDITOR_SPEC 12章-4）
                            fe_ext_reload_btn = gr.Button("再読み込み（編集を破棄）", size="sm")
                            fe_ext_over_btn = gr.Button("外部の変更を捨てて上書き保存", variant="stop", size="sm")
                ex_strip_cb = gr.Checkbox(label="生成の記録を含めない（seed・images・gen・partial を取り除く）", value=True)
                ex_result = gr.Markdown()
                ex_file = gr.File(label="ダウンロード", interactive=False)

        NAV_OUT = [nav, lst, crumbs_md, target_md, browse_msg, query_tb]
        QE_VIEW = [moss_st, anima_st, negm_st, nega_st, moss_rb, anima_rb, negm_rb, nega_rb, diff_tb,
                   moss_df, anima_df, negm_df, nega_df]
        QE_OUT = [qe] + QE_VIEW
        QE_TB = dict(zip(Q.FIELDS, [moss_tb, anima_tb, negm_tb, nega_tb]))
        QE_RB = dict(zip(Q.FIELDS, [moss_rb, anima_rb, negm_rb, nega_rb]))
        FULL = [sc] + NAV_OUT + [sel_md, moss_tb, anima_tb, negm_tb, nega_tb, summary_md, issues_md, neg_cb,
                                 name_tb, slug_tb] + QE_OUT
        SV_VIEW = [sv_crumbs, folder_rd, scene_rd, over_btn, over_md, confirm_row, confirm_md]
        SV_IN = [sc, sv]
        SV_OUT = [sv] + SV_VIEW + [slug_tb]
        GP_OUT = [gp] + [c for key in L.ITEMS for c in items[key]] + [scale_sl, scale_md, scale_rb]
        GP_IN = [sc, gp, scale_sl]
        GEN_OUT = [gen_status, gallery, gen, retry_btn]
        EX_VIEW = [ex_scene_md, ex_scene_btn, ex_crumbs, ex_folder_rd, ex_preview]
        FE_VIEW = [fe_name, fe_desc, fe_list, fe_removed]
        EX_IN = [sc, ex, fe, fe_name, fe_desc]
        EX_OUT = [ex] + EX_VIEW + [fe] + FE_VIEW
        SD_OUT = [sd, seed_nb, seed_btn, seed_md]
        SD_IN = [sc, sd]

        # ---- 出力の組み立て ----
        def data_issues_text() -> str:
            n_err = sum(i.level == "error" for i in repo.issues)
            n_warn = len(repo.issues) - n_err
            return f"**エラー {n_err} / 警告 {n_warn}**\n\n" + L.issues_text(repo.issues)

        def nav_part(nav_v, msg):
            choices, crumbs = L.listing(repo, nav_v)
            return [nav_v, gr.update(choices=choices, value=None), crumbs, L.target_label(nav_v),
                    gr.skip() if msg is None else msg, nav_v.get("query", "")]

        def nav_out(nav_v, msg=None):
            return nav_part(nav_v, msg)

        def qe_part(qe_v, apply):
            html = Q.field_diff_html(qe_v)   # 編集した欄のすぐ下の色付き差分。編集がなければ出さない
            return [qe_v] + Q.statuses(qe_v, apply) + [gr.update(visible=f in qe_v["edits"]) for f in Q.FIELDS] \
                + [Q.diff_view(qe_v)] + [gr.update(value=html[f], visible=bool(html[f])) for f in Q.FIELDS]

        def full(sc_old, sc_new, nav_v, qe_v, msg=None, sc_changed=True):
            if not sc_changed:  # フォルダ移動だけのときは、枠の描き直しとコンパイルを避ける
                return [gr.skip()] + nav_part(nav_v, msg) + [gr.skip()] * (len(FULL) - 1 - len(NAV_OUT))
            r = L.compile_view(repo, sc_new)
            apply = sc_new["apply_negative"]
            note = "" if apply else "（未適用・参考表示）"
            # クイック編集：再コンパイルで結果が変わったときだけ編集をリセットする（破棄前の差分は自動で残す）
            qe2, _ = Q.refresh(qe_v, r, (sc_new["open_seq"], sc_new["action_id"], sc_new["background_id"]),
                               sc_new["scene_id"])
            shown = Q.shown_values(qe2)
            moss, anima, negm, nega = (shown[f] for f in Q.FIELDS)
            issues = L.issues_text(r.issues) if r is not None else ""
            opened = sc_old["open_seq"] != sc_new["open_seq"]
            return [sc_new] + nav_part(nav_v, msg) + [
                L.selection_text(repo, sc_new), moss, anima,
                gr.update(value=negm, label=f"ネガティブ（MOSS用）{note}"),
                gr.update(value=nega, label=f"ネガティブ（Anima用）{note}"),
                L.summary_text(r), issues, apply,
                gr.update(value=sc_new["scene_name"]) if opened else gr.skip(),
                gr.update(value="") if opened else gr.skip(),
            ] + qe_part(qe2, apply)

        # ---- イベント ----
        def on_pick(target, slot, nav_v):
            return nav_out(L.set_target(nav_v, target, slot), "")

        def on_search(q, nav_v):
            return nav_out(L.set_query(nav_v, q), "")

        def on_clear_query(nav_v):
            return nav_out(L.set_query(nav_v, ""), "")

        def on_outfit(slot, sc_v, nav_v, value, qe_v):
            return full(sc_v, L.set_outfit(sc_v, slot, value), nav_v, qe_v)

        def on_browse(choice, sc_v, nav_v, qe_v):
            sc2, nav2, msg = L.browse(repo, sc_v, nav_v, choice)
            return full(sc_v, sc2, nav2, qe_v, msg, sc_changed=(sc2 != sc_v))

        def on_upload(path, sc_v, nav_v, qe_v):
            sc2, nav2, msg = L.from_upload(repo, sc_v, nav_v, path)
            return full(sc_v, sc2, nav2, qe_v, msg)

        def on_negative(flag, sc_v, nav_v, qe_v):
            sc2 = copy.deepcopy(sc_v)
            sc2["apply_negative"] = bool(flag)
            return full(sc_v, sc2, nav_v, qe_v)

        def comfy_parts():
            cfg = comfy["cfg"]
            head = (f"✅ 接続先 `{cfg.url}` ／ ワークフロー `{cfg.workflow_path.name}`" if cfg
                    else "⚠ ComfyUI連携は無効です")
            detail = L.issues_text(comfy["issues"]) if comfy["issues"] else ""
            return [head + ("\n\n" + detail if detail else ""), gr.update(interactive=cfg is not None)]

        def on_reload(sc_v, nav_v, qe_v):
            repo.reload()
            reload_comfy()
            return full(sc_v, sc_v, nav_v, qe_v, "🔄 再読み込みしました") + [data_issues_text()] + comfy_parts()

        def on_load(sc_v, nav_v, qe_v):
            reload_comfy()
            return full(sc_v, sc_v, nav_v, qe_v, "") + [data_issues_text()] + comfy_parts()

        # ---- 保存（10.2章）----
        def sv_parts(sv_v, sc_v):
            """保存ブロックの表示（パンくず・フォルダ一覧・Scene 一覧・上書き先・確認行）。"""
            crumbs, folders, scenes = SB.listing(repo, sv_v)
            pending = sv_v["pending"]
            confirm = bool(pending and pending["kind"] == "confirm")
            return [f"保存先フォルダ：`{repo.rel(SB.cwd_path(repo, sv_v))}/`（{crumbs}）",
                    gr.update(choices=folders, value=None),
                    gr.update(choices=scenes, value=SB.selected_value(repo, sv_v)),
                    gr.update(interactive=SB.overwrite_target(repo, sc_v) is not None),
                    SB.overwrite_label(repo, sc_v),
                    gr.update(visible=confirm), SB.confirm_text(pending)]

        def refresh_save(sc_v, sv_v):
            """Scene を開き直した／Action を選び直したときだけ、フォルダと id の初期値を作り直す。"""
            sv2, new_id = SB.sync(repo, sv_v, sc_v)
            return [sv2] + sv_parts(sv2, sc_v) + [gr.update(value=new_id) if new_id is not None else gr.skip()]

        def on_sv_move(choice, sc_v, sv_v):
            sv2 = SB.move(repo, sv_v, choice)
            return [sv2] + sv_parts(sv2, sc_v)

        def on_sv_select(choice, sc_v, sv_v):
            sv2, sid = SB.select(repo, sv_v, choice)
            return [sv2] + sv_parts(sv2, sc_v) + [gr.update(value=sid) if sid else gr.skip()]

        def on_mkdir(name, sc_v, sv_v):
            msg = SB.make_folder(repo, sv_v, name)
            return sv_parts(sv_v, sc_v) + [msg, gr.update(value="") if msg.startswith("📁") else gr.skip()]

        def effective_of(gp_v):
            cfg = comfy["cfg"]
            return effective_gen(cfg, gp_v["gen"]) if cfg is not None and cfg.samplers else None

        def no_save(msg, sv_v):
            """保存せずにメッセージだけ出す（Scene の表示は変えない）。"""
            return [gr.skip()] * len(FULL) + [msg, gr.skip(), gr.update(visible=False), sv_v]

        def run_save(target, force, sc_v, nav_v, sv_v, name, gen_v, gp_v, qe_v):
            sc2, msg = L.save(repo, sc_v, target, name, gen_v, gen_params=gp_v["gen"],
                              effective=effective_of(gp_v), force=force)
            external = msg.startswith(L.EXTERNAL_PREFIX)
            sv2 = SB.set_pending(sv_v, SB.pack(repo, target, "external") if external else None)
            if msg.startswith("💾"):   # 保存できたら、そのフォルダを「最後に保存したフォルダ」にする（次の初期フォルダ）
                sv2 = SB.note_saved(repo, sv2, repo.path_of("scene", sc2["scene_id"]))
            return full(sc_v, sc2, nav_v, qe_v) + [msg, data_issues_text(), gr.update(visible=external), sv2]

        def on_save_over(sc_v, nav_v, sv_v, slug, name, gen_v, gp_v, qe_v):
            target = SB.overwrite_target(repo, sc_v)
            if target is None:
                return no_save("⚠ 上書き保存できるのは、Scene を開いているときだけです（新規作成中は「別名で保存」を使ってください）", sv_v)
            return run_save(target, False, sc_v, nav_v, sv_v, name, gen_v, gp_v, qe_v)

        def on_save_as(sc_v, nav_v, sv_v, slug, name, gen_v, gp_v, qe_v):
            kind, where, sid = SB.plan_saveas(repo, sv_v, slug)
            if kind == "error":
                return no_save(where, SB.set_pending(sv_v, None))
            if kind == "confirm":
                pending = SB.pack(repo, L.SaveTarget(sid, path=where), "confirm")
                return no_save("⚠ 上書きの確認が必要です。下の［上書きする］を押すと保存します", SB.set_pending(sv_v, pending))
            return run_save(L.SaveTarget(sid, folder=where), False, sc_v, nav_v, sv_v, name, gen_v, gp_v, qe_v)

        def on_confirm(sc_v, nav_v, sv_v, slug, name, gen_v, gp_v, qe_v):
            pending = sv_v["pending"]
            if not pending or pending["kind"] != "confirm":
                return no_save("⚠ 確認待ちの保存がありません", sv_v)
            return run_save(SB.unpack(repo, pending), False, sc_v, nav_v, sv_v, name, gen_v, gp_v, qe_v)

        def on_cancel(sv_v):
            return no_save("キャンセルしました。保存していません", SB.set_pending(sv_v, None))

        def on_ext_over(sc_v, nav_v, sv_v, slug, name, gen_v, gp_v, qe_v):
            pending = sv_v["pending"]
            if not pending or pending["kind"] != "external":
                return no_save("⚠ 外部変更で止まった保存がありません", sv_v)
            return run_save(SB.unpack(repo, pending), True, sc_v, nav_v, sv_v, name, gen_v, gp_v, qe_v)

        def on_ext_reload(sc_v, nav_v, sv_v, qe_v):
            target = SB.unpack(repo, sv_v["pending"])
            sv2 = SB.set_pending(sv_v, None)
            if target is not None and target.scene_id == sc_v["scene_id"]:   # 開いている Scene を読み直す
                sc2, nav2, msg = L.reopen_scene(repo, sc_v, nav_v)
                return full(sc_v, sc2, nav2, qe_v, msg) + ["", gr.update(visible=False), sv2]
            repo.reload()
            return [gr.skip()] * len(FULL) + ["🔄 読み直しました。もう一度保存してください", gr.update(visible=False), sv2]

        # ---- 書き出し（10.3章）----
        def ex_parts(ex_v, sc_v):
            crumbs, folders, preview = EB.folder_view(repo, ex_v)
            return [EB.scene_label(repo, sc_v), gr.update(interactive=EB.scene_entry(repo, sc_v) is not None),
                    crumbs, gr.update(choices=folders, value=None), preview]

        def fe_view(fe_v):
            """フォルダ情報の編集欄の表示 [fe] + FE_VIEW。"""
            return [fe_v, fe_v["name"], fe_v["description"], gr.update(choices=FE.labels(fe_v), value=fe_v["selected"]),
                    FE.removed_text(fe_v)]

        def fe_parts(ex_v, fe_v, name, desc):
            """対象フォルダが変わった・編集していないときは、ディスクの今の内容に読み直す。同じフォルダで編集中ならそのまま残す。"""
            folder = SB.cwd_path(repo, ex_v)
            same = fe_v["folder"] == repo.rel(folder)
            if same and FE.dirty(fe_v, name, desc):
                return [fe_v] + [gr.skip()] * len(FE_VIEW)
            return fe_view(FE.open_folder(repo, folder, fe_v["selected"] if same else None))

        def refresh_export(sc_v, ex_v, fe_v, fe_name_v, fe_desc_v):
            """Scene を開いた・保存した・読み直したあとに、押せるかどうかと対象の表示を作り直す。"""
            ex2 = EB.sync(repo, ex_v, sc_v)
            return [ex2] + ex_parts(ex2, sc_v) + fe_parts(ex2, fe_v, fe_name_v, fe_desc_v)

        def on_ex_mode(mode, sc_v, ex_v, fe_v, fe_name_v, fe_desc_v):
            ex2 = EB.set_mode(ex_v, mode)
            return ([ex2, gr.update(visible=mode != "folder"), gr.update(visible=mode == "folder")] + ex_parts(ex2, sc_v)
                    + fe_parts(ex2, fe_v, fe_name_v, fe_desc_v))

        def on_ex_move(choice, sc_v, ex_v, fe_v, fe_name_v, fe_desc_v):
            ex2 = EB.move(repo, ex_v, choice)
            return [ex2] + ex_parts(ex2, sc_v) + fe_parts(ex2, fe_v, fe_name_v, fe_desc_v)

        # ---- フォルダ情報の編集（10.4章）----
        def on_fe_select(fe_v, value):
            return FE.select(fe_v, value)

        def on_fe_edit(action, fe_v, value):
            fe2 = FE.select(fe_v, value)
            fe2, msg = FE.toggle(fe2) if action == "toggle" else FE.move(fe2, action)
            return [fe2, gr.update(choices=FE.labels(fe2), value=fe2["selected"]), FE.removed_text(fe2), msg]

        def on_fe_save(force, fe_v, name, desc, nav_v, sc_v, sv_v, ex_v):
            fe2, msg, external = FE.save(repo, fe_v, name, desc, force=force)
            tail = [msg, gr.update(visible=external)]
            if not msg.startswith("💾"):   # 保存していない（エラー・外部変更）：表示はそのまま
                return [fe_v] + [gr.skip()] * len(FE_VIEW) + tail + [gr.skip()] * (len(NAV_OUT) + len(SV_VIEW) + len(EX_VIEW))
            # 保存したら、ピッカーの表示名・並び順、保存ブロックの一覧、書き出しの対象件数をその場で更新する
            return fe_view(fe2) + tail + nav_part(nav_v, None) + sv_parts(sv_v, sc_v) + ex_parts(ex_v, sc_v)

        def on_fe_ext_reload(ex_v):
            repo.reload()
            return fe_view(FE.open_folder(repo, SB.cwd_path(repo, ex_v))) + ["🔄 読み直しました（編集を破棄）", gr.update(visible=False)]

        def on_export_scene(sc_v, gp_v, name, strip):
            if EB.scene_entry(repo, sc_v) is None:
                return ["⚠ 保存済みの Scene を開いているときだけ書き出せます（新規作成中は、先に保存してください）", None]
            changed = EB.screen_changed(repo, sc_v, gp_v["gen"], name)
            res = export_scene(repo.root, sc_v["scene_id"], bool(strip))
            return [EB.result_text(res, changed), EB.write_zip(res)]

        def on_export_folder(ex_v, strip):
            res = export_folder(repo.root, SB.cwd_path(repo, ex_v), bool(strip))
            return [EB.result_text(res), EB.write_zip(res)]

        # ---- クイック編集（10.1章）----
        def on_qe_commit(qe_v, sc_v, moss, anima, negm, nega):
            """欄のフォーカスが外れたとき／［差分を更新］：欄の今の文字列を取り込んで、差分を作り直す。"""
            qe2 = Q.set_edits(qe_v, dict(zip(Q.FIELDS, [moss, anima, negm, nega])))
            return qe_part(qe2, sc_v["apply_negative"])

        def on_qe_reset(field, qe_v, sc_v):
            qe2 = Q.reset_field(qe_v, field)
            return qe_part(qe2, sc_v["apply_negative"]) + [Q.base_values(qe2["result"])[field]]

        # ---- 生成パラメータ（スライダー）----
        def gp_view(gp_v):
            cfg = comfy["cfg"]
            out = []
            for stage, param in L.ITEMS:
                st = L.param_state(cfg, gp_v, stage, param)
                out.append(gr.update(value=st["value"], interactive=True) if st["enabled"]
                           else gr.update(interactive=False))
                out.append(L.source_text(st))
                out.append(gr.update(interactive=st["overridden"]))
            return out

        def scale_view(sc_v, scale):
            """解像度倍率の表示（操作できるか・サイズの文言・戻すボタン）。canvas が変わったときにも呼ぶ。"""
            cfg = comfy["cfg"]
            usable = cfg is not None and supports_anima_scale(cfg)
            r = L.compile_view(repo, sc_v)
            return [gr.update(interactive=usable), L.scale_text(r.canvas if r else None, scale, usable),
                    gr.update(interactive=usable and scale != 1.0)]

        def refresh_gp(sc_v, gp_v, scale):
            """Scene を開いたら、その Scene.gen をスライダーに反映する（倍率のサイズ表示も更新する）。"""
            gp2 = L.sync_gp(sc_v, gp_v)
            return [gp2] + gp_view(gp2) + scale_view(sc_v, scale)

        # ---- シード固定（13.2章）----
        def sd_view(sd_v, keep_value=False):
            label, text = L.seed_labels(sd_v)
            btn = gr.update(value=label, elem_classes=L.seed_btn_classes(sd_v))   # 「シードを固定」の文言のときだけオレンジ
            return [sd_v, gr.skip() if keep_value else sd_v["value"], btn, text]

        def refresh_sd(sc_v, sd_v):
            """Scene を開いたら、保存されていたシードがあれば固定ON、なければランダムに戻す。"""
            sd2 = L.sync_sd(sc_v, sd_v)
            return sd_view(sd2, keep_value=(sd2 is sd_v))

        def on_seed_toggle(typed, sd_v):
            sd2, warn = L.toggle_sd(sd_v, typed)
            out = sd_view(sd2, keep_value=True)
            if warn:
                out[3] = warn
            return out

        def on_seed_after_generate(sd_v, gen_v):
            sd2 = L.after_generate(sd_v, gen_v)
            return sd_view(sd2, keep_value=(sd2 is sd_v))

        def on_scale_release(sc_v, scale):
            return scale_view(sc_v, scale)[1:]

        def on_scale_reset(sc_v):
            return [1.0] + scale_view(sc_v, 1.0)[1:]

        def on_release(stage, param, value, gp_v):
            gp2 = L.set_override(gp_v, stage, param, value)
            return [gp2, L.source_text(L.param_state(comfy["cfg"], gp2, stage, param)), gr.update(interactive=True)]

        def on_reset(stage, param, gp_v):
            gp2 = L.clear_override(gp_v, stage, param)
            st = L.param_state(comfy["cfg"], gp2, stage, param)
            return [gp2, gr.update(value=st["value"]), L.source_text(st), gr.update(interactive=False)]

        def on_pick_dir(current):
            try:
                path = pick_folder(current or "")
            except Exception as e:
                return gr.skip(), f"⚠ {e}"
            return (path, gr.skip()) if path else (gr.skip(), gr.skip())

        # ---- 生成（13章）----
        def gallery_items(done: Generation):
            shots = [(Image.open(io.BytesIO(i.data)), f"{STAGE_LABEL[i.stage]}：{Path(i.record_path).name}")
                     for i in done.images]
            if done.failed_stage == "anima":  # Anima 枠は「失敗」と表示する
                tile = Image.new("RGB", (256, 256), "#4a1d1d")
                ImageDraw.Draw(tile).text((88, 120), "FAILED", fill="white")
                shots.append((tile, "Anima段：失敗"))
            return shots

        def finished(done: Generation):
            """生成（または再試行）の結果を、画面の出力にする。"""
            paths = "\n".join(f"- {STAGE_LABEL[i.stage]}：`{i.record_path}`" for i in done.images)
            parts = ([L.gen_summary(done.gen)] if done.gen else []) \
                + ([f"Anima解像度 {done.anima_size[0]}×{done.anima_size[1]}"] if done.anima_size else [])
            used = f"\n\n使った値：{' ／ '.join(parts)}" if parts else ""
            if done.complete:
                status = (f"✅ 完了　シード `{done.seed}`（MOSS段・Anima段で共通）{used}\n\n{paths}\n\n"
                          "※ 保存ボタンを押すと、シードと画像のパスが Scene に記録されます")
                return [status, gallery_items(done), done.snapshot(), gr.update(visible=False)]
            if done.failed_stage == "anima":
                status = (f"⚠ **Anima段が失敗しました**（MOSS段は成功）：{done.error}\n\n"
                          f"MOSS画像は残してあります。「Anima段だけ再試行」で、同じシード `{done.seed}` のまま Anima段だけを再送できます。\n\n"
                          f"{paths}{used}\n\n※ このまま保存すると、MOSS段だけの部分結果として記録されます（`compiled.partial: true`）")
                return [status, gallery_items(done), done.snapshot(), gr.update(visible=True)]
            status = (f"❌ **MOSS段が失敗しました**：{done.error}\n\n画像は記録されません。選んだシードは破棄し、"
                      "再生成では新しいシードを使います")
            return [status, [], None, gr.update(visible=False)]

        def stream(events, quick=None):
            """iter_run / iter_retry_anima のイベントを、画面の更新に変える。quick＝クイック編集した生成の記録。"""
            skip = gr.skip()
            done = None
            try:
                for ev in events:
                    if isinstance(ev, Generation):
                        done = ev
                    else:
                        yield [f"⏳ {ev}", skip, skip, skip]
            except ComfyError as e:
                yield [f"❌ {e}", skip, skip, skip]
                return
            except OSError as e:
                yield [f"❌ 画像のコピーに失敗しました：{e}", skip, skip, skip]
                return
            out = finished(done)
            if quick and out[2] is not None:   # 試し打ち：保存しても compiled に記録しない。再試行で最初の文字列を使う
                out[2]["quick_edit"] = quick
                out[0] += ("\n\n✏ クイック編集した文字列での生成です（" + "、".join(Q.FIELD_LABEL[f] for f in quick["fields"])
                           + "）。試し打ちなので、保存してもシードと画像は記録されません")
            yield out

        def on_generate(sc_v, copy_dir, gp_v, scale, sd_v, typed_seed, moss, anima, negm, nega):
            """生成を実行し、経過を逐次表示する。YAMLへの記録は保存ボタンで行う。"""
            skip = gr.skip()
            try:   # 固定中は欄の値、そうでなければ None（ランダム）
                seed = L.seed_for_run(sd_v, typed_seed)
            except ValueError as e:
                yield [f"❌ {e}", skip, skip, skip]
                return
            cfg = comfy["cfg"]
            if cfg is None:
                yield ["⚠ ComfyUI連携の設定が無効です（上の設定状態を確認してください）", skip, skip, skip]
                return
            result = L.compile_view(repo, sc_v)
            if result is None:
                yield ["⚠ Scene を開くか、Action を選んでください", skip, skip, skip]
                return
            if not result.ok:
                yield ["❌ 検証エラーがあるため生成できません。\n\n" + L.issues_text(result.issues), skip, skip, skip]
                return
            try:   # 表示中の文字列を送る（送信時に差し替える）。MOSS の JSON が壊れていたら止める
                result_sent, sent = Q.apply_quick_edit(result, dict(zip(Q.FIELDS, [moss, anima, negm, nega])))
            except Q.QuickEditError as e:
                yield [f"❌ {e}", skip, skip, skip]
                return
            yield from stream(iter_run(cfg, result_sent, Path(copy_dir) if copy_dir else None, seed=seed,
                                       gen=gp_v["gen"], anima_scale=scale),
                              quick=Q.quick_info(result, sent))

        def on_retry(sc_v, copy_dir, gp_v, scale, gen_v):
            """MOSS段が成功して Anima段が失敗したときだけ、Anima段のみを同じシードで再送する。"""
            skip = gr.skip()
            cfg = comfy["cfg"]
            if cfg is None or not gen_v or gen_v.get("failed_stage") != "anima":
                yield ["⚠ 再試行できるのは、MOSS段が成功して Anima段が失敗した生成だけです", skip, skip, skip]
                return
            result = L.compile_view(repo, sc_v)
            if result is None or not result.ok:
                yield ["❌ 検証エラーがあるため再試行できません", skip, skip, skip]
                return
            changed = differences(gen_v, result, effective_of(gp_v))
            if changed:
                yield ["⚠ 最初の生成から次の項目が変わっているため、Anima段だけの再試行はできません："
                       + "、".join(changed) + "\n\n全体を「生成」し直してください", skip, skip, skip]
                return
            yield from stream(iter_retry_anima(cfg, Q.retry_result(result, gen_v), gen_v,
                                               Path(copy_dir) if copy_dir else None, gen=gp_v["gen"],
                                               anima_scale=scale),
                              quick=gen_v.get("quick_edit"))

        # ---- 配線 ----
        pick_scene_btn.click(partial(on_pick, "scene", None), [nav], NAV_OUT, api_name="pick_scene")
        pick_action_btn.click(partial(on_pick, "action", None), [nav], NAV_OUT, api_name="pick_action")
        pick_bg_btn.click(partial(on_pick, "background", None), [nav], NAV_OUT, api_name="pick_background")
        query_tb.submit(on_search, [query_tb, nav], NAV_OUT, api_name="search")
        search_btn.click(on_search, [query_tb, nav], NAV_OUT, api_name="search_btn")
        clear_q_btn.click(on_clear_query, [nav], NAV_OUT, api_name="clear_query")
        lst.input(on_browse, [lst, sc, nav, qe], FULL, api_name="browse").then(
            refresh_gp, GP_IN, GP_OUT, api_name="gp_after_browse").then(
            refresh_sd, SD_IN, SD_OUT, api_name="sd_after_browse").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_browse").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_browse")
        upload.upload(on_upload, [upload, sc, nav, qe], FULL, api_name="upload").then(
            refresh_gp, GP_IN, GP_OUT, api_name="gp_after_upload").then(
            refresh_sd, SD_IN, SD_OUT, api_name="sd_after_upload").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_upload").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_upload")
        neg_cb.input(on_negative, [neg_cb, sc, nav, qe], FULL, api_name="negative")
        COMFY_OUT = [data_issues_md, comfy_md, gen_btn]
        reload_btn.click(on_reload, [sc, nav, qe], FULL + COMFY_OUT, api_name="reload").then(
            refresh_gp, GP_IN, GP_OUT, api_name="gp_after_reload").then(
            refresh_sd, SD_IN, SD_OUT, api_name="sd_after_reload").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_reload").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_reload")
        SAVE_IN = [sc, nav, sv, slug_tb, name_tb, gen, gp, qe]
        SAVE_OUT = FULL + [save_msg, data_issues_md, ext_row, sv]
        over_btn.click(on_save_over, SAVE_IN, SAVE_OUT, api_name="save_overwrite").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_overwrite").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_overwrite")
        saveas_btn.click(on_save_as, SAVE_IN, SAVE_OUT, api_name="save_as").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_save_as").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_save_as")
        confirm_ok_btn.click(on_confirm, SAVE_IN, SAVE_OUT, api_name="save_confirm").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_confirm").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_confirm")
        confirm_cancel_btn.click(on_cancel, [sv], SAVE_OUT, api_name="save_cancel").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_cancel").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_cancel")
        ext_over_btn.click(on_ext_over, SAVE_IN, SAVE_OUT, api_name="save_ext_overwrite").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_ext_overwrite").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_ext_overwrite")
        ext_reload_btn.click(on_ext_reload, [sc, nav, sv, qe], FULL + [save_msg, ext_row, sv],
                             api_name="ext_reload").then(
            refresh_gp, GP_IN, GP_OUT, api_name="gp_after_ext_reload").then(
            refresh_sd, SD_IN, SD_OUT, api_name="sd_after_ext_reload").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_ext_reload").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_ext_reload")
        folder_rd.input(on_sv_move, [folder_rd, sc, sv], [sv] + SV_VIEW, api_name="sv_move")
        scene_rd.input(on_sv_select, [scene_rd, sc, sv], SV_OUT, api_name="sv_select")
        ex_mode.input(on_ex_mode, [ex_mode, sc, ex, fe, fe_name, fe_desc],
                      [ex, ex_scene_grp, ex_folder_grp] + EX_VIEW + [fe] + FE_VIEW, api_name="ex_mode")
        ex_folder_rd.input(on_ex_move, [ex_folder_rd, sc, ex, fe, fe_name, fe_desc], EX_OUT, api_name="ex_move")
        fe_list.input(on_fe_select, [fe, fe_list], [fe], api_name="fe_select")
        FE_EDIT_OUT = [fe, fe_list, fe_removed, fe_msg]
        fe_up_btn.click(partial(on_fe_edit, "up"), [fe, fe_list], FE_EDIT_OUT, api_name="fe_up")
        fe_down_btn.click(partial(on_fe_edit, "down"), [fe, fe_list], FE_EDIT_OUT, api_name="fe_down")
        fe_toggle_btn.click(partial(on_fe_edit, "toggle"), [fe, fe_list], FE_EDIT_OUT, api_name="fe_toggle")
        FE_SAVE_IN = [fe, fe_name, fe_desc, nav, sc, sv, ex]
        FE_SAVE_OUT = [fe] + FE_VIEW + [fe_msg, fe_ext_row] + NAV_OUT + SV_VIEW + EX_VIEW
        fe_save_btn.click(partial(on_fe_save, False), FE_SAVE_IN, FE_SAVE_OUT, api_name="fe_save")
        fe_ext_over_btn.click(partial(on_fe_save, True), FE_SAVE_IN, FE_SAVE_OUT, api_name="fe_save_force")
        fe_ext_reload_btn.click(on_fe_ext_reload, [ex], [fe] + FE_VIEW + [fe_msg, fe_ext_row], api_name="fe_ext_reload")
        ex_scene_btn.click(on_export_scene, [sc, gp, name_tb, ex_strip_cb], [ex_result, ex_file], api_name="export_scene")
        ex_folder_btn.click(on_export_folder, [ex, ex_strip_cb], [ex_result, ex_file], api_name="export_folder")
        mkdir_btn.click(on_mkdir, [newdir_tb, sc, sv], SV_VIEW + [save_msg, newdir_tb], api_name="mkdir")
        demo.load(on_load, [sc, nav, qe], FULL + COMFY_OUT, api_name="init").then(
            refresh_gp, GP_IN, GP_OUT, api_name="gp_after_init").then(
            refresh_sd, SD_IN, SD_OUT, api_name="sd_after_init").then(
            refresh_save, SV_IN, SV_OUT, api_name="sv_after_init").then(
            refresh_export, EX_IN, EX_OUT, api_name="ex_after_init")
        for (stage, param), (sl, src, rb) in items.items():
            sl.release(partial(on_release, stage, param), [sl, gp], [gp, src, rb],
                       api_name=f"gp_set_{stage}_{param}")
            rb.click(partial(on_reset, stage, param), [gp], [gp, sl, src, rb],
                     api_name=f"gp_reset_{stage}_{param}")
        scale_sl.release(on_scale_release, [sc, scale_sl], [scale_md, scale_rb], api_name="scale_set")
        scale_rb.click(on_scale_reset, [sc], [scale_sl, scale_md, scale_rb], api_name="scale_reset")
        pick_dir_btn.click(on_pick_dir, [copy_dir_tb], [copy_dir_tb, gen_status], api_name="pick_dir")
        clear_dir_btn.click(lambda: "", None, [copy_dir_tb], api_name="clear_dir")
        gen_btn.click(on_generate, [sc, copy_dir_tb, gp, scale_sl, sd, seed_nb, moss_tb, anima_tb, negm_tb, nega_tb],
                      GEN_OUT, api_name="generate").then(
            on_seed_after_generate, [sd, gen], SD_OUT, api_name="sd_after_generate")
        seed_btn.click(on_seed_toggle, [seed_nb, sd], SD_OUT, api_name="seed_toggle")
        QE_IN = [qe, sc, moss_tb, anima_tb, negm_tb, nega_tb]
        for f, tb in QE_TB.items():
            tb.blur(on_qe_commit, QE_IN, QE_OUT, api_name=f"qe_blur_{f}")
        diff_btn.click(on_qe_commit, QE_IN, QE_OUT, api_name="qe_diff")
        for f, rb in QE_RB.items():
            rb.click(partial(on_qe_reset, f), [qe, sc], QE_OUT + [QE_TB[f]], api_name=f"qe_reset_{f}")
        retry_btn.click(on_retry, [sc, copy_dir_tb, gp, scale_sl, gen], GEN_OUT, api_name="retry")

    return demo
