"""
"Your image is ready" push, even when the phone app was killed.

When a prompt queued by the Comfy Mobile app ends, ComfyUI's executor calls
PromptServer.send_sync("execution_success" | "execution_error" | "execution_interrupted",
{"prompt_id": ...}, sid=<client_id>). We wrap send_sync, and for prompts whose client_id
is the app's ("COMFY_MOBILE…") we POST {target, promptId, status} to the Comfy Mobile
Worker for every target the app registered. The Worker sends the FCM message.

Fire-and-forget: one daemon thread, bounded queue, 5 s timeout. Never blocks or breaks
ComfyUI; every failure is swallowed (and logged at debug level).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Callable, Iterable, List, Optional

logger = logging.getLogger("comfymobile_link")

DEFAULT_RELAY_URL = "https://comfymobile-api.seosnapshot.workers.dev/v1/push/notify"
APP_CLIENT_ID_PREFIX = "COMFY_MOBILE"

EVENT_STATUS = {
    "execution_success": "success",
    "execution_error": "error",
    "execution_interrupted": "interrupted",
}


def relay_url() -> str:
    return os.environ.get("COMFYMOBILE_PUSH_URL", DEFAULT_RELAY_URL)


def push_all_prompts() -> bool:
    """COMFYMOBILE_PUSH_ALL=1 also pushes for prompts queued from the browser."""
    return os.environ.get("COMFYMOBILE_PUSH_ALL", "").strip() in ("1", "true", "yes")


def classify_event(event: object, data: object, sid: object, include_all: bool = False) -> Optional[tuple]:
    """(prompt_id, status) when this send_sync call should trigger a push, else None. Pure."""
    status = EVENT_STATUS.get(event) if isinstance(event, str) else None
    if status is None or not isinstance(data, dict):
        return None
    prompt_id = data.get("prompt_id")
    if not isinstance(prompt_id, str) or not prompt_id:
        return None
    if not include_all and not (isinstance(sid, str) and sid.startswith(APP_CLIENT_ID_PREFIX)):
        return None
    return prompt_id, status


class PushRelay:
    """Single background sender. `post` is injectable for tests."""

    def __init__(
        self,
        targets: Callable[[], List[str]],
        on_target_gone: Callable[[str], None],
        post: Optional[Callable[[str, dict], int]] = None,
        url: Optional[str] = None,
        max_queue: int = 64,
    ):
        self._targets = targets
        self._on_gone = on_target_gone
        self._post = post or _http_post
        self._url = url or relay_url()
        self._q: "queue.Queue[tuple]" = queue.Queue(maxsize=max_queue)
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self._seen_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def notify(self, prompt_id: str, status: str) -> bool:
        """Queue a push for every registered target. Returns False if nothing was queued."""
        with self._seen_lock:
            if prompt_id in self._seen:
                return False
            self._seen[prompt_id] = None
            while len(self._seen) > 256:
                self._seen.popitem(last=False)
        targets = self._targets()
        if not targets:
            return False
        try:
            self._q.put_nowait((prompt_id, status, tuple(targets)))
        except queue.Full:
            logger.debug("[Comfy Mobile Link] push queue full, dropping %s", prompt_id)
            return False
        self._ensure_thread()
        return True

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="comfymobile-push", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=30)
            except queue.Empty:
                return  # idle: let the thread end; the next notify starts a new one
            self.deliver(*item)

    def deliver(self, prompt_id: str, status: str, targets: Iterable[str]) -> None:
        for target in targets:
            body = {"target": target, "promptId": prompt_id, "status": status}
            try:
                code = self._post(self._url, body)
            except Exception as e:  # never let anything escape into ComfyUI
                logger.debug("[Comfy Mobile Link] push failed: %s", e)
                continue
            if code in (404, 410):
                # The app uninstalled / unregistered: stop sending to it.
                try:
                    self._on_gone(target)
                except Exception:
                    pass

    def drain_for_tests(self) -> None:
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return
            self.deliver(*item)


def _http_post(url: str, body: dict) -> int:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "user-agent": "comfymobile-link"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def wrap_send_sync(server, relay: PushRelay, include_all: Optional[Callable[[], bool]] = None) -> bool:
    """Wrap server.send_sync once. Returns False if it was already wrapped / unavailable."""
    original = getattr(server, "send_sync", None)
    if original is None or getattr(original, "_comfymobile_wrapped", False):
        return False
    include_all = include_all or push_all_prompts

    def send_sync(event, data, sid=None):
        try:
            return original(event, data, sid)
        finally:
            try:
                hit = classify_event(event, data, sid, include_all())
                if hit:
                    relay.notify(*hit)
            except Exception:
                pass

    send_sync._comfymobile_wrapped = True  # type: ignore[attr-defined]
    server.send_sync = send_sync
    return True
