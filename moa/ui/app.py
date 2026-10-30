"""シーン組み立て画面（10章）。ロジックは moa.ui.logic、コンパイルは moa.core にある。"""
from __future__ import annotations

import copy
from functools import partial

import gradio as gr

from moa.core import Repository
from moa.ui import logic as L


def build_ui(repo: Repository) -> gr.Blocks:
    with gr.Blocks(title="MOSS/Anima プロンプトビルダー") as demo:
        sc = gr.State(L.new_sc())
        nav = gr.State(L.new_nav())

        with gr.Row():
            # ---- 左：共有ブラウザ（常時表示。キャスト枠のボタンで選択先を切り替える）----
            with gr.Column(scale=2, min_width=320):
                target_md = gr.Markdown(L.target_label(L.new_nav()))
                crumbs_md = gr.Markdown()
                lst = gr.Radio(choices=[], value=None, label="一覧（クリックで選択／フォルダは移動）")
                browse_msg = gr.Markdown()
                with gr.Row():
                    upload = gr.UploadButton("YAMLファイルを指定", file_types=[".yaml", ".yml"], size="sm")
                    reload_btn = gr.Button("🔄 データ再読み込み", size="sm")

            # ---- 右：Scene 組み立て ----
            with gr.Column(scale=5):
                sel_md = gr.Markdown()
                reselect_btn = gr.Button("🔁 Situation / Scene を選び直す", size="sm")

                @gr.render(inputs=[sc])
                def render_slots(sc_v):
                    slots = L.slots_of(repo, sc_v)
                    if not slots:
                        gr.Markdown("_Situation を選ぶと、スロットの数だけキャスト枠が表示されます。_")
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
                                outfit_rd.input(partial(on_outfit, slot), [sc, nav, outfit_rd], FULL)

                neg_cb = gr.Checkbox(label="ネガティブを適用する（既定OFF。OFFでも合成結果は下に表示）", value=False)
                moss_tb = gr.Textbox(label="MOSS JSON", lines=16, max_lines=30, show_copy_button=True, interactive=False)
                anima_tb = gr.Textbox(label="Animaプロンプト", lines=5, show_copy_button=True, interactive=False)
                with gr.Row():
                    negm_tb = gr.Textbox(label="ネガティブ（MOSS用）", lines=3, show_copy_button=True, interactive=False)
                    nega_tb = gr.Textbox(label="ネガティブ（Anima用）", lines=3, show_copy_button=True, interactive=False)
                issues_md = gr.Markdown()
                with gr.Accordion("データ読み込みの検証結果（data/ 全体）", open=False):
                    data_issues_md = gr.Markdown()

                # 保存は誤操作を避けるため、画面の一番下に置く
                gr.Markdown("---\n### 保存")
                with gr.Row():
                    name_tb = gr.Textbox(label="Scene表示名（任意）", scale=2)
                    slug_tb = gr.Textbox(
                        label="Scene名（半角英小文字・数字・_）",
                        info="空欄なら「名前_1」「名前_2」…と番号を付けて新規保存。既存と同じ名前を入力したときだけ上書き",
                        scale=3)
                save_btn = gr.Button("💾 保存", variant="secondary")
                save_msg = gr.Markdown()

        NAV_OUT = [nav, lst, crumbs_md, target_md, browse_msg]
        FULL = [sc, nav, lst, crumbs_md, target_md, browse_msg,
                sel_md, moss_tb, anima_tb, negm_tb, nega_tb, issues_md, neg_cb, name_tb, slug_tb]

        # ---- 出力の組み立て ----
        def data_issues_text() -> str:
            n_err = sum(i.level == "error" for i in repo.issues)
            n_warn = len(repo.issues) - n_err
            return f"**エラー {n_err} / 警告 {n_warn}**\n\n" + L.issues_text(repo.issues)

        def nav_part(nav_v, msg):
            choices, crumbs = L.listing(repo, nav_v)
            return [nav_v, gr.update(choices=choices, value=None), crumbs, L.target_label(nav_v),
                    gr.skip() if msg is None else msg]

        def nav_out(nav_v, msg=None):
            return nav_part(nav_v, msg)

        def full(sc_old, sc_new, nav_v, msg=None, sc_changed=True):
            if not sc_changed:  # フォルダ移動だけのときは、枠の描き直しとコンパイルを避ける
                return [gr.skip()] + nav_part(nav_v, msg) + [gr.skip()] * 9
            r = L.compile_view(repo, sc_new)
            apply = sc_new["apply_negative"]
            note = "" if apply else "（未適用・参考表示）"
            if r is None:
                moss = anima = negm = nega = ""
                issues = ""
            else:
                moss, anima, negm, nega = (r.moss_prompt, r.anima_prompt, r.negative_moss, r.negative_anima)
                issues = L.issues_text(r.issues)
            switched = (sc_old["situation_id"], sc_old["scene_id"]) != (sc_new["situation_id"], sc_new["scene_id"])
            return [sc_new] + nav_part(nav_v, msg) + [
                L.selection_text(repo, sc_new), moss, anima,
                gr.update(value=negm, label=f"ネガティブ（MOSS用）{note}"),
                gr.update(value=nega, label=f"ネガティブ（Anima用）{note}"),
                issues, apply,
                gr.update(value=sc_new["scene_name"]) if switched else gr.skip(),
                gr.update(value="") if switched else gr.skip(),
            ]

        # ---- イベント ----
        def on_pick(target, slot, nav_v):
            return nav_out(L.set_target(nav_v, target, slot), "")

        def on_outfit(slot, sc_v, nav_v, value):
            return full(sc_v, L.set_outfit(sc_v, slot, value), nav_v)

        def on_browse(choice, sc_v, nav_v):
            sc2, nav2, msg = L.browse(repo, sc_v, nav_v, choice)
            return full(sc_v, sc2, nav2, msg, sc_changed=(sc2 != sc_v))

        def on_upload(path, sc_v, nav_v):
            sc2, nav2, msg = L.from_upload(repo, sc_v, nav_v, path)
            return full(sc_v, sc2, nav2, msg)

        def on_negative(flag, sc_v, nav_v):
            sc2 = copy.deepcopy(sc_v)
            sc2["apply_negative"] = bool(flag)
            return full(sc_v, sc2, nav_v)

        def on_reload(sc_v, nav_v):
            repo.reload()
            return full(sc_v, sc_v, nav_v, "🔄 再読み込みしました") + [data_issues_text()]

        def on_load(sc_v, nav_v):
            return full(sc_v, sc_v, nav_v, "") + [data_issues_text()]

        def on_save(sc_v, nav_v, slug, display_name):
            sc2, msg = L.save(repo, sc_v, slug, display_name)
            return full(sc_v, sc2, nav_v) + [msg, data_issues_text()]

        reselect_btn.click(partial(on_pick, "situation", None), [nav], NAV_OUT, api_name="reselect")
        lst.input(on_browse, [lst, sc, nav], FULL, api_name="browse")
        upload.upload(on_upload, [upload, sc, nav], FULL, api_name="upload")
        neg_cb.input(on_negative, [neg_cb, sc, nav], FULL, api_name="negative")
        reload_btn.click(on_reload, [sc, nav], FULL + [data_issues_md], api_name="reload")
        save_btn.click(on_save, [sc, nav, slug_tb, name_tb], FULL + [save_msg, data_issues_md], api_name="save")
        demo.load(on_load, [sc, nav], FULL + [data_issues_md], api_name="init")

    return demo
