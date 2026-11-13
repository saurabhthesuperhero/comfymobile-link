"""
Pairing helpers: LAN address discovery, the --listen check, the persistent pairing
token + push targets, and the QR payload. Pure Python (stdlib only) so it can be
tested without ComfyUI.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

PAYLOAD_VERSION = 1
MAX_PUSH_TARGETS = 10


# ─────────────────────────── listen check ───────────────────────────

@dataclass
class ListenStatus:
    """How ComfyUI was started, and whether a phone on the LAN can reach it."""
    addresses: List[str]
    all_interfaces: bool          # 0.0.0.0 / :: (what `--listen` with no value gives)
    loopback_only: bool           # only 127.0.0.1 / localhost / ::1 (ComfyUI's default)
    specific_lan: List[str] = field(default_factory=list)  # e.g. --listen 192.168.1.5

    @property
    def lan_reachable(self) -> bool:
        return self.all_interfaces or bool(self.specific_lan)


def _is_loopback(host: str) -> bool:
    h = host.strip().strip("[]").lower()
    if h in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def parse_listen(listen: Optional[str]) -> ListenStatus:
    """Parse ComfyUI's `--listen` value ("127.0.0.1" by default, "0.0.0.0,::" for a bare --listen)."""
    raw = (listen or "127.0.0.1").strip()
    addrs = [a.strip() for a in raw.split(",") if a.strip()] or ["127.0.0.1"]
    all_if = any(a.strip("[]") in ("0.0.0.0", "::") for a in addrs)
    specific = [a for a in addrs if a.strip("[]") not in ("0.0.0.0", "::") and not _is_loopback(a)]
    loop_only = all(_is_loopback(a) for a in addrs)
    return ListenStatus(addresses=addrs, all_interfaces=all_if, loopback_only=loop_only, specific_lan=specific)


def listen_fix_text(status: ListenStatus, port: int) -> Optional[str]:
    """The exact fix shown in the panel when a phone cannot reach this ComfyUI, else None."""
    if status.lan_reachable:
        return None
    return (
        "ComfyUI is only listening on this computer (127.0.0.1), so your phone can't reach it.\n"
        "Fix: restart ComfyUI with --listen\n"
        f"  • Manual / portable: python main.py --listen 0.0.0.0 --port {port}\n"
        "    (Windows portable: add --listen to the line in run_nvidia_gpu.bat)\n"
        "  • ComfyUI Desktop: Settings → Server-Config → Host = 0.0.0.0, then restart\n"
        "Then allow Python / ComfyUI through your firewall on private networks."
    )


# ─────────────────────────── LAN addresses ───────────────────────────

def _rank(ip: str) -> int:
    """Order: home LAN (192.168/10/172.16) first, then Tailscale/CGNAT, then anything else."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return 9
    if a.version != 4:
        return 8
    if a in ipaddress.ip_network("192.168.0.0/16"):
        return 0
    if a in ipaddress.ip_network("10.0.0.0/8"):
        return 1
    if a in ipaddress.ip_network("172.16.0.0/12"):
        return 2
    if a in ipaddress.ip_network("100.64.0.0/10"):
        return 3  # Tailscale / CGNAT
    return 5


def usable_ipv4(candidates: Iterable[str]) -> List[str]:
    """Dedupe, drop loopback / link-local / unspecified, sort by [_rank] (stable)."""
    out: List[str] = []
    for c in candidates:
        try:
            a = ipaddress.ip_address(c)
        except ValueError:
            continue
        if a.version != 4 or a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast:
            continue
        s = str(a)
        if s not in out:
            out.append(s)
    return sorted(out, key=_rank)


def discover_lan_ips() -> List[str]:
    """Best-effort local IPv4 addresses. Sends no packets (UDP connect only picks a route)."""
    found: List[str] = []
    for probe in ("192.168.0.1", "10.255.255.255", "8.8.8.8"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0)
            s.connect((probe, 1))
            found.append(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    return usable_ipv4(found)


def build_urls(status: ListenStatus, lan_ips: Sequence[str], port: int, https: bool = False) -> List[str]:
    """URLs the phone should try, in order."""
    scheme = "https" if https else "http"
    if status.specific_lan and not status.all_interfaces:
        hosts = usable_ipv4(status.specific_lan) or [h.strip("[]") for h in status.specific_lan]
    else:
        hosts = list(lan_ips)
    return [f"{scheme}://{h}:{port}" for h in hosts][:6]


# ─────────────────────────── QR payload ───────────────────────────

def build_payload(name: str, urls: Sequence[str], token: str) -> dict:
    return {"v": PAYLOAD_VERSION, "name": (name or "ComfyUI")[:40], "urls": list(urls), "token": token}


def payload_text(payload: dict) -> str:
    """Compact JSON (smaller QR)."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(stored: str, presented: Optional[str]) -> Optional[bool]:
    """None when nothing was presented; accepts the raw token or its sha256 hex."""
    if not presented:
        return None
    presented = presented.strip()
    return hmac.compare_digest(stored, presented) or hmac.compare_digest(token_hash(stored), presented.lower())


def qr_svg(text: str, border: int = 3) -> str:
    """Render [text] as a standalone SVG QR code (Nayuki's MIT encoder, ECC medium)."""
    from .qrcodegen import QrCode

    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    parts = []
    for y in range(size):
        for x in range(size):
            if qr.get_module(x, y):
                parts.append(f"M{x + border},{y + border}h1v1h-1z")
    dim = size + border * 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" viewBox="0 0 {dim} {dim}" '
        f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#FFFFFF"/>'
        f'<path d="{" ".join(parts)}" fill="#000000"/></svg>'
    )


# ─────────────────────────── persistent state ───────────────────────────

def _valid_target(t: object) -> bool:
    return isinstance(t, str) and 16 <= len(t) <= 128 and all(c.isalnum() or c in "-_" for c in t)


class PairingState:
    """Token + registered push targets, stored as JSON next to ComfyUI's user data."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.token = ""
        self.targets: List[str] = []
        self._load()

    def _load(self) -> None:
        data = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            data = {}
        tok = data.get("token")
        self.token = tok if isinstance(tok, str) and len(tok) >= 16 else secrets.token_urlsafe(24)
        self.targets = [t for t in data.get("push_targets", []) if _valid_target(t)][:MAX_PUSH_TARGETS]
        self._save()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"token": self.token, "push_targets": self.targets}, f)
            os.replace(tmp, self.path)
        except OSError:
            pass  # read-only install: keep working from memory

    def reset(self) -> str:
        with self._lock:
            self.token = secrets.token_urlsafe(24)
            self.targets = []
            self._save()
            return self.token

    def add_target(self, target: str) -> bool:
        if not _valid_target(target):
            return False
        with self._lock:
            if target in self.targets:
                self.targets.remove(target)
            self.targets.insert(0, target)  # newest first; the oldest falls off
            del self.targets[MAX_PUSH_TARGETS:]
            self._save()
        return True

    def remove_target(self, target: str) -> bool:
        with self._lock:
            if target not in self.targets:
                return False
            self.targets.remove(target)
            self._save()
            return True

    def snapshot_targets(self) -> List[str]:
        with self._lock:
            return list(self.targets)
