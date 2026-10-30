"""ComfyUI の API クライアント：HTTP（/prompt, /view, /object_info, /interrupt）と WebSocket（/ws の進捗）。"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Callable, ContextManager, Iterator

import httpx


class ComfyError(Exception):
    """利用者に見せてよい日本語メッセージを持つ例外。"""


def _format_rejection(body: dict) -> str:
    err = body.get("error") or {}
    head = f"ComfyUI がワークフローを受け付けませんでした：{err.get('message', '')} {err.get('details', '')}".strip()
    lines = [head]
    for nid, ne in (body.get("node_errors") or {}).items():
        for e in ne.get("errors", []):
            lines.append(f"  ノード {nid}（{ne.get('class_type')}）：{e.get('message', '')} {e.get('details', '')}".rstrip())
    return "\n".join(lines)


class EventStream:
    """WebSocket のイベントを1つずつ返す。`get` はタイムアウトしたら None（テストでは差し替えられる）。"""

    def __init__(self, ws):
        self._ws = ws

    def get(self, timeout: float) -> dict | None:
        from websockets.exceptions import ConnectionClosed
        try:
            msg = self._ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        except ConnectionClosed:
            raise ComfyError("ComfyUI との WebSocket 接続が切れました") from None
        if isinstance(msg, bytes):  # プレビュー画像などのバイナリは使わない
            return {"type": "binary"}
        try:
            return json.loads(msg)
        except ValueError:
            return {"type": "unknown"}


class ComfyClient:
    def __init__(self, url: str, timeout_sec: float = 600,
                 event_source: Callable[[], ContextManager[EventStream]] | None = None):
        self.url = url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.client_id = uuid.uuid4().hex
        self._event_source = event_source  # テスト用：WebSocket の代わりに使うイベント列
        # localhost への接続がシステムのプロキシ設定に影響されないようにする
        self.http = httpx.Client(base_url=self.url, timeout=30, trust_env=False)

    # ---- HTTP ----
    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            return self.http.request(method, path, **kw)
        except httpx.ConnectError:
            raise ComfyError(f"ComfyUI（{self.url}）に接続できません。起動しているか、config.yaml の comfyui.url を確認してください") from None
        except httpx.TimeoutException:
            raise ComfyError(f"ComfyUI（{self.url}）の応答がありません（タイムアウト）") from None

    def submit(self, workflow: dict) -> str:
        r = self._request("POST", "/prompt", json={"prompt": workflow, "client_id": self.client_id})
        if r.status_code != 200:
            try:
                raise ComfyError(_format_rejection(r.json()))
            except ValueError:
                raise ComfyError(f"ComfyUI がエラーを返しました（HTTP {r.status_code}）：{r.text[:300]}") from None
        body = r.json()
        if body.get("node_errors"):
            raise ComfyError(_format_rejection(body))
        return body["prompt_id"]

    def fetch_image(self, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        r = self._request("GET", "/view", params={"filename": filename, "subfolder": subfolder, "type": type_})
        if r.status_code != 200:
            raise ComfyError(f"画像 {filename} を取得できません（HTTP {r.status_code}）")
        return r.content

    def interrupt(self) -> None:
        """実行中のジョブを中断する（段のタイムアウトのとき）。失敗しても例外にしない。"""
        try:
            self.http.post("/interrupt")
        except httpx.HTTPError:
            pass

    def cancel_queued(self, prompt_id: str) -> None:
        """まだ始まっていないジョブをキューから外す。失敗しても例外にしない。"""
        try:
            self.http.post("/queue", json={"delete": [prompt_id]})
        except httpx.HTTPError:
            pass

    def input_max(self, class_type: str, input_name: str) -> int | None:
        """ノードクラスの入力の最大値（`/object_info`）。取れなければ None。"""
        try:
            r = self.http.get(f"/object_info/{class_type}")
            spec = r.json()[class_type]["input"]
            for section in ("required", "optional"):
                if input_name in spec.get(section, {}):
                    cfg = spec[section][input_name]
                    return int(cfg[1]["max"]) if len(cfg) > 1 and "max" in cfg[1] else None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            pass
        return None

    # ---- WebSocket ----
    @contextmanager
    def events(self) -> Iterator[EventStream]:
        """進捗イベントを購読する。**`submit` の前に開く**こと（始まりのイベントを取りこぼさないため）。"""
        if self._event_source is not None:
            with self._event_source() as stream:
                yield stream
            return
        from websockets.exceptions import WebSocketException
        from websockets.sync.client import connect
        ws_url = ("wss" if self.url.startswith("https") else "ws") + self.url[self.url.index("://"):]
        try:
            ws = connect(f"{ws_url}/ws?clientId={self.client_id}", open_timeout=10, max_size=None)
        except (OSError, TimeoutError, WebSocketException) as e:
            raise ComfyError(f"ComfyUI の WebSocket（{ws_url}/ws）に接続できません：{e}") from None
        try:
            yield EventStream(ws)
        finally:
            ws.close()
