"""ビルダーの CSS（SPEC 10.1章）。プロンプト欄と差分欄だけに効く専用クラス `moa-prompt` / `moa-diff` で指定する。"""
from __future__ import annotations

DEFAULT_PROMPT_FONT_PX = 16
TEXTBOX_ID = "moa-tb-{}"   # 4つの編集欄の elem_id（{} は moss / anima / negm / nega）

JUMP_JS = r"""
(function () {
  if (window.__moaJump) return;
  window.__moaJump = true;
  function caretBox(ta, pos) {                      // 同じ見た目の隠し要素で、カーソル位置の行の上端と高さを測る
    var cs = getComputedStyle(ta), d = document.createElement('div');
    ['boxSizing', 'width', 'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'lineHeight',
     'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderRightWidth',
     'borderBottomWidth', 'borderLeftWidth', 'tabSize', 'wordSpacing'].forEach(function (p) { d.style[p] = cs[p]; });
    d.style.position = 'absolute'; d.style.visibility = 'hidden'; d.style.left = '-9999px'; d.style.top = '0';
    d.style.whiteSpace = 'pre-wrap'; d.style.wordWrap = 'break-word'; d.style.overflow = 'hidden';
    d.textContent = ta.value.substring(0, pos);
    var m = document.createElement('span'); m.textContent = '​'; d.appendChild(m);
    document.body.appendChild(d);
    var box = {top: m.offsetTop, h: m.offsetHeight || parseFloat(cs.lineHeight) || 20};
    document.body.removeChild(d);
    return box;
  }
  function scroller(el) {                           // ページ側でスクロールする入れ物（なければ window）
    for (var p = el.parentElement; p; p = p.parentElement) {
      var oy = getComputedStyle(p).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && p.scrollHeight > p.clientHeight) return p;
    }
    return null;
  }
  document.addEventListener('click', function (e) {
    var el = e.target.closest && e.target.closest('[data-moa-pos]');
    if (!el) return;
    var ta = document.querySelector('#moa-tb-' + el.getAttribute('data-moa-field') + ' textarea');
    if (!ta) return;
    e.preventDefault();
    var cps = Array.from(ta.value);                 // 位置はコードポイント単位。textarea は UTF-16 単位なので数え直す
    var cp = Math.min(parseInt(el.getAttribute('data-moa-pos'), 10), cps.length);
    var pos = cps.slice(0, cp).join('').length;
    ta.focus({preventScroll: true});
    ta.setSelectionRange(pos, pos);
    var box = caretBox(ta, pos);
    ta.scrollTop = Math.max(0, Math.min(ta.scrollHeight - ta.clientHeight, box.top + box.h / 2 - ta.clientHeight / 2));
    var r = ta.getBoundingClientRect();
    var caretY = r.top + (box.top - ta.scrollTop) + box.h / 2;   // 画面上のカーソルの高さ
    var sc = scroller(ta);
    if (sc) sc.scrollBy(0, caretY - (sc.getBoundingClientRect().top + sc.clientHeight / 2));
    else window.scrollBy(0, caretY - window.innerHeight / 2);
  });
})();
"""


def prompt_css(px: int | float = DEFAULT_PROMPT_FONT_PX) -> str:
    return f""".moa-prompt textarea, .moa-prompt .moa-diff {{ font-size: {px}px !important; line-height: 1.7 !important; }}
.moa-diff {{ white-space: pre-wrap; word-break: break-word; padding: 8px 10px; border: 1px solid #c8c8c8; border-radius: 6px; }}
.moa-diff .moa-head {{ font-weight: 700; margin-top: 0.4em; }}
.moa-diff .moa-indent {{ padding-left: 1.2em; }}
.moa-diff .moa-mark {{ display: inline-block; width: 1.2em; font-weight: 700; }}
.moa-diff .moa-jump {{ cursor: pointer; }}
.moa-diff .moa-jump:hover {{ outline: 2px solid #3b82f6; }}
.moa-diff .moa-go {{ font-size: 0.75em; margin-left: 0.6em; padding: 0 0.5em; border: 1px solid #999; border-radius: 4px; cursor: pointer; background: transparent; }}
.moa-diff ins.moa-ins {{ background: #c9f0d0; color: #14532d; font-weight: 700; text-decoration: none; }}
.moa-diff del.moa-del {{ color: #777; text-decoration: line-through; }}
.moa-diff .moa-note {{ color: #666; font-size: 0.8em; }}
.dark .moa-diff {{ border-color: #555; }}
.dark .moa-diff ins.moa-ins {{ background: #1f5b33; color: #d6f5dc; }}
.dark .moa-diff del.moa-del {{ color: #aaa; }}
.dark .moa-diff .moa-note {{ color: #aaa; }}
"""
