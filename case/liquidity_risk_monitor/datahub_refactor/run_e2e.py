#!/usr/bin/env python3
"""改造前后 E2E 对比跑批器（不推送飞书）

用法: python3 run_e2e.py <目标脚本路径> [脚本参数...]
"""
import os
import runpy
import sys

WS = '/Coze/Drive/蓁宝/所有对话/主对话'
sys.path.insert(0, WS)

# 屏蔽飞书推送，避免测试期打扰
try:
    import feishu_push

    def _noop(*a, **k):
        print('[E2E] 飞书推送已抑制')
        return True

    feishu_push.push_card_to_feishu = _noop
    feishu_push.push_to_feishu = _noop
except Exception as e:  # pragma: no cover
    print(f'[E2E] feishu_push 加载失败（忽略）: {e}')

target = sys.argv[1]
sys.argv = [target] + sys.argv[2:]
print(f'[E2E] 运行: {target} {sys.argv[1:]}')
runpy.run_path(target, run_name='__main__')
