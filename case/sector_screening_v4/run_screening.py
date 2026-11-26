#!/usr/bin/env python3
import subprocess
import sys

result = subprocess.run(
    ['python3', (__import__('os').environ.get('CASE_WORKSPACE') or '/Coze/Drive/蓁宝/所有对话/主对话') + '/板块筛选v3.py'],
    capture_output=True,
    text=True,
    timeout=1200
)

print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("Return code:", result.returncode)
