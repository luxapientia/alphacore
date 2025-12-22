#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from dataclasses import dataclass


def _guest_log(message: str) -> None:
    print(f"[Guest] {message}", flush=True)


@dataclass(frozen=True)
class CheckConfig:
    timeout_s: int
    proxy_url: str
    dns_ip: str


def _resolve_ipv4(hostname: str) -> str:
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
    except Exception:
        return ""
    for info in infos:
        ip = info[4][0]
        if ip:
            return ip
    return ""


def _curl_code(url: str, *, timeout_s: int, use_proxy: bool, proxy_url: str) -> str:
    cmd = [
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--max-time",
        str(max(1, int(timeout_s))),
        url,
    ]
    env = os.environ.copy()
    if use_proxy:
        env["http_proxy"] = proxy_url
        env["https_proxy"] = proxy_url
    else:
        env.pop("http_proxy", None)
        env.pop("https_proxy", None)
    try:
        proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    except FileNotFoundError:
        return "000"
    code = (proc.stdout or "").strip()
    if len(code) >= 3:
        code = code[-3:]
    if not (len(code) == 3 and code.isdigit()):
        return "000"
    return code


def run_checks(cfg: CheckConfig) -> int:
    # DNS allowlist/sinkhole behavior (dnsmasq should be on 172.16.0.1).
    google_ip = _resolve_ipv4("googleapis.com")
    _guest_log(f"DNS: googleapis.com -> {google_ip or 'empty'}")
    if not google_ip or google_ip == "0.0.0.0":
        _guest_log(f"ERROR: DNS allowlist failed: googleapis.com resolved to '{google_ip or 'empty'}'.")
        return 1

    compute_ip = _resolve_ipv4("compute.googleapis.com")
    _guest_log(f"DNS: compute.googleapis.com -> {compute_ip or 'empty'}")
    if not compute_ip or compute_ip == "0.0.0.0":
        _guest_log(f"ERROR: DNS allowlist failed: compute.googleapis.com resolved to '{compute_ip or 'empty'}'.")
        return 1

    blocked_ip = _resolve_ipv4("example.com")
    _guest_log(f"DNS: example.com -> {blocked_ip or 'empty'}")
    if blocked_ip and blocked_ip != "0.0.0.0":
        _guest_log(f"ERROR: DNS sinkhole failed: example.com resolved to '{blocked_ip}' (expected 0.0.0.0/empty).")
        return 1

    # Proxy liveness: any non-000 indicates TCP connect + HTTP response.
    proxy_code = _curl_code("http://172.16.0.1:8888/", timeout_s=cfg.timeout_s, use_proxy=True, proxy_url=cfg.proxy_url)
    _guest_log(f"Proxy: 172.16.0.1:8888 -> HTTP {proxy_code}")
    if proxy_code == "000":
        _guest_log("ERROR: Proxy not reachable at 172.16.0.1:8888.")
        return 1

    # Host should not expose arbitrary services to the guest (only DNS+proxy).
    host_http_code = _curl_code("http://172.16.0.1/", timeout_s=cfg.timeout_s, use_proxy=False, proxy_url=cfg.proxy_url)
    _guest_log(f"Host HTTP (expected blocked): 172.16.0.1:80 -> HTTP {host_http_code}")
    if host_http_code != "000":
        _guest_log(f"ERROR: Host HTTP unexpectedly reachable at 172.16.0.1:80 (HTTP {host_http_code}).")
        return 1

    # Direct egress must fail (no proxy).
    direct_code = _curl_code(
        "https://www.googleapis.com/discovery/v1/apis",
        timeout_s=cfg.timeout_s,
        use_proxy=False,
        proxy_url=cfg.proxy_url,
    )
    _guest_log(f"Direct egress (expected blocked): googleapis -> HTTP {direct_code}")
    if direct_code == "200":
        _guest_log("ERROR: Direct egress unexpectedly succeeded without proxy (HTTP 200).")
        return 1

    # Allowed via proxy must succeed.
    allowed_code = _curl_code(
        "https://www.googleapis.com/discovery/v1/apis",
        timeout_s=cfg.timeout_s,
        use_proxy=True,
        proxy_url=cfg.proxy_url,
    )
    _guest_log(f"Proxy allowlist: googleapis -> HTTP {allowed_code}")
    if allowed_code != "200":
        _guest_log(f"ERROR: Allowed googleapis traffic via proxy failed (HTTP {allowed_code}).")
        return 1

    compute_code = _curl_code("https://compute.googleapis.com/", timeout_s=cfg.timeout_s, use_proxy=True, proxy_url=cfg.proxy_url)
    _guest_log(f"Proxy allowlist: compute.googleapis.com -> HTTP {compute_code} (expected not 000)")
    if compute_code == "000":
        _guest_log("ERROR: compute.googleapis.com not reachable via proxy (HTTP 000).")
        return 1

    # Blocked domain via proxy must fail.
    blocked_code = _curl_code("http://example.com", timeout_s=cfg.timeout_s, use_proxy=True, proxy_url=cfg.proxy_url)
    _guest_log(f"Proxy denylist: example.com -> HTTP {blocked_code} (expected not 200)")
    if blocked_code == "200":
        _guest_log("ERROR: Blocked domain unexpectedly reachable via proxy (HTTP 200).")
        return 1
    blocked_https_code = _curl_code("https://example.com", timeout_s=cfg.timeout_s, use_proxy=True, proxy_url=cfg.proxy_url)
    _guest_log(f"Proxy denylist: https example.com -> HTTP {blocked_https_code} (expected not 200)")
    if blocked_https_code == "200":
        _guest_log("ERROR: Blocked HTTPS domain unexpectedly reachable via proxy (HTTP 200).")
        return 1

    # Metadata must not be retrievable, with or without proxy.
    # Without proxy we expect a hard connect failure (iptables REJECT/DROP).
    try:
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", "2", "http://169.254.169.254/latest/meta-data"],
            env={k: v for k, v in os.environ.items() if k not in {"http_proxy", "https_proxy"}},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0:
            _guest_log("ERROR: Metadata endpoint returned an HTTP response without proxy; sandbox egress policy is broken.")
            return 1
    except FileNotFoundError:
        _guest_log("ERROR: curl missing; cannot check metadata egress.")
        return 1

    meta_proxy_code = _curl_code(
        "http://169.254.169.254/latest/meta-data",
        timeout_s=cfg.timeout_s,
        use_proxy=True,
        proxy_url=cfg.proxy_url,
    )
    _guest_log(f"Proxy denylist: metadata -> HTTP {meta_proxy_code} (expected not 200)")
    if meta_proxy_code == "200":
        _guest_log("ERROR: Metadata endpoint returned HTTP 200 via proxy; proxy egress guard is broken.")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AlphaCore guest network-policy self-checks.")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("ACORE_NET_CHECK_TIMEOUT", "5")))
    parser.add_argument("--proxy-url", default=os.environ.get("PROXY_URL", os.environ.get("http_proxy", "http://172.16.0.1:8888")))
    parser.add_argument("--dns-ip", default=os.environ.get("ACORE_STATIC_DNS", "172.16.0.1"))
    args = parser.parse_args()

    cfg = CheckConfig(timeout_s=max(1, int(args.timeout)), proxy_url=str(args.proxy_url), dns_ip=str(args.dns_ip))
    return run_checks(cfg)


if __name__ == "__main__":
    sys.exit(main())

