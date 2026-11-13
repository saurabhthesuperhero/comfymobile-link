"""Route smoke test with a fake PromptServer (needs aiohttp, which ComfyUI ships)."""

import asyncio
import types

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from comfymobile_link import converter as C  # noqa: E402
from comfymobile_link import pairing as P  # noqa: E402
from comfymobile_link import routes as R  # noqa: E402


class FakePromptServer:
    def __init__(self):
        self.routes = web.RouteTableDef()
        self.port = 8188
        self.sent = []

    def send_sync(self, event, data, sid=None):
        self.sent.append(event)


def test_routes_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_state_path", lambda: str(tmp_path / "state.json"))
    monkeypatch.setattr(R, "_cli_args", lambda: types.SimpleNamespace(listen="0.0.0.0,::", port=8188,
                                                                       tls_keyfile=None, tls_certfile=None))
    monkeypatch.setattr(P, "discover_lan_ips", lambda: ["192.168.1.9"])
    C.set_nodes_module(types.SimpleNamespace(NODE_CLASS_MAPPINGS={}, NODE_DISPLAY_NAME_MAPPINGS={}))
    server = FakePromptServer()
    R.setup(server)
    app = web.Application()
    app.add_routes(server.routes)

    async def run():
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            info = await (await client.get("/comfymobile/info")).json()
            assert info["urls"] == ["http://192.168.1.9:8188"]
            assert info["listen"]["lanReachable"] is True and info["converter"] is True
            assert info["paired"] is None

            pairing = await (await client.get("/comfymobile/pairing")).json()
            assert pairing["payload"]["urls"] == ["http://192.168.1.9:8188"]
            assert pairing["svg"].startswith("<svg")
            token = pairing["payload"]["token"]

            ok = await (await client.get("/comfymobile/info", headers={R.TOKEN_HEADER: token})).json()
            assert ok["paired"] is True
            bad = await (await client.get("/comfymobile/info", headers={R.TOKEN_HEADER: "nope"})).json()
            assert bad["paired"] is False

            r = await client.post("/comfymobile/register_push", json={"target": "T" * 43})
            assert r.status == 200 and (await r.json())["targets"] == 1
            r = await client.post("/comfymobile/register_push", json={"target": "bad target"})
            assert r.status == 400
            r = await client.post("/comfymobile/unregister_push", json={"target": "T" * 43})
            assert (await r.json())["removed"] is True

            api = {"1": {"class_type": "SaveImage", "inputs": {}}}
            r = await client.post("/comfymobile/convert", json=api)
            assert r.status == 200 and await r.json() == api
            r = await client.post("/comfymobile/convert", json={})
            assert r.status == 400
            r = await client.post("/comfymobile/convert", data=b"not json", headers={"content-type": "application/json"})
            assert r.status == 400
        finally:
            await client.close()

    asyncio.run(run())
    # The push hook is installed and still forwards to the original send_sync.
    server.send_sync("execution_success", {"prompt_id": "p"}, "COMFY_MOBILE")
    assert server.sent == ["execution_success"]
    C.set_nodes_module(None)
