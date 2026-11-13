"""
Comfy Mobile Link — pair the Comfy Mobile Android app with this ComfyUI by QR code,
convert GUI workflows to API prompts, and push "Your image is ready" to the phone.

Adds no nodes to the graph; everything lives in HTTP routes + a sidebar panel.
"""

import logging

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

try:
    from server import PromptServer  # type: ignore

    from .comfymobile_link import routes as _routes

    _routes.setup(PromptServer.instance)
except ImportError:
    # Imported outside ComfyUI (tests, packaging): nothing to register.
    pass
except Exception as e:  # never break ComfyUI startup
    logging.getLogger("comfymobile_link").error("[Comfy Mobile Link] failed to start: %s", e)
