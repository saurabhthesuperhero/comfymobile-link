"""
aiohttp routes on ComfyUI's PromptServer:

  GET  /comfymobile/info            version, LAN URLs, port, --listen check, converter,
                                    push target count, `paired` (X-ComfyMobile-Token check)
  GET  /comfymobile/pairing         QR payload + SVG for the "Comfy Mobile" panel
  POST /comfymobile/pairing/reset   new token, forget push targets
  POST /comfymobile/convert         GUI workflow JSON → API prompt JSON (body = workflow)
  POST /comfymobile/register_push   {target} from the app (opaque Worker id)
  POST /comfymobile/unregister_push {target}
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Optional

from aiohttp import web

from . import pairing as P
from .converter import WorkflowConverter
from .push import PushRelay, relay_url, wrap_send_sync

logger = logging.getLogger("comfymobile_link")

VERSION = "1.0.0"
MAX_CONVERT_BYTES = 8 * 1024 * 1024
TOKEN_HEADER = "X-ComfyMobile-Token"

_state: Optional[P.PairingState] = None
_relay: Optional[PushRelay] = None


def _cli_args():
    try:
        from comfy.cli_args import args  # type: ignore
        return args
    except Exception:
        return None


def _listen_and_port(server) -> tuple:
    args = _cli_args()
    listen = getattr(args, "listen", None) if args else None
    port = getattr(args, "port", None) if args else None
    port = port or getattr(server, "port", None) or 8188
    https = bool(args and getattr(args, "tls_keyfile", None) and getattr(args, "tls_certfile", None))
    return P.parse_listen(listen), int(port), https


def _state_path() -> str:
    try:
        import folder_paths  # type: ignore
        base = folder_paths.get_user_directory()
    except Exception:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "comfymobile_link.json")


def _hostname() -> str:
    try:
        return socket.gethostname().split(".")[0][:40] or "ComfyUI"
    except Exception:
        return "ComfyUI"


def _info(server, presented_token: Optional[str]) -> dict:
    status, port, https = _listen_and_port(server)
    urls = P.build_urls(status, P.discover_lan_ips(), port, https)
    return {
        "name": "Comfy Mobile Link",
        "version": VERSION,
        "hostname": _hostname(),
        "port": port,
        "urls": urls,
        "listen": {
            "addresses": status.addresses,
            "allInterfaces": status.all_interfaces,
            "lanReachable": status.lan_reachable,
            "fix": P.listen_fix_text(status, port),
        },
        "converter": True,
        "push": {"targets": len(_state.targets) if _state else 0, "relay": bool(relay_url())},
        "paired": P.token_matches(_state.token, presented_token) if _state else None,
    }


def setup(server) -> None:
    """Register routes + the push hook on a PromptServer instance. Idempotent enough for one load."""
    global _state, _relay
    _state = P.PairingState(_state_path())
    _relay = PushRelay(targets=_state.snapshot_targets, on_target_gone=_state.remove_target)
    try:
        wrap_send_sync(server, _relay)
    except Exception as e:  # pragma: no cover
        logger.warning("[Comfy Mobile Link] push hook unavailable: %s", e)

    routes = server.routes

    @routes.get("/comfymobile/info")
    async def info(request):
        token = request.headers.get(TOKEN_HEADER) or request.query.get("token_hash")
        return web.json_response(_info(server, token))

    @routes.get("/comfymobile/pairing")
    async def pairing(request):
        data = _info(server, None)
        payload = P.build_payload(data["hostname"], data["urls"], _state.token)
        text = P.payload_text(payload)
        svg = P.qr_svg(text) if data["urls"] else None
        return web.json_response({**data, "payload": payload, "payloadText": text, "svg": svg})

    @routes.post("/comfymobile/pairing/reset")
    async def pairing_reset(request):
        _state.reset()
        return web.json_response({"ok": True})

    @routes.post("/comfymobile/convert")
    async def convert(request):
        if request.content_length is not None and request.content_length > MAX_CONVERT_BYTES:
            return web.json_response({"error": "Workflow too large"}, status=413)
        try:
            workflow = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        if not isinstance(workflow, dict):
            return web.json_response({"error": "Workflow must be a JSON object"}, status=400)
        if WorkflowConverter.is_api_format(workflow):
            return web.json_response(workflow)
        if not isinstance(workflow.get("nodes"), list) or not isinstance(workflow.get("links"), list):
            return web.json_response({"error": "Invalid workflow format - missing nodes or links"}, status=400)
        try:
            api = WorkflowConverter.convert_to_api(workflow)
        except Exception as e:
            logger.exception("[Comfy Mobile Link] convert failed")
            return web.json_response({"error": f"Conversion failed: {type(e).__name__}"}, status=500)
        return web.json_response(api, dumps=lambda x: json.dumps(x, ensure_ascii=False))

    @routes.post("/comfymobile/register_push")
    async def register_push(request):
        try:
            body = await request.json()
        except Exception:
            body = None
        target = body.get("target") if isinstance(body, dict) else None
        if not _state.add_target(target):
            return web.json_response({"ok": False, "error": "invalid target"}, status=400)
        return web.json_response({"ok": True, "targets": len(_state.targets)})

    @routes.post("/comfymobile/unregister_push")
    async def unregister_push(request):
        try:
            body = await request.json()
        except Exception:
            body = None
        target = body.get("target") if isinstance(body, dict) else None
        removed = _state.remove_target(target) if isinstance(target, str) else False
        return web.json_response({"ok": True, "removed": removed})

    status, port, _ = _listen_and_port(server)
    if not status.lan_reachable:
        logger.warning("[Comfy Mobile Link] ComfyUI listens on %s only; phones can't connect. Start with --listen.",
                       ",".join(status.addresses))
    logger.info("[Comfy Mobile Link] v%s ready: /comfymobile/info, /comfymobile/convert (port %s)", VERSION, port)
