#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共享LLM分析模块 - 调用DeepSeek V4 Flash进行研判分析
所有技能脚本可直接import使用，成本极低($0.14/1M input tokens)
"""

import os
import json
try:
    from coze_workload_identity import requests
except ImportError:
    import requests

# DeepSeek API配置
DEEPSEEK_API_KEY = "***REMOVED-DEEPSEEK-KEY***"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-v4-flash"

def llm_analyze(system_prompt: str, user_content: str, max_tokens: int = 2000) -> str:
    """
    调用DeepSeek V4 Flash进行研判分析
    
    Args:
        system_prompt: 系统提示词（角色设定+分析框架）
        user_content: 用户内容（数据快照JSON/文本）
        max_tokens: 最大输出token数
    
    Returns:
        LLM分析结果文本
    """
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,  # 低温度保证稳定性
        "stream": False
    }
    
    try:
        response = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = result.get("usage", {})
            print(f"  [LLM] 分析完成 | input={usage.get('prompt_tokens', '?')} tok | output={usage.get('completion_tokens', '?')} tok | 费用≈${usage.get('prompt_tokens', 0) * 0.14 / 1000000:.4f}")
            return content
        else:
            print(f"  [LLM] API错误: {response.status_code} - {response.text[:200]}")
            return ""
            
    except requests.exceptions.Timeout:
        print("  [LLM] 请求超时(30s)")
        return ""
    except Exception as e:
        print(f"  [LLM] 调用失败: {str(e)[:100]}")
        return ""


def llm_analyze_json(system_prompt: str, data_dict: dict, max_tokens: int = 2000) -> str:
    """
    便捷方法：直接传dict，自动转JSON给LLM分析
    
    Args:
        system_prompt: 系统提示词
        data_dict: 数据字典（自动转为JSON字符串）
        max_tokens: 最大输出token数
    
    Returns:
        LLM分析结果文本
    """
    user_content = f"以下是自动采集的数据快照，请基于这些数据进行分析：\n\n```json\n{json.dumps(data_dict, ensure_ascii=False, indent=2)}\n```"
    return llm_analyze(system_prompt, user_content, max_tokens)


if __name__ == "__main__":
    # 快速测试
    print("测试DeepSeek V4 Flash连接...")
    result = llm_analyze(
        "你是一个测试助手。",
        "请回复'连接成功'四个字。",
        max_tokens=20
    )
    print(f"结果: {result}")
