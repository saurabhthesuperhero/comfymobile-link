# Comfy Mobile Link

Companion node for the **Comfy Mobile** Android app
([Google Play](https://play.google.com/store/apps/details?id=com.saikaizeninnovations.comfymobile)).

- **Pair in 30 seconds.** A "Comfy Mobile" sidebar panel shows a QR code with this
  computer's LAN address, port and a pairing code. In the app: Home → **Pair with QR**.
  No more "localhost / wrong IP / which port".
- **Listen check.** If ComfyUI only listens on `127.0.0.1`, the panel tells you exactly
  what to change (`--listen`, or Host = 0.0.0.0 in ComfyUI Desktop).
- **Built-in workflow converter.** `POST /comfymobile/convert` turns a GUI workflow
  (`nodes` + `links`, subgraphs included) into an API prompt using this server's own node
  definitions. You no longer need a separate converter plugin.
- **"Your image is ready" push, even when the app is closed** (Comfy Mobile Premium).
  When a job queued from the app finishes, this node sends `{target, promptId, status}`
  to the Comfy Mobile relay, which forwards a notification to your phone.

Adds no nodes to your graphs and has **no pip dependencies**.

## Install

ComfyUI-Manager → Custom Nodes Manager → search **Comfy Mobile Link** → Install → restart.

Or manually:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/saurabhthesuperhero/comfymobile-link
```

Start ComfyUI so your phone can reach it:

```bash
python main.py --listen            # manual / portable
```

ComfyUI Desktop: Settings → Server-Config → Host `0.0.0.0`, restart.
Allow Python/ComfyUI through your firewall on private networks.

## HTTP API

| Route | Purpose |
| --- | --- |
| `GET /comfymobile/info` | version, LAN URLs, port, `listen` check + fix, `converter: true`, push target count, `paired` (send the pairing code in `X-ComfyMobile-Token`) |
| `GET /comfymobile/pairing` | the QR payload `{"v":1,"name","urls":[…],"token"}` and its SVG (used by the panel) |
| `POST /comfymobile/pairing/reset` | new pairing code; forgets registered phones |
| `POST /comfymobile/convert` | body = GUI workflow JSON → API prompt JSON (API-format input is returned unchanged) |
| `POST /comfymobile/register_push` | `{"target": "<opaque id from the Comfy Mobile relay>"}` |
| `POST /comfymobile/unregister_push` | `{"target": "…"}` |

## Privacy

- Only prompts queued **by the Comfy Mobile app** (client id `COMFY_MOBILE…`) trigger a push.
  Set `COMFYMOBILE_PUSH_ALL=1` to also push for jobs queued in the browser.
- A push sends only an opaque target id, the prompt id and `success` / `error` /
  `interrupted` to `https://comfymobile-api.seosnapshot.workers.dev/v1/push/notify`
  (override with `COMFYMOBILE_PUSH_URL`). No images, prompts or workflow data leave your
  computer. Nothing is sent until a phone registers for push.
- State (pairing code, registered targets) is stored in `ComfyUI/user/comfymobile_link.json`.
- ComfyUI itself has no password. The pairing code identifies your server to the app; it is
  not access control. Keep ComfyUI on a network you trust (or use Tailscale).

## Development

```bash
python -m pip install pytest aiohttp
python -m pytest -q tests
```

## Credits

- Workflow converter ported from Seth A. Robinson's
  [comfyui-workflow-to-api-converter-endpoint](https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint) (The Unlicense).
- QR encoder: [Project Nayuki QR Code generator](https://www.nayuki.io/page/qr-code-generator-library) (MIT).

License: MIT.
