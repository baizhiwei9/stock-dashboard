# -*- coding: utf-8 -*-
"""
feishu_push 卡片功能验证脚本
- 只打印JSON，不实际发送，避免骚扰主群
"""
import sys
import os
import json

# 让 import 能找到同目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feishu_push import markdown_to_card, _extract_title_from_content, push_to_feishu

PASS = "✅"
FAIL = "❌"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  {status} {name}" + (f"  ({detail})" if detail else ""))
    results.append(condition)

print("=" * 60)
print("测试1: markdown_to_card 基础功能")
print("=" * 60)

short_md = """## 市场日报 2026-01-15

| 指数 | 涨跌幅 |
|------|--------|
| 上证 | +1.2% |
| 深证 | +0.8% |

**总结**：今日大盘放量上攻，北向资金净流入50亿。"""

card = markdown_to_card("测试标题", short_md)

check("返回dict类型", isinstance(card, dict))
check("有header", "header" in card)
check("header.template=blue", card["header"].get("template") == "blue")
check("header.title正确", card["header"]["title"]["content"] == "测试标题")
check("有elements", "elements" in card)
check("elements[0]是markdown", card["elements"][0]["tag"] == "markdown")
check("markdown内容完整", "市场日报" in card["elements"][0]["content"])
check("短内容无note", len(card["elements"]) == 1)

print()
print("=" * 60)
print("测试2: 长内容自动截断")
print("=" * 60)

long_md = "## 长报告\n\n" + ("这是一行测试内容，用来填充长度。\n" * 300)
print(f"  原始长度: {len(long_md)} 字符")

card_long = markdown_to_card("长报告测试", long_md)

check("返回dict类型", isinstance(card_long, dict))
check("有header", "header" in card_long)
md_content = card_long["elements"][0]["content"]
check("markdown内容已截断", len(md_content) <= 2800, f"截断后长度: {len(md_content)}")
check("有note提示", len(card_long["elements"]) == 2)
check("note内容正确", "截断" in card_long["elements"][1]["elements"][0]["content"])

print()
print("=" * 60)
print("测试3: _extract_title_from_content")
print("=" * 60)

check("# 标题提取", _extract_title_from_content("# 今日市场") == "今日市场")
check("## 二级标题", _extract_title_from_content("## 风险日报 2026-01-15") == "风险日报 2026-01-15")
check("### 三级标题", _extract_title_from_content("### 详细分析") == "详细分析")
check("无标题→默认", _extract_title_from_content("今日大盘走势分析") == "📊 消息通知")
check("空内容→默认", _extract_title_from_content("") == "📊 消息通知")
check("None→默认", _extract_title_from_content(None) == "📊 消息通知")

print()
print("=" * 60)
print("测试4: push_to_feishu as_card 参数逻辑（mock验证）")
print("=" * 60)

# 测试 as_card=True 时生成的卡片JSON是合法的
card_for_push = markdown_to_card("市场日报", short_md)
card_json_str = json.dumps(card_for_push, ensure_ascii=False)
check("卡片JSON可序列化", len(card_json_str) > 0)
check("JSON长度合理", len(card_json_str) < 10000, f"{len(card_json_str)} bytes")

# 测试 as_card=False 回退逻辑（不调用实际发送，只验证函数签名兼容）
import inspect
sig = inspect.signature(push_to_feishu)
params = sig.parameters
check("as_card参数存在", "as_card" in params)
check("as_card默认True", params["as_card"].default is True)
check("as_file参数仍在", "as_file" in params)
check("file_path参数仍在", "file_path" in params)
check("text参数仍在", "text" in params)
check("chat_id参数仍在", "chat_id" in params)

print()
print("=" * 60)
print("测试5: 卡片JSON样例预览")
print("=" * 60)
print(json.dumps(card, ensure_ascii=False, indent=2)[:2000])

print()
print("=" * 60)
total = len(results)
passed = sum(results)
print(f"结果: {passed}/{total} 通过")
if passed == total:
    print("🎉 全部测试通过！")
    sys.exit(0)
else:
    print("⚠️ 有测试失败，请检查")
    sys.exit(1)
