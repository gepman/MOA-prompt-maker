"""ComfyUI の HTTP API クライアント（/prompt, /history, /queue, /view）。"""
from __future__ import annotations

import time
import uuid
from typing import Generator

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


class ComfyClient:
    def __init__(self, url: str, timeout_sec: float = 600, poll_interval: float = 1.0):
        self.url = url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.poll_interval = poll_interval
        self.client_id = uuid.uuid4().hex
        # localhost への接続がシステムのプロキシ設定に影響されないようにする
        self.http = httpx.Client(base_url=self.url, timeout=30, trust_env=False)

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

    def iter_wait(self, prompt_id: str) -> Generator[str, None, dict]:
        """完了まで待つ。経過を文字列で yield し、最後に history の outputs を返す。"""
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            if elapsed > self.timeout_sec:
                raise ComfyError(f"{self.timeout_sec:.0f}秒待っても完了しませんでした。ComfyUI側では実行が続いている可能性があります（prompt_id: {prompt_id}）")
            hist = self._request("GET", f"/history/{prompt_id}").json()
            entry = hist.get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    msgs = [m[1].get("exception_message", "") for m in status.get("messages", [])
                            if isinstance(m, list) and len(m) == 2 and isinstance(m[1], dict)]
                    raise ComfyError("ComfyUI での実行中にエラーが発生しました：" + " ".join(m for m in msgs if m).strip())
                if status.get("completed", True) or entry.get("outputs"):
                    return entry.get("outputs", {})
            q = self._request("GET", "/queue").json()
            pending = [item[1] for item in q.get("queue_pending", [])]
            if prompt_id in pending:
                state = f"キュー待ち（{pending.index(prompt_id) + 1}番目）"
            else:
                state = "実行中"
            yield f"{state}… {elapsed:.0f}秒"
            time.sleep(self.poll_interval)

    def fetch_image(self, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        r = self._request("GET", "/view", params={"filename": filename, "subfolder": subfolder, "type": type_})
        if r.status_code != 200:
            raise ComfyError(f"画像 {filename} を取得できません（HTTP {r.status_code}）")
        return r.content
