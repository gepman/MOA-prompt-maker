"""エディタの画面（EDITOR_SPEC 3章）。処理は moa.editor.logic、検証・コンパイルは moa.core にある。

方針（CLAUDE.md）：個数が変わる部品（mossItems のカード、スロットごとの服装）は最大数を最初から用意して表示／非表示を切り替える。
キー入力ごとの再コンパイル・再描画はしない。ハンドラーは {部品: 値} の dict を返し、全部品を outputs に並べる。
"""
from __future__ import annotations

import copy
from functools import partial

import gradio as gr

from moa.core.models import Issue
from moa.core.repository import Repository
from moa.editor.logic import cards as C
from moa.editor.logic import diffs as D
from moa.editor.logic import forms, session, tree
from moa.editor.logic import picker as PK
from moa.editor.logic.bboxdraw import draw_bboxes
from moa.editor.logic.forms import DEFAULT_CANVAS, KIND_LABEL, MAX_ITEMS
from moa.editor.logic.preview import preview
from moa.editor.logic.save import (CONFIRM_COW, CONFIRM_USED_ACTION, COW_ID_ERROR, ERRORS, EXTERNAL, SAVED,
                                   save_draft, save_folder)
from moa.editor.logic.validation import validate_draft
from moa.ui.style import DEFAULT_PROMPT_FONT_PX, prompt_css

MAX_SLOTS = 8
TRI_CHOICES = [("未指定（キーを書かない）", "unset"), ("none（着せない）", "none"), ("Outfit を選ぶ", "outfit")]
SECTION_CHOICES = [("キャラ", "characters"), ("服装", "outfits"), ("背景", "backgrounds"), ("行為", "actions")]
CARD_FIELDS = ("label", "caption", "y0", "x0", "y1", "x1")


def issue_lines(issues: list[Issue]) -> str:
    if not issues:
        return "✅ 問題なし"
    return "\n".join(f"- {'❌' if i.level == 'error' else '⚠️'} " + (f"`{i.field}` " if i.field else "") + i.message
                     for i in issues)


def build_editor(repo: Repository, prompt_font_px: int | float = DEFAULT_PROMPT_FONT_PX) -> gr.Blocks:
    c: dict = {}   # 名前 → 部品

    def R(name, comp):
        c[name] = comp
        return comp

    def pick_row(name: str, empty: str = "（未設定）"):
        """選択欄：選択中の項目の表示＋［選ぶ］。値は gr.State（名前 `name`）に持つ。共通ピッカー（右サイドバー）で選ぶ。"""
        R(name, gr.State(None))
        with gr.Row():
            R(f"{name}_show", gr.Markdown(f"選択中：{empty}"))
            R(f"{name}_btn", gr.Button("選ぶ", size="sm", scale=0, min_width=72, variant="primary"))

    with gr.Blocks(title="MOA エディタ", css=prompt_css(prompt_font_px)) as demo:
        st = R("st", gr.State(session.new_state()))
        cards_n = R("cards_n", gr.State(0))

        with gr.Row():
            gr.Markdown("## MOA エディタ")
            R("status", gr.Markdown("保存状態：—"))
            reload_btn = gr.Button("🔄 再読み込み", size="sm")

        with gr.Row():
            # ---- 左：ツリー ----
            with gr.Column(scale=3, min_width=280):
                R("section", gr.Radio(SECTION_CHOICES, value="characters", label="ルート"))
                R("crumbs", gr.Markdown())
                R("lst", gr.Radio(choices=[], value=None, label="一覧（クリックで選択／フォルダは移動）"))
                R("nav_msg", gr.Markdown())
                new_btn = gr.Button("＋新規作成")
                with gr.Row():
                    R("folder_tb", gr.Textbox(label="新しいフォルダ名", scale=3))
                    mkdir_btn = gr.Button("＋フォルダ作成", scale=1)
                with gr.Group(visible=False) as pending_box:
                    R("pending_md", gr.Markdown())
                    with gr.Row():
                        discard_btn = gr.Button("破棄して移動", variant="stop")
                        cancel_btn = gr.Button("キャンセル")
                R("pending_box", pending_box)

            # ---- 中央：編集フォーム ----
            with gr.Column(scale=5, min_width=480):
                R("head", gr.Markdown("_左の一覧から項目を選ぶか、［＋新規作成］を押してください。_"))

                with gr.Group(visible=False) as entity_box:
                    R("id", gr.Textbox(label="ID（作成時だけ入力。以後は変更できません）", interactive=False))
                    R("id_msg", gr.Markdown())
                    R("name", gr.Textbox(label="name（表示名）"))
                    R("tags", gr.Textbox(label="tags（カンマ区切り）"))

                    with gr.Group(visible=False) as char_blk:
                        R("group", gr.Textbox(label="group"))
                        R("sex", gr.Radio(["female", "male"], value="female", label="sex"))
                        R("def_mode", gr.Radio(TRI_CHOICES, value="unset", label="defaultOutfit"))
                        pick_row("def_pick", "未指定")

                    with gr.Group(visible=False) as rating_blk:
                        R("rating", gr.Radio(["safe", "sensitive", "explicit"], value="safe", label="rating"))
                        R("metaTags", gr.Textbox(label="metaTags（カンマ区切り。MOSS の summary.meta に入る）"))

                    with gr.Group(visible=False) as canvas_blk:
                        gr.Markdown("⚠ Background では**プレイスホルダーは使えません**。"
                                    "キャストの姿や行為は書かず、背景だけを書いてください。")
                        with gr.Row():
                            R("cw", gr.Number(label="canvas w", precision=0, value=1536))
                            R("ch", gr.Number(label="canvas h", precision=0, value=1024))

                    with gr.Group(visible=False) as action_blk:
                        R("help_act", gr.Markdown())
                        gr.Markdown("**推奨背景**（Scene 作成時の初期値）")
                        pick_row("act_bg")
                        R("slots", gr.Textbox(label="slots（カンマ区切り。1つ以上、重複不可）"))
                        R("slot_help", gr.Markdown())
                        rows = []
                        for i in range(MAX_SLOTS):
                            with gr.Group(visible=False) as row:
                                R(f"o_mode{i}", gr.Radio(TRI_CHOICES, value="unset", label=f"outfits.slot{i}"))
                                pick_row(f"o_pick{i}", "未指定")
                            R(f"o_row{i}", row)
                            rows.append(row)
                        gr.Markdown("**extras**（キャスト以外に特定の人物として描く人数。全部 0 なら書きません）")
                        with gr.Row():
                            R("ex_f", gr.Number(label="female", precision=0, value=0, minimum=0))
                            R("ex_m", gr.Number(label="male", precision=0, value=0, minimum=0))
                            R("ex_o", gr.Number(label="others", precision=0, value=0, minimum=0))

                    with gr.Group(visible=False) as moss_blk:
                        R("moss", gr.Textbox(label="moss", lines=6, elem_classes=["moa-prompt"]))
                        R("df_moss", gr.HTML(visible=False, elem_classes=["moa-prompt"]))

                    with gr.Group(visible=False) as cap_blk:
                        R("mossCaption", gr.Textbox(label="mossCaption", lines=4, elem_classes=["moa-prompt"]))
                        R("df_mossCaption", gr.HTML(visible=False, elem_classes=["moa-prompt"]))

                    with gr.Group(visible=False) as cards_blk:
                        gr.Markdown("**mossItems**（label・caption・bbox `[y_min, x_min, y_max, x_max]`）")
                        for i in range(MAX_ITEMS):
                            with gr.Group(visible=False) as card:
                                R(f"c_hdr{i}", gr.Markdown(f"**#{i + 1}**"))
                                with gr.Row():
                                    R(f"c_label{i}", gr.Dropdown(C.LABELS, value="object", allow_custom_value=True,
                                                                  label="label", scale=2))
                                    R(f"c_up{i}", gr.Button("↑", size="sm", scale=0, min_width=40))
                                    R(f"c_down{i}", gr.Button("↓", size="sm", scale=0, min_width=40))
                                    R(f"c_del{i}", gr.Button("削除", size="sm", scale=0, min_width=60))
                                    R(f"c_full{i}", gr.Button("全面", size="sm", scale=0, min_width=60))
                                R(f"c_caption{i}", gr.Textbox(label="caption", lines=2, elem_classes=["moa-prompt"]))
                                R(f"c_df{i}", gr.HTML(visible=False, elem_classes=["moa-prompt"]))
                                with gr.Row():
                                    for k, lab in zip(("y0", "x0", "y1", "x1"), ("y_min", "x_min", "y_max", "x_max")):
                                        R(f"c_{k}{i}", gr.Number(label=lab, precision=0, value=0))
                            R(f"c_card{i}", card)
                        R("add_btn", gr.Button("＋アイテム追加"))
                        R("add_hint", gr.Markdown())
                        R("bbox_img", gr.Image(label="bbox プレビュー図（表示専用）", type="pil", interactive=False))

                    with gr.Group(visible=False) as anima_blk:
                        R("anima", gr.Textbox(label="anima", lines=4, elem_classes=["moa-prompt"]))
                        R("df_anima", gr.HTML(visible=False, elem_classes=["moa-prompt"]))
                    with gr.Group(visible=False) as hair_blk:
                        R("bodyHairMoss", gr.Textbox(label="bodyHairMoss", lines=2, elem_classes=["moa-prompt"]))
                        R("df_bodyHairMoss", gr.HTML(visible=False, elem_classes=["moa-prompt"]))
                        R("bodyHairAnima", gr.Textbox(label="bodyHairAnima", lines=2, elem_classes=["moa-prompt"]))
                        R("df_bodyHairAnima", gr.HTML(visible=False, elem_classes=["moa-prompt"]))
                    with gr.Group(visible=False) as neg_blk:
                        R("negativeMoss", gr.Textbox(label="negativeMoss", lines=2, elem_classes=["moa-prompt"]))
                        R("df_negativeMoss", gr.HTML(visible=False, elem_classes=["moa-prompt"]))
                        R("negativeAnima", gr.Textbox(label="negativeAnima", lines=2, elem_classes=["moa-prompt"]))
                        R("df_negativeAnima", gr.HTML(visible=False, elem_classes=["moa-prompt"]))

                with gr.Group(visible=False) as folder_box:
                    gr.Markdown("**フォルダの設定（`_folder.yaml`）**。ファイルが無ければ保存時に新しく作ります。")
                    R("f_name", gr.Textbox(label="name（フォルダの表示名）"))
                    R("f_desc", gr.Textbox(label="description"))
                    R("f_order", gr.Textbox(label="order（1行に1つ、ファイル名またはフォルダ名）", lines=4))

                R("chg_html", gr.HTML(visible=False, elem_classes=["moa-prompt"]))   # 保存前の変更点（14.4）
                with gr.Row(visible=False) as buttons_row:
                    save_btn = gr.Button("💾 保存", variant="primary")
                    R("copy_btn", gr.Button("複製して新規作成"))
                    check_btn = gr.Button("検証だけする")
                    diff_btn = gr.Button("差分を更新")

                with gr.Group(visible=False) as conf_box:
                    R("conf_md", gr.Markdown())
                    R("conf_scenes", gr.Markdown())
                    R("conf_diff", gr.HTML(elem_classes=["moa-prompt"]))   # 保存前の変更点を併記（14.4）
                    R("cow_id", gr.Textbox(label="新しい ID（複製の ID）", visible=False))
                    with gr.Row():
                        R("conf_ok", gr.Button("保存", variant="primary"))
                        conf_cancel = gr.Button("キャンセル")
                with gr.Group(visible=False) as ext_box:
                    gr.Markdown("⚠ **ファイルが外部で変更されています**（VS Code・ビルダー・エクスプローラーなど）。")
                    with gr.Row():
                        ext_reload = gr.Button("再読み込み（編集を破棄）")
                        ext_over = gr.Button("上書き保存", variant="stop")
                R("save_msg", gr.Markdown())
                R("issues", gr.Markdown())
                with gr.Accordion("YAML（閲覧のみ）", open=False):
                    R("yaml", gr.Code(language="yaml", interactive=False, label="現在のファイル"))

            # ---- 右：プレビュー ----
            with gr.Column(scale=4, min_width=360):
                gr.Markdown("### プレビュー\n**未保存の内容でプレビュー中**（保存ファイルには触りません）")
                with gr.Group(visible=False) as pv_act_blk:
                    gr.Markdown("**Action**")
                    pick_row("pv_act", "（自動）")
                with gr.Group(visible=False) as pv_slot_blk:
                    R("pv_slot", gr.Radio(choices=[], value=None, label="スロット"))
                with gr.Group(visible=False) as pv_char_blk:
                    gr.Markdown("**キャラ**")
                    pick_row("pv_char", "（自動）")
                with gr.Group(visible=False) as pv_bg_blk:
                    gr.Markdown("**Background**")
                    pick_row("pv_bg", "（自動）")
                with gr.Row():
                    pv_use = gr.Button("ツリーで選択中のものを使う", size="sm")
                    pv_btn = gr.Button("プレビュー更新", variant="primary", size="sm")
                R("pv_notes", gr.Markdown())
                R("pv_summary", gr.Markdown())
                R("pv_issues", gr.Markdown())
                R("pv_moss", gr.Textbox(label="MOSS JSON（読み取り専用）", lines=12, max_lines=24, interactive=False))
                R("pv_anima", gr.Textbox(label="Anima（読み取り専用）", lines=4, interactive=False))
                R("pv_negm", gr.Textbox(label="ネガティブ（MOSS）", lines=2, interactive=False))
                R("pv_nega", gr.Textbox(label="ネガティブ（Anima）", lines=2, interactive=False))
                R("pv_img", gr.Image(label="bbox プレビュー図（背景＝青、行為＝橙、はみ出し＝赤）", type="pil",
                                     interactive=False))

        # ---- 共通ピッカー（右サイドバー）----
        R("pk", gr.State(PK.new_pk()))
        with gr.Sidebar(position="right", open=False, width=420) as pk_bar:
            R("pk_target", gr.Markdown())
            R("pk_crumbs", gr.Radio(choices=[], value=None, label="パンくず（押すとそのフォルダへ）"))
            with gr.Row():
                R("pk_q", gr.Textbox(label="名前検索（Enter か 🔍。空にして Enter でフォルダ表示に戻る）",
                                     placeholder="name・ID・tags で部分一致", scale=4))
                R("pk_search", gr.Button("🔍", size="sm", scale=0, min_width=48))
                R("pk_clear_q", gr.Button("✕", size="sm", scale=0, min_width=48))
            R("pk_msg", gr.Markdown())
            with gr.Row():
                R("pk_dirs", gr.Radio(choices=[], value=None, label="フォルダ", scale=1))
                R("pk_items", gr.Radio(choices=[], value=None, label="項目（名前（ID））", scale=2))
            R("pk_none", gr.Button("空にする", size="sm", visible=False))
            R("pk_cancel", gr.Button("キャンセル"))
        R("pk_bar", pk_bar)

        # ---- 部品の一覧 ----
        blocks = {"entity": entity_box, "folder": folder_box, "buttons": buttons_row, "char": char_blk,
                  "rating": rating_blk, "canvas": canvas_blk, "action": action_blk, "moss": moss_blk,
                  "cap": cap_blk, "cards": cards_blk, "anima": anima_blk, "hair": hair_blk, "neg": neg_blk,
                  "pv_act": pv_act_blk, "pv_slot": pv_slot_blk, "pv_char": pv_char_blk, "pv_bg": pv_bg_blk}
        boxes = {"conf": conf_box, "ext": ext_box}
        FORM_NAMES = (["id", "name", "tags", "group", "sex", "def_mode", "def_pick", "rating", "metaTags", "cw", "ch",
                       "act_bg", "slots"]
                      + [f"o_{k}{i}" for i in range(MAX_SLOTS) for k in ("mode", "pick")]
                      + ["ex_f", "ex_m", "ex_o", "moss", "mossCaption"]
                      + [f"c_{k}{i}" for i in range(MAX_ITEMS) for k in CARD_FIELDS]
                      + ["anima", "bodyHairMoss", "bodyHairAnima", "negativeMoss", "negativeAnima",
                         "f_name", "f_desc", "f_order"])
        PV_NAMES = ["pv_act", "pv_slot", "pv_char", "pv_bg"]
        # ピッカーの選択先 → 値を持つ部品の名前 / 三値コントロールの名前
        TARGET_STATE = {"def": "def_pick", "act_bg": "act_bg", "pv_act": "pv_act", "pv_char": "pv_char",
                        "pv_bg": "pv_bg", **{f"o{i}": f"o_pick{i}" for i in range(MAX_SLOTS)}}
        TARGET_MODE = {"def": "def_mode", **{f"o{i}": f"o_mode{i}" for i in range(MAX_SLOTS)}}
        STATE_FORM = {"def_pick", "act_bg", *(f"o_pick{i}" for i in range(MAX_SLOTS))}   # gr.State（.input を持たない）
        FORM = [c[n] for n in FORM_NAMES]
        OUT = list(c.values()) + list(blocks.values()) + list(boxes.values())

        # ---- フォーム値の集め方・入れ方 ----
        def card_items(a: dict, n: int) -> list[dict]:
            return [{"label": a[f"c_label{i}"] or "object", "caption": a[f"c_caption{i}"] or "",
                     "bbox": [int(a[f"c_{k}{i}"] or 0) for k in ("y0", "x0", "y1", "x1")]} for i in range(n)]

        def gather(st_v: dict, n: int, args) -> dict | None:
            """画面 → フォーム値（folder モードは {"name","description","order"}）。"""
            a = dict(zip(FORM_NAMES, args))
            mode, kind = st_v["mode"], st_v["kind"]
            if mode is None:
                return None
            if mode == "folder":
                return {"name": a["f_name"] or "", "description": a["f_desc"] or "", "order": a["f_order"] or ""}
            v = {"id": (a["id"] or "").strip(), "name": a["name"] or "", "tags": a["tags"] or ""}
            for k in ("anima", "negativeMoss", "negativeAnima"):
                v[k] = a[k] or ""
            if kind == "character":
                v |= {"group": a["group"] or "", "sex": a["sex"], "moss": a["moss"] or "",
                      "defaultOutfit": forms.outfit_value(a["def_mode"], a["def_pick"]),
                      "bodyHairMoss": a["bodyHairMoss"] or "", "bodyHairAnima": a["bodyHairAnima"] or ""}
            elif kind == "outfit":
                v["moss"] = a["moss"] or ""
            else:
                v |= {"rating": a["rating"], "metaTags": a["metaTags"] or "", "mossCaption": a["mossCaption"] or "",
                      "mossItems": card_items(a, n)}
                if kind == "background":
                    v["canvas"] = {"w": int(a["cw"] or 0), "h": int(a["ch"] or 0)}
                else:
                    slots = forms.split_list(a["slots"])
                    v |= {"background": a["act_bg"] or None, "slots": slots,
                          "outfits": {s: o for i, s in enumerate(slots[:MAX_SLOTS])
                                      if (o := forms.outfit_value(a[f"o_mode{i}"], a[f"o_pick{i}"]))},
                          "extras": {"female": int(a["ex_f"] or 0), "male": int(a["ex_m"] or 0),
                                     "others": int(a["ex_o"] or 0)}}
            return v

        def set_ref(name: str, kind: str, entity_id: str | None, mode: str = "outfit", empty: str = "（未設定）") -> dict:
            """選択欄の値（State）と表示を入れる。mode は三値コントロール（unset / none / outfit）。"""
            return {c[name]: entity_id,
                    c[f"{name}_show"]: "選択中：" + PK.shown(repo, kind, entity_id, mode=mode, empty=empty)}

        def slot_rows(slots: list[str], outfits: dict) -> dict:
            """スロットごとの服装指定の行（最大 MAX_SLOTS）。"""
            out = {}
            for i in range(MAX_SLOTS):
                if i < len(slots):
                    mode, oid = forms.outfit_mode(outfits.get(slots[i]))
                    out[c[f"o_row{i}"]] = gr.update(visible=True)
                    out[c[f"o_mode{i}"]] = gr.update(value=mode, label=f"outfits.{slots[i]}")
                    out |= set_ref(f"o_pick{i}", "outfit", oid, mode, "（未選択）")
                else:
                    out[c[f"o_row{i}"]] = gr.update(visible=False)
            return out

        def slot_help(slots: list[str]) -> dict:
            names = "、".join(f"`{s}`" for s in slots) or "（なし）"
            ph = ("プレイスホルダー：`{{slot}}`（キャラ）、`{{slot.outfit}}`（服装）、`{{slot.bodyHair}}`（体毛）。"
                  f"宣言済みのスロット：{names}")
            return {c["help_act"]: ph, c["slot_help"]: f"宣言済みのスロット：{names}"}

        def cards_updates(items: list[dict]) -> dict:
            out = {c["cards_n"]: len(items), c["add_btn"]: gr.update(interactive=C.can_add(items)),
                   c["add_hint"]: C.add_hint(items)}
            for i in range(MAX_ITEMS):
                if i < len(items):
                    it = items[i]
                    out[c[f"c_card{i}"]] = gr.update(visible=True)
                    out[c[f"c_hdr{i}"]] = f"**#{i + 1}**"
                    out[c[f"c_label{i}"]] = it["label"]
                    out[c[f"c_caption{i}"]] = it["caption"]
                    for k, n in zip(("y0", "x0", "y1", "x1"), it["bbox"]):
                        out[c[f"c_{k}{i}"]] = n
                else:
                    out[c[f"c_card{i}"]] = gr.update(visible=False)
            return out

        def visibility(st_v: dict) -> dict:
            mode, kind = st_v["mode"], st_v["kind"]
            ent = mode in ("edit", "new", "copy")
            on = lambda *kinds: ent and kind in kinds  # noqa: E731
            return {blocks["entity"]: gr.update(visible=ent), blocks["folder"]: gr.update(visible=mode == "folder"),
                    blocks["buttons"]: gr.update(visible=mode is not None),
                    blocks["char"]: gr.update(visible=on("character")),
                    blocks["rating"]: gr.update(visible=on("background", "action")),
                    blocks["canvas"]: gr.update(visible=on("background")),
                    blocks["action"]: gr.update(visible=on("action")),
                    blocks["moss"]: gr.update(visible=on("character", "outfit")),
                    blocks["cap"]: gr.update(visible=on("background", "action")),
                    blocks["cards"]: gr.update(visible=on("background", "action")),
                    blocks["anima"]: gr.update(visible=ent), blocks["hair"]: gr.update(visible=on("character")),
                    blocks["neg"]: gr.update(visible=ent),
                    blocks["pv_act"]: gr.update(visible=on("character", "outfit", "background")),
                    blocks["pv_slot"]: gr.update(visible=on("character", "outfit")),
                    blocks["pv_char"]: gr.update(visible=on("outfit")),
                    blocks["pv_bg"]: gr.update(visible=on("character", "outfit", "action")),
                    c["copy_btn"]: gr.update(visible=mode == "edit")}

        def own_image(kind: str, v: dict):
            """編集中の bbox 図（自分のアイテムだけ）。キャンバスは 8章の規則。"""
            if kind == "background":
                canvas, source = v["canvas"], "background"
            else:
                bg = repo.background(v["background"]) if v.get("background") else None
                canvas, source = (bg.canvas if bg else DEFAULT_CANVAS), "action"
            try:
                return draw_bboxes(canvas, [(source, forms.normalize(kind, v)["mossItems"])])
            except Exception:
                return None

        def status_text(st_v: dict, v: dict | None) -> str:
            if st_v["mode"] is None:
                return "保存状態：—"
            return "保存状態：**● 未保存の変更あり**" if session.is_dirty(st_v, v) else "保存状態：保存済み"

        def head_text(st_v: dict) -> str:
            mode, kind = st_v["mode"], st_v["kind"]
            if mode == "edit":
                return f"**{KIND_LABEL[kind]}を編集**：`{st_v['path']}`"
            if mode in ("new", "copy"):
                what = "複製して新規作成" if mode == "copy" else "新規作成"
                return f"**{KIND_LABEL[kind]}を{what}**　保存先：`{st_v['save_dir']}/`"
            if mode == "folder":
                return f"**フォルダの設定**：`{st_v['folder']}/_folder.yaml`"
            return "_左の一覧から項目を選ぶか、［＋新規作成］を押してください。_"

        def diff_clear() -> dict:
            """差分表示をすべて隠す（開く・保存・再読み込みで基準が変わったとき、編集中でないとき）。"""
            out = {c["chg_html"]: gr.update(value="", visible=False)}
            for k in D.LONG_KEYS:
                out[c[f"df_{k}"]] = gr.update(value="", visible=False)
            for i in range(MAX_ITEMS):
                out[c[f"c_df{i}"]] = gr.update(value="", visible=False)
            return out

        def diff_values(st_v: dict, v: dict | None) -> dict:
            """色付きの差分（欄ごと・カードごと・保存前の変更点）。基準は「読み込んだときの内容」（14.2）。変更がなければ隠す。"""
            out = diff_clear()
            base = D.baseline(st_v)
            if st_v["mode"] not in ("edit", "new", "copy") or v is None or base is None:
                return out
            view = D.views(st_v["kind"], v, base)
            for k, html in view["fields"].items():
                out[c[f"df_{k}"]] = gr.update(value=html, visible=bool(html))
            for i, html in enumerate(view["cards"][:MAX_ITEMS]):
                out[c[f"c_df{i}"]] = gr.update(value=html, visible=bool(html))
            out[c["chg_html"]] = gr.update(value=view["changes"], visible=bool(view["changes"]))
            return out

        def hide_boxes() -> dict:
            return {boxes["conf"]: gr.update(visible=False), boxes["ext"]: gr.update(visible=False),
                    c["pending_box"]: gr.update(visible=False)}

        def tree_part(st_v: dict) -> dict:
            choices, crumbs = tree.listing(repo, st_v)
            return {c["section"]: st_v["section"], c["crumbs"]: crumbs,
                    c["lst"]: gr.update(choices=choices, value=None)}

        def pv_part(st_v: dict) -> dict:
            """プレビューの選択欄を、編集中の種別に合わせて作り直す。"""
            out = {c["pv_slot"]: gr.update(choices=[], value=None)}
            for n, kind in (("pv_act", "action"), ("pv_bg", "background"), ("pv_char", "character")):
                out |= set_ref(n, kind, None, empty="（自動）")
            for n in ("pv_notes", "pv_summary", "pv_issues", "pv_moss", "pv_anima", "pv_negm", "pv_nega"):
                out[c[n]] = ""
            out[c["pv_img"]] = None
            return out

        def render(st_v: dict, msg: str | None = None) -> dict:
            """状態 → 画面全体（ツリー・フォーム・プレビューの選択欄）。"""
            out = {c["st"]: st_v, c["head"]: head_text(st_v), c["issues"]: "", c["save_msg"]: msg or "",
                   c["nav_msg"]: ""}
            out |= tree_part(st_v) | visibility(st_v) | hide_boxes() | pv_part(st_v) | diff_clear() | {c["conf_diff"]: ""}
            out[c["pk_bar"]] = gr.update(open=False)   # ピッカーは閉じる（前回のフォルダは pk に残す）
            mode, kind = st_v["mode"], st_v["kind"]
            v = st_v["loaded"]
            out[c["status"]] = status_text(st_v, v)
            out[c["yaml"]] = ""
            if mode == "folder":
                out |= {c["f_name"]: v["name"], c["f_desc"]: v["description"], c["f_order"]: v["order"]}
            elif mode in ("edit", "new", "copy"):
                is_new = mode != "edit"
                out[c["id"]] = gr.update(value=v["id"], interactive=is_new)
                out[c["id_msg"]] = (f"保存先：`{st_v['save_dir']}/{v['id'] or '<id>'}.yaml`" if is_new
                                    else f"`{st_v['path']}`")
                out[c["name"]] = v["name"]
                out[c["tags"]] = forms.join_list(v["tags"])
                for k in ("anima", "negativeMoss", "negativeAnima"):
                    out[c[k]] = v[k]
                if kind == "character":
                    mode3, oid = forms.outfit_mode(v["defaultOutfit"])
                    out |= {c["group"]: v["group"], c["sex"]: v["sex"], c["moss"]: v["moss"],
                            c["def_mode"]: mode3,
                            c["bodyHairMoss"]: v["bodyHairMoss"], c["bodyHairAnima"]: v["bodyHairAnima"]}
                    out |= set_ref("def_pick", "outfit", oid, mode3, "（未選択）")
                elif kind == "outfit":
                    out[c["moss"]] = v["moss"]
                else:
                    out |= {c["rating"]: v["rating"], c["metaTags"]: forms.join_list(v["metaTags"]),
                            c["mossCaption"]: v["mossCaption"]}
                    out |= cards_updates(v["mossItems"])
                    out[c["bbox_img"]] = own_image(kind, v)
                    if kind == "background":
                        out |= {c["cw"]: v["canvas"]["w"], c["ch"]: v["canvas"]["h"]}
                    else:
                        out |= set_ref("act_bg", "background", v.get("background") or "")
                        out |= {c["slots"]: forms.join_list(v["slots"]),
                                c["ex_f"]: v["extras"].get("female", 0), c["ex_m"]: v["extras"].get("male", 0),
                                c["ex_o"]: v["extras"].get("others", 0)}
                        out |= slot_rows(v["slots"], v["outfits"]) | slot_help(v["slots"])
                if mode == "edit" and st_v["path"]:
                    p = repo.root / st_v["path"]
                    out[c["yaml"]] = p.read_text(encoding="utf-8") if p.exists() else ""
            return out

        # ---- イベント ----
        STATE_IN = [c["st"], c["cards_n"]]

        def current(st_v, n, args):
            return gather(st_v, n, args)

        def navigate(action: dict, st_v, n, *args):
            """移動の要求。未保存の変更があれば止めて確認行を出す。"""
            v = current(st_v, n, args)
            st2, done = session.request(repo, st_v, v, action)
            if done:
                return render(st2)
            label = {"section": "ルートの切り替え", "reload": "再読み込み", "new": "新規作成"}.get(
                action["action"], "移動")
            return {c["st"]: st2, c["pending_box"]: gr.update(visible=True),
                    c["pending_md"]: f"⚠ 未保存の変更があります。**{label}**の前に確認してください。",
                    c["lst"]: gr.update(value=None), c["section"]: st_v["section"]}

        def on_section(section, st_v, n, *args):
            return navigate({"action": "section", "section": section}, st_v, n, *args)

        def on_browse(choice, st_v, n, *args):
            if not choice:
                return {}
            kind, _, rel = choice.partition(":")
            action = {"action": "up"} if choice == "up" else {"action": kind, "rel": rel}
            return navigate(action, st_v, n, *args)

        def on_new(st_v, n, *args):
            return navigate({"action": "new"}, st_v, n, *args)

        def on_reload(st_v, n, *args):
            return navigate({"action": "reload"}, st_v, n, *args)

        def on_discard(st_v):
            return render(session.resolve_pending(repo, st_v, True))

        def on_cancel(st_v):
            st2 = session.resolve_pending(repo, st_v, False)
            return {c["st"]: st2, c["pending_box"]: gr.update(visible=False)}

        def on_mkdir(name, st_v):
            msg, made = tree.make_folder(repo, st_v, name)
            out = {c["nav_msg"]: msg}
            if made:
                out |= tree_part(st_v) | {c["folder_tb"]: ""}
            return out

        # ---- 共通ピッカー ----
        def pk_part(pk_v: dict) -> dict:
            """ピッカーの画面（開閉・パンくず・フォルダ・項目・検索欄）。"""
            v = PK.view(repo, pk_v)
            show_dirs = bool(v["dirs"])
            show_items = bool(v["items"]) or not show_dirs   # フォルダだけ／項目だけのときは、あるほうを全幅にする
            return {c["pk"]: pk_v, c["pk_bar"]: gr.update(open=pk_v["target"] is not None),
                    c["pk_target"]: v["target_md"], c["pk_crumbs"]: gr.update(choices=v["crumbs"], value=v["cwd"]),
                    c["pk_dirs"]: gr.update(choices=v["dirs"], value=None, visible=show_dirs),
                    c["pk_items"]: gr.update(choices=v["items"], value=None, visible=show_items),
                    c["pk_msg"]: v["msg"],
                    c["pk_q"]: pk_v["query"],
                    c["pk_none"]: gr.update(visible=v["can_clear"], value=v["clear_text"] or "空にする")}

        def on_open(target, pk_v, cur, slots_text):
            label = None
            if target.startswith("o") and target[1:].isdigit():
                slots = forms.split_list(slots_text)
                i = int(target[1:])
                label = f"outfits.{slots[i]}" if i < len(slots) else None
            return pk_part(PK.open_picker(repo, pk_v, target, cur or None, label))

        def on_pk_enter(rel, pk_v):
            return pk_part(PK.enter(repo, pk_v, rel)) if rel else {}

        def on_pk_search(q, pk_v):
            return pk_part(PK.set_query(pk_v, q)) if pk_v["target"] else {}

        def on_pk_clear_query(pk_v):
            return pk_part(PK.set_query(pk_v, "")) if pk_v["target"] else {}

        def on_pk_cancel(pk_v):
            return pk_part(PK.close(pk_v))

        def apply_pick(pk_v, st_v, n, args, entity_id: str | None):
            """ピッカーで選んだ値（entity_id。None は空にする）を欄に入れて、ピッカーを閉じる。"""
            t = pk_v["target"]
            if t is None:
                return {}
            kind = PK.TARGETS[t][0]
            out = pk_part(PK.pick(repo, pk_v, entity_id) if entity_id else PK.close(pk_v))
            name = TARGET_STATE[t]
            value = entity_id or ("" if t == "act_bg" else None)
            out |= set_ref(name, kind, value, "outfit", PK.EMPTY_TEXT.get(t, "（未選択）"))
            a = dict(zip(FORM_NAMES, args))
            if name in a:   # フォームの値（プレビューの選択欄は保存対象ではない）
                a[name] = value
                if t in TARGET_MODE:
                    out[c[TARGET_MODE[t]]] = a[TARGET_MODE[t]] = "outfit"
                v = gather(st_v, n, list(a.values()))
                out[c["status"]] = status_text(st_v, v)
                if t == "act_bg" and v is not None:
                    out[c["bbox_img"]] = own_image(st_v["kind"], v)
                out |= diff_values(st_v, v)
            return out

        def on_pk_pick(entity_id, pk_v, st_v, n, *args):
            return apply_pick(pk_v, st_v, n, args, entity_id) if entity_id else {}

        def on_pk_none(pk_v, st_v, n, *args):
            return apply_pick(pk_v, st_v, n, args, None)

        def mode_show(name, mode, cur):
            return "選択中：" + PK.shown(repo, "outfit", cur, mode=mode, empty="（未選択）")

        def on_dirty(st_v, n, *args):
            v = current(st_v, n, args)
            return {c["status"]: status_text(st_v, v)}

        def on_diff(st_v, n, *args):
            """欄のフォーカスが外れたとき・［差分を更新］：今のフォームの値を基準と比べて、差分表示を作り直す（キー入力ごとには動かさない）。"""
            return diff_values(st_v, current(st_v, n, args))

        def on_slots(st_v, n, *args):
            a = dict(zip(FORM_NAMES, args))
            slots = forms.split_list(a["slots"])[:MAX_SLOTS]
            out = {}
            for i in range(MAX_SLOTS):
                out[c[f"o_row{i}"]] = gr.update(visible=i < len(slots))
                if i < len(slots):
                    out[c[f"o_mode{i}"]] = gr.update(label=f"outfits.{slots[i]}")
            return out | slot_help(slots) | on_dirty(st_v, n, *args)

        # カード操作
        def card_op(op: str, i: int, st_v, n, *args):
            a = dict(zip(FORM_NAMES, args))
            kind = st_v["kind"]
            items = card_items(a, n)
            if kind == "background":
                canvas = {"w": int(a["cw"] or DEFAULT_CANVAS["w"]), "h": int(a["ch"] or DEFAULT_CANVAS["h"])}
            else:
                bg = repo.background(a["act_bg"]) if a["act_bg"] else None
                canvas = bg.canvas if bg else DEFAULT_CANVAS
            items = {"add": lambda: C.add_item(items, canvas), "del": lambda: C.delete_item(items, i),
                     "up": lambda: C.move_up(items, i), "down": lambda: C.move_down(items, i),
                     "full": lambda: C.set_full(items, i, canvas)}[op]()
            out = cards_updates(items)
            v = gather(st_v, len(items), args)
            if v is not None:
                v["mossItems"] = items
                out[c["status"]] = status_text(st_v, v)
                out[c["bbox_img"]] = own_image(kind, v)
                out[c["issues"]] = issue_lines(validate_draft(repo, kind, v, new=st_v["mode"] != "edit",
                                                              original_id=st_v["id"]))
                out |= diff_values(st_v, v)
            return out

        # 検証・保存
        def on_check(st_v, n, *args):
            v = current(st_v, n, args)
            if v is None or st_v["mode"] == "folder":
                return {}
            return {c["issues"]: issue_lines(validate_draft(repo, st_v["kind"], v, new=st_v["mode"] != "edit",
                                                            original_id=st_v["id"]))}

        def do_save(confirmed: bool, force: bool, st_v, n, cow_id, *args):
            v = current(st_v, n, args)
            if v is None:
                return {}
            out = hide_boxes()
            if st_v["mode"] == "folder":
                res = save_folder(repo, st_v, v, force=force)
            else:
                res = save_draft(repo, st_v, v, confirmed=confirmed, force=force, cow_id=cow_id)
            if res.status == SAVED:
                return render(res.state, f"💾 {res.message}") | {c["issues"]: issue_lines(res.issues)}
            if res.status == EXTERNAL:
                return out | {boxes["ext"]: gr.update(visible=True), c["save_msg"]: "⚠ " + res.message,
                              c["issues"]: issue_lines(res.issues)}
            if res.status == ERRORS:
                return out | {c["save_msg"]: f"❌ {res.message}", c["issues"]: issue_lines(res.issues)}
            scenes = "\n".join(f"- {name}（`{sid}`）" for name, sid in res.scenes)
            base = D.baseline(st_v)
            changes = D.changes_html(st_v["kind"], v, base) if base is not None else ""
            out |= {boxes["conf"]: gr.update(visible=True), c["conf_scenes"]: "参照している Scene：\n" + scenes,
                    c["conf_diff"]: ("<b>保存前の変更点</b>" + changes) if changes else "",
                    c["issues"]: issue_lines(res.issues)}
            if res.status in (CONFIRM_COW, COW_ID_ERROR):
                warn = f"\n\n❌ {res.message}" if res.status == COW_ID_ERROR else ""
                out |= {c["conf_md"]: ("**" + (res.message if res.status == CONFIRM_COW else
                                               "この背景は Scene で使われているため、複製として保存します") + "**" + warn),
                        c["cow_id"]: gr.update(visible=True, value=res.default_new_id or cow_id),
                        c["conf_ok"]: gr.update(value="複製として保存")}
            else:
                out |= {c["conf_md"]: f"**{res.message}**", c["cow_id"]: gr.update(visible=False),
                        c["conf_ok"]: gr.update(value="保存")}
            return out

        def on_conf_cancel():
            return hide_boxes() | {c["save_msg"]: "保存をキャンセルしました"}

        def on_ext_reload(st_v):
            return render(session.reload_current(repo, st_v), "🔄 ファイルを読み直しました（編集は破棄）")

        def on_copy(st_v, n, *args):
            v = current(st_v, n, args)
            st2 = session.start_copy(repo, st_v, v)
            return render(st2)

        # プレビュー
        def on_use_tree(st_v):
            last = st_v["last"]
            out = {}
            for name, kind in (("pv_act", "action"), ("pv_bg", "background"), ("pv_char", "character")):
                if last.get(kind):
                    out |= set_ref(name, kind, last[kind], empty="（自動）")
            return out

        def on_preview(st_v, n, *args):
            sel = dict(zip(PV_NAMES, args[len(FORM_NAMES):]))
            v = current(st_v, n, args[:len(FORM_NAMES)])
            if v is None or st_v["mode"] == "folder":
                return {}
            res = preview(repo, st_v["kind"], v, original_id=st_v["id"],
                          choices={"action": sel["pv_act"], "slot": sel["pv_slot"], "character": sel["pv_char"],
                                   "background": sel["pv_bg"]})
            out = {c["pv_notes"]: "\n".join(f"- {x}" for x in res.notes), c["pv_issues"]: issue_lines(res.issues),
                   c["pv_summary"]: (f"**実効 rating：`{res.rating}`** ／ **人数タグ：`{res.people_tags or '（なし）'}`**"
                                     if res.ok else ""),
                   c["pv_moss"]: res.moss, c["pv_anima"]: res.anima, c["pv_negm"]: res.negative_moss,
                   c["pv_nega"]: res.negative_anima}
            if res.bg_items or res.action_items:
                out[c["pv_img"]] = draw_bboxes(res.canvas, [("background", res.bg_items), ("action", res.action_items)])
            ch = res.chosen
            if ch:
                out |= set_ref("pv_act", "action", ch["action"], empty="（自動）")
                out |= set_ref("pv_bg", "background", ch["background"], empty="（自動）")
                act = None if st_v["kind"] == "action" else repo.action(ch["action"])
                slots = forms.normalize("action", v)["slots"] if st_v["kind"] == "action" else (
                    list(act.slots) if act else [])
                out[c["pv_slot"]] = gr.update(choices=slots, value=ch["slot"] if ch["slot"] in slots else None)
                if ch.get("character") and st_v["kind"] == "outfit":
                    out |= set_ref("pv_char", "character", ch["character"], empty="（自動）")
            return out

        def on_load(st_v):
            return render(st_v)

        # ---- 配線 ----
        ST_FORM = STATE_IN + FORM
        c["section"].input(on_section, [c["section"]] + ST_FORM, OUT, api_name="section")
        c["lst"].input(on_browse, [c["lst"]] + ST_FORM, OUT, api_name="browse")
        new_btn.click(on_new, ST_FORM, OUT, api_name="new")
        reload_btn.click(on_reload, ST_FORM, OUT, api_name="reload")
        discard_btn.click(on_discard, [c["st"]], OUT, api_name="discard")
        cancel_btn.click(on_cancel, [c["st"]], OUT, api_name="cancel")
        mkdir_btn.click(on_mkdir, [c["folder_tb"], c["st"]], OUT, api_name="mkdir")

        # 共通ピッカー
        for t, name in TARGET_STATE.items():
            c[f"{name}_btn"].click(partial(on_open, t), [c["pk"], c[name], c["slots"]], OUT, api_name=f"pick_{t}")
        c["pk_dirs"].input(on_pk_enter, [c["pk_dirs"], c["pk"]], OUT, api_name="pk_dir")
        c["pk_crumbs"].input(on_pk_enter, [c["pk_crumbs"], c["pk"]], OUT, api_name="pk_crumb")
        c["pk_q"].submit(on_pk_search, [c["pk_q"], c["pk"]], OUT, api_name="pk_search")
        c["pk_search"].click(on_pk_search, [c["pk_q"], c["pk"]], OUT, api_name="pk_search_btn")
        c["pk_clear_q"].click(on_pk_clear_query, [c["pk"]], OUT, api_name="pk_clear_query")
        c["pk_items"].input(on_pk_pick, [c["pk_items"], c["pk"]] + ST_FORM, OUT, api_name="pk_pick")
        c["pk_none"].click(on_pk_none, [c["pk"]] + ST_FORM, OUT, api_name="pk_none")
        c["pk_cancel"].click(on_pk_cancel, [c["pk"]], OUT, api_name="pk_cancel")
        mode_show_pairs = [("def_pick", "def_mode")] + [(f"o_pick{i}", f"o_mode{i}") for i in range(MAX_SLOTS)]
        for name, mode in mode_show_pairs:
            c[mode].input(partial(mode_show, name), [c[mode], c[name]], [c[f"{name}_show"]])

        c["slots"].input(on_slots, ST_FORM, OUT, api_name="slots")
        dirty_triggers = [comp.input for n, comp in zip(FORM_NAMES, FORM) if n != "slots" and n not in STATE_FORM]
        gr.on(dirty_triggers, on_dirty, ST_FORM, OUT)

        diff_btn.click(on_diff, ST_FORM, OUT, api_name="diff")
        for n_, comp in zip(FORM_NAMES, FORM):   # 差分の更新：フォーカスが外れたとき（ラジオは選んだとき）だけ。キー入力ごとには動かさない
            if n_ in STATE_FORM:
                continue
            (comp.input if isinstance(comp, gr.Radio) else comp.blur)(on_diff, ST_FORM, OUT, api_name=f"diff_{n_}")

        c["add_btn"].click(partial(card_op, "add", 0), ST_FORM, OUT, api_name="card_add")
        for i in range(MAX_ITEMS):
            for op in ("up", "down", "del", "full"):
                c[f"c_{op}{i}"].click(partial(card_op, op, i), ST_FORM, OUT, api_name=f"card_{op}_{i}")

        check_btn.click(on_check, ST_FORM, OUT, api_name="check")
        save_btn.click(partial(do_save, False, False), STATE_IN + [c["cow_id"]] + FORM, OUT, api_name="save")
        c["conf_ok"].click(partial(do_save, True, False), STATE_IN + [c["cow_id"]] + FORM, OUT, api_name="confirm")
        ext_over.click(partial(do_save, False, True), STATE_IN + [c["cow_id"]] + FORM, OUT, api_name="overwrite")
        conf_cancel.click(on_conf_cancel, None, OUT, api_name="conf_cancel")
        ext_reload.click(on_ext_reload, [c["st"]], OUT, api_name="ext_reload")
        c["copy_btn"].click(on_copy, ST_FORM, OUT, api_name="copy")

        pv_use.click(on_use_tree, [c["st"]], OUT, api_name="pv_use")
        pv_btn.click(on_preview, ST_FORM + [c[n] for n in PV_NAMES], OUT, api_name="preview")

        demo.load(on_load, [c["st"]], OUT, api_name="init")

    demo.named = c   # 名前 → 部品（テストが画面を操作するときに使う）
    return demo
