"""PC 以外からの接続の判定（EDITOR_SPEC 15.1章）。

フォルダ選択のダイアログは、エディタを動かしている PC の画面に出る。ほかの端末から開いているときに押されても、
その端末には何も出ずに PC 側で待ち続けてしまうので、PC 以外（127.0.0.1 / ::1 以外）の接続ではボタンを無効にする。
"""
from __future__ import annotations

LOCAL_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
REMOTE_REASON = ("フォルダ選択のダイアログは、エディタを動かしている PC の画面に出るため、"
                 "PC 以外からの接続では使えません（zip・yaml のアップロードは使えます）")


def is_local_client(host: str | None) -> bool:
    """接続元が、エディタを動かしている PC 自身か。確認できないとき（None）は False（安全側）。"""
    return (host or "").strip().lower() in LOCAL_HOSTS


def client_host(request) -> str | None:
    """gr.Request（または同じ形のもの）から接続元のアドレスを取る。取れなければ None。"""
    client = getattr(request, "client", None)
    return getattr(client, "host", None)


def folder_dialog_blocked(request) -> str:
    """フォルダ選択を使えない理由。使えるなら空文字。"""
    host = client_host(request)
    if is_local_client(host):
        return ""
    return REMOTE_REASON + (f"（接続元：{host}）" if host else "（接続元を確認できません）")
