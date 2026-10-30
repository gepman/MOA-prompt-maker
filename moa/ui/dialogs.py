"""OSのフォルダ選択ダイアログ（ローカル実行前提）。"""
import subprocess
import sys

# tkinter は別プロセスで動かす（Gradioのワーカースレッドから直接Tkを使うと不安定なため）
_CODE = """
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk()
root.withdraw()
root.attributes('-topmost', True)
path = filedialog.askdirectory(title='生成画像のコピー先フォルダ', initialdir=sys.argv[1] or None, mustexist=False)
sys.stdout.buffer.write(path.encode('utf-8'))
"""


def pick_folder(initial: str = "") -> str | None:
    """選ばれたフォルダのパス。キャンセルなら None。"""
    proc = subprocess.run([sys.executable, "-c", _CODE, initial or ""],
                          capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or "フォルダ選択ダイアログを開けませんでした")
    path = proc.stdout.decode("utf-8").strip()
    return path.replace("/", "\\") if path and sys.platform == "win32" else (path or None)
