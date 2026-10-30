"""シーン組み立て画面（10章）。ロジックは moa.ui.logic、コンパイルは moa.core にある。"""
from __future__ import annotations

import copy
import io
from functools import partial
from pathlib import Path
from typing import Callable

import gradio as gr
from PIL import Image

from moa.comfy import ComfyConfig, ComfyError, Generation, iter_run
from moa.comfy.config import STAGE_LABEL
from moa.core import Repository
from moa.core.gen import GEN_PARAMS
from moa.core.models import Issue
from moa.ui import logic as L
from moa.ui.dialogs import pick_folder


def build_ui(repo: Repository,
             comfy_loader: Callable[[], tuple[ComfyConfig | None, list[Issue]]] | None = None) -> gr.Blocks:
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

    with gr.Blocks(title="MOSS/Anima プロンプトビルダー") as demo:
        sc = gr.State(L.new_sc())
        nav = gr.State(L.new_nav())
        gen = gr.State(None)  # 直近の生成結果（保存ボタンで Scene に記録する）
        gp = gr.State(L.new_gp())  # 画面で上書きした生成パラメータ（動かした項目だけ。保存で Scene.gen になる）

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
                gen_btn = gr.Button("🎨 生成", variant="primary")
                gen_status = gr.Markdown()
                gallery = gr.Gallery(label="生成結果（MOSS段 → Anima段）", columns=2, height="auto",
                                     object_fit="contain", interactive=False)

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
        GP_OUT = [gp] + [c for key in L.ITEMS for c in items[key]]

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

        def comfy_parts():
            cfg = comfy["cfg"]
            head = (f"✅ 接続先 `{cfg.url}` ／ ワークフロー `{cfg.workflow_path.name}`" if cfg
                    else "⚠ ComfyUI連携は無効です")
            detail = L.issues_text(comfy["issues"]) if comfy["issues"] else ""
            return [head + ("\n\n" + detail if detail else ""), gr.update(interactive=cfg is not None)]

        def on_reload(sc_v, nav_v):
            repo.reload()
            reload_comfy()
            return full(sc_v, sc_v, nav_v, "🔄 再読み込みしました") + [data_issues_text()] + comfy_parts()

        def on_load(sc_v, nav_v):
            reload_comfy()
            return full(sc_v, sc_v, nav_v, "") + [data_issues_text()] + comfy_parts()

        def on_save(sc_v, nav_v, slug, display_name, gen_v, gp_v):
            sc2, msg = L.save(repo, sc_v, slug, display_name, gen_v, gen_params=gp_v["gen"])
            return full(sc_v, sc2, nav_v) + [msg, data_issues_text()]

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

        def refresh_gp(sc_v, gp_v):
            """Scene を開いたら、その Scene.gen をスライダーに反映する。"""
            gp2 = L.sync_gp(sc_v, gp_v)
            return [gp2] + gp_view(gp2)

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

        def on_generate(sc_v, copy_dir, gp_v):
            """生成を実行し、経過を逐次表示する。YAMLへの記録は保存ボタンで行う。"""
            skip = gr.skip()
            cfg = comfy["cfg"]
            if cfg is None:
                yield "⚠ ComfyUI連携の設定が無効です（上の設定状態を確認してください）", skip, skip
                return
            result = L.compile_view(repo, sc_v)
            if result is None:
                yield "⚠ Situation / Scene を選んでください", skip, skip
                return
            if not result.ok:
                yield "❌ 検証エラーがあるため生成できません。\n\n" + L.issues_text(result.issues), skip, skip
                return
            done = None
            try:
                for ev in iter_run(cfg, result, Path(copy_dir) if copy_dir else None, gen=gp_v["gen"]):
                    if isinstance(ev, Generation):
                        done = ev
                    else:
                        yield f"⏳ {ev}", skip, skip
            except ComfyError as e:
                yield f"❌ {e}", skip, skip
                return
            except OSError as e:
                yield f"❌ 画像のコピーに失敗しました：{e}", skip, skip
                return
            shots = [(Image.open(io.BytesIO(i.data)), f"{STAGE_LABEL[i.stage]}：{Path(i.record_path).name}")
                     for i in done.images]
            paths = "\n".join(f"- {STAGE_LABEL[i.stage]}：`{i.record_path}`" for i in done.images)
            state = {"seed": done.seed, "images": [i.record_path for i in done.images],
                     "moss_prompt": done.moss_prompt, "anima_prompt": done.anima_prompt,
                     "negative_applied": done.negative_applied, "gen": done.gen}
            used = f"\n\n使った値：{L.gen_summary(done.gen)}" if done.gen else ""
            yield (f"✅ 完了　シード `{done.seed}`（MOSS段・Anima段で共通）{used}\n\n{paths}\n\n"
                   "※ 保存ボタンを押すと、シードと画像のパスが Scene に記録されます"), shots, state

        reselect_btn.click(partial(on_pick, "situation", None), [nav], NAV_OUT, api_name="reselect")
        lst.input(on_browse, [lst, sc, nav], FULL, api_name="browse").then(
            refresh_gp, [sc, gp], GP_OUT, api_name="gp_after_browse")
        upload.upload(on_upload, [upload, sc, nav], FULL, api_name="upload").then(
            refresh_gp, [sc, gp], GP_OUT, api_name="gp_after_upload")
        neg_cb.input(on_negative, [neg_cb, sc, nav], FULL, api_name="negative")
        COMFY_OUT = [data_issues_md, comfy_md, gen_btn]
        reload_btn.click(on_reload, [sc, nav], FULL + COMFY_OUT, api_name="reload").then(
            refresh_gp, [sc, gp], GP_OUT, api_name="gp_after_reload")
        save_btn.click(on_save, [sc, nav, slug_tb, name_tb, gen, gp], FULL + [save_msg, data_issues_md],
                       api_name="save")
        demo.load(on_load, [sc, nav], FULL + COMFY_OUT, api_name="init").then(
            refresh_gp, [sc, gp], GP_OUT, api_name="gp_after_init")
        for (stage, param), (sl, src, rb) in items.items():
            sl.release(partial(on_release, stage, param), [sl, gp], [gp, src, rb],
                       api_name=f"gp_set_{stage}_{param}")
            rb.click(partial(on_reset, stage, param), [gp], [gp, sl, src, rb],
                     api_name=f"gp_reset_{stage}_{param}")
        pick_dir_btn.click(on_pick_dir, [copy_dir_tb], [copy_dir_tb, gen_status], api_name="pick_dir")
        clear_dir_btn.click(lambda: "", None, [copy_dir_tb], api_name="clear_dir")
        gen_btn.click(on_generate, [sc, copy_dir_tb, gp], [gen_status, gallery, gen], api_name="generate")

    return demo
