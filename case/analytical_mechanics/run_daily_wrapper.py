#!/usr/bin/env python3
"""包装脚本：禁用IPv6 + 运行日报"""
import socket
socket.setdefaulttimeout(30)
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET]

import os, sys, runpy
script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'daily_report.py')
runpy.run_path(script_path, run_name='__main__')
