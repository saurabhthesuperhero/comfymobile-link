import json

from comfymobile_link import pairing as P
from comfymobile_link.qrcodegen import QrCode


def test_parse_listen_default_is_loopback_only():
    s = P.parse_listen(None)
    assert s.loopback_only and not s.lan_reachable
    assert P.listen_fix_text(s, 8188) and "--listen" in P.listen_fix_text(s, 8188)


def test_parse_listen_bare_flag_and_specific_ip():
    s = P.parse_listen("0.0.0.0,::")
    assert s.all_interfaces and s.lan_reachable and P.listen_fix_text(s, 8188) is None
    s2 = P.parse_listen("192.168.1.20")
    assert not s2.all_interfaces and s2.lan_reachable and s2.specific_lan == ["192.168.1.20"]
    assert P.parse_listen("localhost,127.0.0.1").loopback_only


def test_usable_ipv4_filters_and_orders():
    ips = P.usable_ipv4(["127.0.0.1", "169.254.3.3", "100.101.102.103", "8.8.4.4", "10.0.0.5",
                         "192.168.1.9", "192.168.1.9", "0.0.0.0", "fe80::1", "garbage"])
    assert ips == ["192.168.1.9", "10.0.0.5", "100.101.102.103", "8.8.4.4"]


def test_build_urls():
    lan = ["192.168.1.9", "100.64.1.2"]
    assert P.build_urls(P.parse_listen("0.0.0.0"), lan, 8188) == ["http://192.168.1.9:8188", "http://100.64.1.2:8188"]
    assert P.build_urls(P.parse_listen("192.168.1.20"), lan, 8000) == ["http://192.168.1.20:8000"]
    assert P.build_urls(P.parse_listen("0.0.0.0"), lan, 8443, https=True)[0] == "https://192.168.1.9:8443"


def test_payload_shape_matches_the_app_contract():
    payload = P.build_payload("studio-pc", ["http://192.168.1.9:8188"], "tok" * 8)
    text = P.payload_text(payload)
    assert json.loads(text) == {"v": 1, "name": "studio-pc", "urls": ["http://192.168.1.9:8188"], "token": "tok" * 8}
    assert " " not in text  # compact → smaller QR


def test_qr_svg_encodes_the_payload():
    text = P.payload_text(P.build_payload("pc", ["http://192.168.1.9:8188", "http://100.64.0.7:8188"], "x" * 32))
    svg = P.qr_svg(text)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    # One 1×1 square per dark module.
    dark = sum(qr.get_module(x, y) for y in range(qr.get_size()) for x in range(qr.get_size()))
    assert svg.count("h1v1h-1z") == dark


def test_token_matches_raw_or_hash():
    assert P.token_matches("secret-token-123456", None) is None
    assert P.token_matches("secret-token-123456", "secret-token-123456") is True
    assert P.token_matches("secret-token-123456", P.token_hash("secret-token-123456")) is True
    assert P.token_matches("secret-token-123456", "nope") is False


def test_state_persists_token_and_targets(tmp_path):
    path = str(tmp_path / "state.json")
    s = P.PairingState(path)
    assert len(s.token) >= 16
    t = "T" * 43
    assert s.add_target(t)
    assert not s.add_target("bad target!")
    assert not s.add_target(None)
    again = P.PairingState(path)
    assert again.token == s.token and again.targets == [t]
    old = again.token
    again.reset()
    assert again.token != old and again.targets == []


def test_state_caps_targets_newest_first(tmp_path):
    s = P.PairingState(str(tmp_path / "s.json"))
    for i in range(P.MAX_PUSH_TARGETS + 3):
        s.add_target(f"target-{i:02d}-xxxxxxxxxxxx")
    assert len(s.targets) == P.MAX_PUSH_TARGETS
    assert s.targets[0] == f"target-{P.MAX_PUSH_TARGETS + 2:02d}-xxxxxxxxxxxx"
    assert s.remove_target(s.targets[0]) and not s.remove_target("missing-target-xxxxxx")


def test_corrupt_state_file_is_replaced(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    s = P.PairingState(str(p))
    assert len(s.token) >= 16 and s.targets == []
