"""Converter tests against a fake `nodes` module (no ComfyUI import)."""

import types

import pytest

from comfymobile_link import converter as C
from comfymobile_link.converter import WorkflowConverter


def _node(required, optional=None, output_node=False):
    spec = {"required": required}
    if optional:
        spec["optional"] = optional

    class N:
        OUTPUT_NODE = output_node

        @classmethod
        def INPUT_TYPES(cls):
            return spec

    return N


FAKE = types.SimpleNamespace(
    NODE_CLASS_MAPPINGS={
        "CheckpointLoaderSimple": _node({"ckpt_name": (["a.safetensors", "b.safetensors"],)}),
        "CLIPTextEncode": _node({"text": ("STRING", {"multiline": True}), "clip": ("CLIP",)}),
        "EmptyLatentImage": _node({
            "width": ("INT", {"default": 512}), "height": ("INT", {"default": 512}),
            "batch_size": ("INT", {"default": 1}),
        }),
        "KSampler": _node({
            "model": ("MODEL",),
            "seed": ("INT", {"default": 0, "control_after_generate": True}),
            "steps": ("INT", {"default": 20}),
            "cfg": ("FLOAT", {"default": 8.0}),
            "sampler_name": (["euler", "dpmpp_2m"],),
            "scheduler": (["normal", "karras"],),
            "positive": ("CONDITIONING",),
            "negative": ("CONDITIONING",),
            "latent_image": ("LATENT",),
            "denoise": ("FLOAT", {"default": 1.0}),
        }),
        "VAEDecode": _node({"samples": ("LATENT",), "vae": ("VAE",)}),
        "SaveImage": _node({"images": ("IMAGE",), "filename_prefix": ("STRING", {"default": "ComfyUI"})}, output_node=True),
    },
    NODE_DISPLAY_NAME_MAPPINGS={"KSampler": "KSampler"},
)


@pytest.fixture(autouse=True)
def fake_nodes():
    C.set_nodes_module(FAKE)
    yield
    C.set_nodes_module(None)


def _txt2img():
    return {
        "nodes": [
            {"id": 4, "type": "CheckpointLoaderSimple", "mode": 0, "inputs": [],
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}, {"name": "CLIP", "type": "CLIP", "links": [3, 5]},
                         {"name": "VAE", "type": "VAE", "links": [8]}],
             "widgets_values": ["b.safetensors"]},
            {"id": 6, "type": "CLIPTextEncode", "mode": 0, "inputs": [{"name": "clip", "type": "CLIP", "link": 3}],
             "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [4]}], "widgets_values": ["a cat"]},
            {"id": 7, "type": "CLIPTextEncode", "mode": 0, "inputs": [{"name": "clip", "type": "CLIP", "link": 5}],
             "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [6]}], "widgets_values": ["blurry"]},
            {"id": 5, "type": "EmptyLatentImage", "mode": 0, "inputs": [],
             "outputs": [{"name": "LATENT", "type": "LATENT", "links": [2]}], "widgets_values": [768, 512, 1]},
            {"id": 3, "type": "KSampler", "mode": 0,
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}, {"name": "positive", "type": "CONDITIONING", "link": 4},
                        {"name": "negative", "type": "CONDITIONING", "link": 6}, {"name": "latent_image", "type": "LATENT", "link": 2}],
             "outputs": [{"name": "LATENT", "type": "LATENT", "links": [7]}],
             "widgets_values": [123456789012, "randomize", 25, 6.5, "dpmpp_2m", "karras", 1]},
            {"id": 8, "type": "VAEDecode", "mode": 0,
             "inputs": [{"name": "samples", "type": "LATENT", "link": 7}, {"name": "vae", "type": "VAE", "link": 8}],
             "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [9]}]},
            {"id": 9, "type": "SaveImage", "mode": 0, "inputs": [{"name": "images", "type": "IMAGE", "link": 9}],
             "outputs": [], "widgets_values": ["mobile"]},
        ],
        "links": [
            [1, 4, 0, 3, 0, "MODEL"], [2, 5, 0, 3, 3, "LATENT"], [3, 4, 1, 6, 0, "CLIP"], [4, 6, 0, 3, 1, "CONDITIONING"],
            [5, 4, 1, 7, 0, "CLIP"], [6, 7, 0, 3, 2, "CONDITIONING"], [7, 3, 0, 8, 0, "LATENT"], [8, 4, 2, 8, 1, "VAE"],
            [9, 8, 0, 9, 0, "IMAGE"],
        ],
        "version": 0.4,
    }


def test_txt2img_matches_save_api_shape():
    api = WorkflowConverter.convert_to_api(_txt2img())
    assert set(api) == {"3", "4", "5", "6", "7", "8", "9"}
    ks = api["3"]
    assert ks["class_type"] == "KSampler"
    assert ks["inputs"]["seed"] == 123456789012          # control_after_generate value skipped
    assert ks["inputs"]["steps"] == 25
    assert ks["inputs"]["cfg"] == 6.5
    assert ks["inputs"]["sampler_name"] == "dpmpp_2m"
    assert ks["inputs"]["scheduler"] == "karras"
    assert ks["inputs"]["denoise"] == 1
    assert ks["inputs"]["model"] == ["4", 0]
    assert ks["inputs"]["positive"] == ["6", 0]
    assert ks["inputs"]["negative"] == ["7", 0]
    assert ks["inputs"]["latent_image"] == ["5", 0]
    assert api["5"]["inputs"] == {"width": 768, "height": 512, "batch_size": 1}
    assert api["6"]["inputs"]["text"] == "a cat"
    assert api["4"]["inputs"]["ckpt_name"] == "b.safetensors"
    assert api["9"]["inputs"] == {"images": ["8", 0], "filename_prefix": "mobile"}


def test_api_format_passthrough():
    api = {"1": {"class_type": "SaveImage", "inputs": {}}}
    assert WorkflowConverter.is_api_format(api)
    assert WorkflowConverter.convert_to_api(api) is api


def test_muted_and_bypassed_nodes():
    wf = _txt2img()
    # Mute the negative prompt encoder: it must vanish from the prompt.
    next(n for n in wf["nodes"] if n["id"] == 7)["mode"] = 2
    api = WorkflowConverter.convert_to_api(wf)
    assert "7" not in api


def test_reroute_is_traced_through():
    wf = _txt2img()
    wf["nodes"].append({"id": 20, "type": "Reroute", "mode": 0,
                        "inputs": [{"name": "", "type": "*", "link": 30}],
                        "outputs": [{"name": "", "type": "VAE", "links": [31]}]})
    # VAE now goes 4 -> Reroute 20 -> VAEDecode 8
    wf["links"] = [l for l in wf["links"] if l[0] != 8] + [[30, 4, 2, 20, 0, "VAE"], [31, 20, 0, 8, 1, "VAE"]]
    vae_decode = next(n for n in wf["nodes"] if n["id"] == 8)
    vae_decode["inputs"][1]["link"] = 31
    api = WorkflowConverter.convert_to_api(wf)
    assert "20" not in api
    assert api["8"]["inputs"]["vae"] == ["4", 2]


def test_subgraph_is_expanded_with_namespaced_ids():
    wf = _txt2img()
    sg_id = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
    # Replace the positive CLIPTextEncode (6) with a subgraph instance wrapping one.
    wf["nodes"] = [n for n in wf["nodes"] if n["id"] != 6]
    wf["nodes"].append({
        "id": 6, "type": sg_id, "mode": 0,
        "inputs": [{"name": "clip", "type": "CLIP", "link": 3}],
        "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [4]}],
        "widgets_values": [],
    })
    wf["definitions"] = {"subgraphs": [{
        "id": sg_id, "name": "Prompt",
        "inputs": [{"name": "clip", "type": "CLIP", "linkIds": [101]}],
        "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "linkIds": [102]}],
        "nodes": [{"id": 1, "type": "CLIPTextEncode", "mode": 0,
                   "inputs": [{"name": "clip", "type": "CLIP", "link": 101}],
                   "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [102]}],
                   "widgets_values": ["a dog in a subgraph"]}],
        "links": [
            {"id": 101, "origin_id": -10, "origin_slot": 0, "target_id": 1, "target_slot": 0, "type": "CLIP"},
            {"id": 102, "origin_id": 1, "origin_slot": 0, "target_id": -20, "target_slot": 0, "type": "CONDITIONING"},
        ],
    }]}
    api = WorkflowConverter.convert_to_api(wf)
    assert "6" not in api
    assert api["6:1"]["class_type"] == "CLIPTextEncode"
    assert api["6:1"]["inputs"]["text"] == "a dog in a subgraph"
    assert api["6:1"]["inputs"]["clip"] == ["4", 1]
    assert api["3"]["inputs"]["positive"] == ["6:1", 0]


def test_unconnected_non_output_nodes_are_dropped():
    wf = _txt2img()
    wf["nodes"].append({"id": 30, "type": "EmptyLatentImage", "mode": 0, "inputs": [],
                        "outputs": [{"name": "LATENT", "type": "LATENT", "links": []}], "widgets_values": [64, 64, 1]})
    assert "30" not in WorkflowConverter.convert_to_api(wf)


def test_works_without_comfyui_nodes_module():
    C.set_nodes_module(None)
    # No node info at all: must not raise.
    api = WorkflowConverter.convert_to_api(_txt2img())
    assert "3" in api
