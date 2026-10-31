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
                       iter_run)
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
        gen = gr.State(None)  # 直近の生成結果のスナップショット（保存ボタンで Scene に記録する。再試行にも使う）
        gp = gr.State(L.new_gp())  # 画面で上書きした生成パラメータ（動かした項目だけ。保存で Scene.gen になる）

        with gr.Row():
            # ---- 左：共有ブラウザ（常時表示。ボタンで選択先を切り替える）----
            with gr.Column(scale=2, min_width=320):
                target_md = gr.Markdown(L.target_label(L.new_nav()))
                crumbs_md = gr.Markdown()
                lst = gr.Radio(choices=[], value=None, label="一覧（クリックで選択／フォルダは移動）")
                browse_msg = gr.Markdown()
                with gr.Row():
                    upload = gr.UploadButton("YAMLファイルを指定", file_types=[".yaml", ".yml"], size="sm")
                    reload_btn = gr.Button("🔄 データ再読み込み", size="sm")
                setdir_btn = gr.Button("📍 このフォルダを保存先にする", size="sm")

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
                                outfit_rd.input(partial(on_outfit, slot), [sc, nav, outfit_rd], FULL)

                neg_cb = gr.Checkbox(label="ネガティブを適用する（既定OFF。OFFでも合成結果は下に表示）", value=False)
                summary_md = gr.Markdown()
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
                retry_btn = gr.Button("🔁 Anima段だけ再試行", visible=False)
                gen_status = gr.Markdown()
                gallery = gr.Gallery(label="生成結果（MOSS段 → Anima段）", columns=2, height="auto",
                                     object_fit="contain", interactive=False)

                # 保存は誤操作を避けるため、画面の一番下に置く
                gr.Markdown("---\n### 保存")
                with gr.Row():
                    name_tb = gr.Textbox(label="Scene表示名（任意）", scale=2)
                    slug_tb = gr.Textbox(
                        label="Scene の id（半角英小文字・数字・_）",
                        info="空欄なら「<名前>_1」「_2」…と番号を付けて新規保存（開いている Scene があればその id、なければ <Action の id>__scene）。"
                             "既存と同じ id を入力したときだけ上書き。目安は「<Action の id>__〜」",
                        scale=3)
                with gr.Row():
                    savedir_md = gr.Markdown(L.savedir_text(repo, L.new_nav()))
                    pick_save_btn = gr.Button("📁 保存先フォルダを選ぶ", size="sm")
                with gr.Row():
                    newdir_tb = gr.Textbox(label="新しいフォルダ名（保存先を選ぶ画面で開いているフォルダの中に作ります）", scale=3)
                    mkdir_btn = gr.Button("＋フォルダ作成", size="sm", scale=1)
                save_btn = gr.Button("💾 保存", variant="secondary")
                save_msg = gr.Markdown()
                with gr.Row(visible=False) as ext_row:   # 外部変更の検知（EDITOR_SPEC 12章-4）
                    ext_reload_btn = gr.Button("再読み込み（編集を破棄）", size="sm")
                    ext_over_btn = gr.Button("上書き保存", variant="stop", size="sm")

        NAV_OUT = [nav, lst, crumbs_md, target_md, browse_msg, savedir_md]
        FULL = [sc] + NAV_OUT + [sel_md, moss_tb, anima_tb, negm_tb, nega_tb, summary_md, issues_md, neg_cb,
                                 name_tb, slug_tb]
        SLUG_IDX = FULL.index(slug_tb)
        GP_OUT = [gp] + [c for key in L.ITEMS for c in items[key]]
        GEN_OUT = [gen_status, gallery, gen, retry_btn]

        # ---- 出力の組み立て ----
        def data_issues_text() -> str:
            n_err = sum(i.level == "error" for i in repo.issues)
            n_warn = len(repo.issues) - n_err
            return f"**エラー {n_err} / 警告 {n_warn}**\n\n" + L.issues_text(repo.issues)

        def nav_part(nav_v, msg):
            choices, crumbs = L.listing(repo, nav_v)
            return [nav_v, gr.update(choices=choices, value=None), crumbs, L.target_label(nav_v),
                    gr.skip() if msg is None else msg, L.savedir_text(repo, nav_v)]

        def nav_out(nav_v, msg=None):
            return nav_part(nav_v, msg)

        def full(sc_old, sc_new, nav_v, msg=None, sc_changed=True):
            if not sc_changed:  # フォルダ移動だけのときは、枠の描き直しとコンパイルを避ける
                return [gr.skip()] + nav_part(nav_v, msg) + [gr.skip()] * 10
            r = L.compile_view(repo, sc_new)
            apply = sc_new["apply_negative"]
            note = "" if apply else "（未適用・参考表示）"
            if r is None or not r.ok:
                moss = anima = negm = nega = ""
            else:
                moss, anima, negm, nega = (r.moss_prompt, r.anima_prompt, r.negative_moss, r.negative_anima)
            issues = L.issues_text(r.issues) if r is not None else ""
            opened = sc_old["open_seq"] != sc_new["open_seq"]
            return [sc_new] + nav_part(nav_v, msg) + [
                L.selection_text(repo, sc_new), moss, anima,
                gr.update(value=negm, label=f"ネガティブ（MOSS用）{note}"),
                gr.update(value=nega, label=f"ネガティブ（Anima用）{note}"),
                L.summary_text(r), issues, apply,
                gr.update(value=sc_new["scene_name"]) if opened else gr.skip(),
                gr.update(value="") if opened else gr.skip(),
            ]

        # ---- イベント ----
        def on_pick(target, slot, nav_v):
            return nav_out(L.set_target(nav_v, target, slot), "")

        def on_setdir(nav_v):
            nav2, msg = L.choose_savedir(repo, nav_v)
            return nav_out(nav2, msg)

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

        def effective_of(gp_v):
            cfg = comfy["cfg"]
            return effective_gen(cfg, gp_v["gen"]) if cfg is not None and cfg.samplers else None

        def do_save(force, sc_v, nav_v, slug, display_name, gen_v, gp_v):
            sc2, msg = L.save(repo, sc_v, nav_v, slug, display_name, gen_v, gen_params=gp_v["gen"],
                              effective=effective_of(gp_v), force=force)
            out = full(sc_v, sc2, nav_v)
            if msg.startswith("💾"):
                out[SLUG_IDX] = gr.update(value="")  # 保存できたら id 欄を空に戻す
            return out + [msg, data_issues_text(), gr.update(visible=msg.startswith(L.EXTERNAL_PREFIX))]

        def on_ext_reload(sc_v, nav_v):
            sc2, nav2, msg = L.reopen_scene(repo, sc_v, nav_v)
            return full(sc_v, sc2, nav2, msg) + ["", gr.update(visible=False)]

        def on_mkdir(name, nav_v):
            msg = L.make_savedir(repo, nav_v, name)
            return nav_out(nav_v, msg) + [gr.update(value="") if msg.startswith("📁") else gr.skip()]

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
            used = f"\n\n使った値：{L.gen_summary(done.gen)}" if done.gen else ""
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

        def stream(events):
            """iter_run / iter_retry_anima のイベントを、画面の更新に変える。"""
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
            yield finished(done)

        def on_generate(sc_v, copy_dir, gp_v):
            """生成を実行し、経過を逐次表示する。YAMLへの記録は保存ボタンで行う。"""
            skip = gr.skip()
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
            yield from stream(iter_run(cfg, result, Path(copy_dir) if copy_dir else None, gen=gp_v["gen"]))

        def on_retry(sc_v, copy_dir, gp_v, gen_v):
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
            yield from stream(iter_retry_anima(cfg, result, gen_v, Path(copy_dir) if copy_dir else None, gen=gp_v["gen"]))

        # ---- 配線 ----
        pick_scene_btn.click(partial(on_pick, "scene", None), [nav], NAV_OUT, api_name="pick_scene")
        pick_action_btn.click(partial(on_pick, "action", None), [nav], NAV_OUT, api_name="pick_action")
        pick_bg_btn.click(partial(on_pick, "background", None), [nav], NAV_OUT, api_name="pick_background")
        pick_save_btn.click(partial(on_pick, "savedir", None), [nav], NAV_OUT, api_name="pick_savedir")
        setdir_btn.click(on_setdir, [nav], NAV_OUT, api_name="set_savedir")
        lst.input(on_browse, [lst, sc, nav], FULL, api_name="browse").then(
            refresh_gp, [sc, gp], GP_OUT, api_name="gp_after_browse")
        upload.upload(on_upload, [upload, sc, nav], FULL, api_name="upload").then(
            refresh_gp, [sc, gp], GP_OUT, api_name="gp_after_upload")
        neg_cb.input(on_negative, [neg_cb, sc, nav], FULL, api_name="negative")
        COMFY_OUT = [data_issues_md, comfy_md, gen_btn]
        reload_btn.click(on_reload, [sc, nav], FULL + COMFY_OUT, api_name="reload").then(
            refresh_gp, [sc, gp], GP_OUT, api_name="gp_after_reload")
        SAVE_OUT = FULL + [save_msg, data_issues_md, ext_row]
        save_btn.click(partial(do_save, False), [sc, nav, slug_tb, name_tb, gen, gp], SAVE_OUT, api_name="save")
        ext_over_btn.click(partial(do_save, True), [sc, nav, slug_tb, name_tb, gen, gp], SAVE_OUT,
                           api_name="save_overwrite")
        ext_reload_btn.click(on_ext_reload, [sc, nav], FULL + [save_msg, ext_row], api_name="ext_reload").then(
            refresh_gp, [sc, gp], GP_OUT, api_name="gp_after_ext_reload")
        mkdir_btn.click(on_mkdir, [newdir_tb, nav], NAV_OUT + [newdir_tb], api_name="mkdir")
        demo.load(on_load, [sc, nav], FULL + COMFY_OUT, api_name="init").then(
            refresh_gp, [sc, gp], GP_OUT, api_name="gp_after_init")
        for (stage, param), (sl, src, rb) in items.items():
            sl.release(partial(on_release, stage, param), [sl, gp], [gp, src, rb],
                       api_name=f"gp_set_{stage}_{param}")
            rb.click(partial(on_reset, stage, param), [gp], [gp, sl, src, rb],
                     api_name=f"gp_reset_{stage}_{param}")
        pick_dir_btn.click(on_pick_dir, [copy_dir_tb], [copy_dir_tb, gen_status], api_name="pick_dir")
        clear_dir_btn.click(lambda: "", None, [copy_dir_tb], api_name="clear_dir")
        gen_btn.click(on_generate, [sc, copy_dir_tb, gp], GEN_OUT, api_name="generate")
        retry_btn.click(on_retry, [sc, copy_dir_tb, gp, gen], GEN_OUT, api_name="retry")

    return demo
