from comfymobile_link.push import PushRelay, classify_event, wrap_send_sync


def test_classify_only_app_prompts_and_terminal_events():
    assert classify_event("execution_success", {"prompt_id": "p1"}, "COMFY_MOBILE") == ("p1", "success")
    assert classify_event("execution_error", {"prompt_id": "p1"}, "COMFY_MOBILE") == ("p1", "error")
    assert classify_event("execution_interrupted", {"prompt_id": "p1"}, "COMFY_MOBILE") == ("p1", "interrupted")
    assert classify_event("execution_success", {"prompt_id": "p1"}, "browser-sid") is None
    assert classify_event("execution_success", {"prompt_id": "p1"}, None) is None
    assert classify_event("execution_success", {"prompt_id": "p1"}, None, include_all=True) == ("p1", "success")
    assert classify_event("progress", {"prompt_id": "p1"}, "COMFY_MOBILE") is None
    assert classify_event("execution_success", {}, "COMFY_MOBILE") is None
    assert classify_event("execution_success", "junk", "COMFY_MOBILE") is None


class FakeServer:
    def __init__(self):
        self.sent = []

    def send_sync(self, event, data, sid=None):
        self.sent.append((event, data, sid))


def _relay(targets, codes=None, gone=None):
    posted = []

    def post(url, body):
        posted.append((url, body))
        return (codes or {}).get(body["target"], 202)

    relay = PushRelay(targets=lambda: list(targets), on_target_gone=(gone.append if gone is not None else lambda t: None),
                      post=post, url="https://relay.test/v1/push/notify")
    relay._ensure_thread = lambda: None  # deliver synchronously in tests
    return relay, posted


def test_wrapper_passes_through_and_queues_push():
    server = FakeServer()
    relay, posted = _relay(["target-aaaaaaaaaaaaaaaa"])
    assert wrap_send_sync(server, relay, include_all=lambda: False)
    assert not wrap_send_sync(server, relay)  # idempotent
    server.send_sync("execution_success", {"prompt_id": "abc"}, "COMFY_MOBILE")
    server.send_sync("executing", {"node": None, "prompt_id": "abc"}, "COMFY_MOBILE")
    assert [e for e, _, _ in server.sent] == ["execution_success", "executing"]
    relay.drain_for_tests()
    assert posted == [("https://relay.test/v1/push/notify",
                       {"target": "target-aaaaaaaaaaaaaaaa", "promptId": "abc", "status": "success"})]


def test_push_errors_never_reach_comfyui():
    server = FakeServer()

    def boom(url, body):
        raise OSError("network down")

    relay = PushRelay(targets=lambda: ["target-aaaaaaaaaaaaaaaa"], on_target_gone=lambda t: None, post=boom)
    relay._ensure_thread = lambda: None
    wrap_send_sync(server, relay, include_all=lambda: False)
    server.send_sync("execution_error", {"prompt_id": "x"}, "COMFY_MOBILE")
    relay.drain_for_tests()  # must not raise
    assert server.sent


def test_dedupes_prompt_and_skips_without_targets():
    relay, posted = _relay([])
    assert relay.notify("p", "success") is False
    relay2, posted2 = _relay(["target-aaaaaaaaaaaaaaaa", "target-bbbbbbbbbbbbbbbb"])
    assert relay2.notify("p", "success") is True
    assert relay2.notify("p", "success") is False
    relay2.drain_for_tests()
    assert len(posted2) == 2


def test_gone_targets_are_dropped():
    gone = []
    relay, posted = _relay(["target-aaaaaaaaaaaaaaaa", "target-bbbbbbbbbbbbbbbb"],
                           codes={"target-aaaaaaaaaaaaaaaa": 410, "target-bbbbbbbbbbbbbbbb": 429}, gone=gone)
    relay.notify("p", "success")
    relay.drain_for_tests()
    assert gone == ["target-aaaaaaaaaaaaaaaa"]
