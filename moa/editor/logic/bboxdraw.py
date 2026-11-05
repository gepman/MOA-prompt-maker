"""bbox プレビュー図（EDITOR_SPEC 8章。表示専用）。キャンバスの枠と中心線の上に、各 bbox を四角と番号で描く。"""
from __future__ import annotations

from PIL import Image, ImageDraw

WIDTH = 512
COLORS = {"background": "#2f7fd8", "action": "#e08a1e"}
WARN = "#d62828"


OUTSIDE = "#b5b5b5"   # 画像の外（余白）
REF = "#9dbde6"       # Action 編集中の、背景の mossItems（操作できない参考の線）


def draw_base(frame, ref_items=()) -> Image.Image:
    """bbox 編集図（EDITOR_SPEC 8.1）の土台。余白はグレー、キャンバスは白で濃い枠、中心線、背景の参考線（薄い色・番号なし）。"""
    from .bboxedit import to_image

    img = Image.new("RGB", (frame.width, frame.height), OUTSIDE)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = frame.canvas_rect
    d.rectangle([x0, y0, x1, y1], fill="white")
    d.line([frame.ox, y0, frame.ox, y1], fill="#cccccc", width=2)
    d.line([x0, frame.oy, x1, frame.oy], fill="#cccccc", width=2)
    thick = max(2, round(frame.width / 300))
    for it in ref_items:
        by0, bx0, by1, bx1 = it["bbox"]
        ax, ay, bx, by = to_image([min(by0, by1), min(bx0, bx1), max(by0, by1), max(bx0, bx1)], frame)
        d.rectangle([ax, ay, bx, by], outline=REF, width=thick)
    d.rectangle([x0, y0, x1, y1], outline="#222222", width=thick)
    return img


def _out_of_canvas(bbox, w, h) -> bool:
    y0, x0, y1, x1 = bbox
    return x0 < -w / 2 or x1 > w / 2 or y0 < -h / 2 or y1 > h / 2


def draw_bboxes(canvas: dict, groups: list[tuple[str, list[dict]]]) -> Image.Image:
    """groups: [("background" | "action", mossItems のフォーム値のリスト)]。はみ出し・大小逆転は警告色で描く。"""
    w, h = int(canvas["w"]), int(canvas["h"])
    scale = WIDTH / w
    W, H = WIDTH, max(1, round(h * scale))
    margin = 24   # はみ出しを描ける余白
    img = Image.new("RGB", (W + margin * 2, H + margin * 2), "#f4f4f4")
    d = ImageDraw.Draw(img)

    def px(x, y):
        return margin + (x + w / 2) * scale, margin + (y + h / 2) * scale

    d.rectangle([margin, margin, margin + W, margin + H], fill="white", outline="#333333")
    d.line([margin + W / 2, margin, margin + W / 2, margin + H], fill="#cccccc")
    d.line([margin, margin + H / 2, margin + W, margin + H / 2], fill="#cccccc")
    for source, items in groups:
        for n, it in enumerate(items, 1):
            y0, x0, y1, x1 = it["bbox"]
            bad = y0 > y1 or x0 > x1 or _out_of_canvas(it["bbox"], w, h)
            color = WARN if bad else COLORS[source]
            (ax, ay), (bx, by) = px(min(x0, x1), min(y0, y1)), px(max(x0, x1), max(y0, y1))
            d.rectangle([ax, ay, bx, by], outline=color, width=2)
            d.text((ax + 3, ay + 2), f"{'B' if source == 'background' else 'A'}{n}", fill=color)
    return img
