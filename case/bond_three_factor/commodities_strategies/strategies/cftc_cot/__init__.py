# -*- coding: utf-8 -*-
"""CFTC COT 持仓极值策略：管理基金净持仓 104 周分位 + 持仓时钟。

导入本包即强制 IPv4：容器内 IPv6 不通时 requests 会静默挂死。
"""
from __future__ import annotations

import socket

_orig_gai = socket.getaddrinfo
if not getattr(_orig_gai, "_cftc_ipv4_only", False):
    def _ipv4_only(*args, **kwargs):
        return [r for r in _orig_gai(*args, **kwargs) if r[0] == socket.AF_INET]

    _ipv4_only._cftc_ipv4_only = True  # type: ignore[attr-defined]
    socket.getaddrinfo = _ipv4_only  # type: ignore[assignment]

__all__ = ["universe", "cot", "prices", "signals", "engine", "report"]
