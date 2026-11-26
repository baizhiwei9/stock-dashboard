#!/usr/bin/env python3
"""
飞书资源 owner 权限转移脚本
支持飞书云文档、电子表格、多维表格（Base）的 owner 权限转移
使用统一的 Drive 权限 API：POST /open-apis/drive/v1/permissions/:token/members/transfer_owner
"""

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, Optional
import requests

# 资源类型映射：用户输入 -> 飞书 API 的 type 参数
RESOURCE_TYPE_MAP = {
    "doc": "docx",
    "docx": "docx",
    "sheet": "sheet",
    "spreadsheet": "sheet",
    "base": "bitable",
    "bitable": "bitable",
    "file": "file",
    "wiki": "wiki",
}


def get_credentials() -> tuple[Optional[str], Optional[str]]:
    """从环境变量获取飞书应用凭证"""
    app_id = os.environ.get("FEISHU_APP_ID_7629232824918245402")
    app_secret = os.environ.get("FEISHU_APP_SECRET_7629232824918245402")

    if not app_id:
        sys.stderr.write("FEISHU_APP_ID_7629232824918245402 environment variable not set\n")
        return None, None

    if not app_secret:
        sys.stderr.write("FEISHU_APP_SECRET_7629232824918245402 environment variable not set\n")
        return None, None

    return app_id, app_secret


def get_tenant_access_token(app_id: str, app_secret: str) -> Optional[str]:
    """获取 tenant_access_token（bot 身份凭证）"""
    try:
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": app_id, "app_secret": app_secret}

        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("code") == 0:
            return data.get("tenant_access_token")
        else:
            sys.stderr.write(f"Failed to get tenant_access_token: {data.get('msg')}\n")
            return None

    except Exception as e:
        sys.stderr.write(f"Error getting tenant_access_token: {e}\n")
        return None


def get_user_open_id() -> Optional[str]:
    """通过 lark-cli 获取当前用户的 open_id"""
    try:
        result = subprocess.run(
            ["lark-cli", "contact", "+get-user", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=20,
        )

        if result.returncode != 0:
            sys.stderr.write(f"Error getting user info: {result.stderr}\n")
            return None

        data = json.loads(result.stdout)
        open_id = data.get("open_id")

        if not open_id:
            sys.stderr.write("No open_id found in user info\n")
            return None

        return open_id

    except subprocess.TimeoutExpired:
        sys.stderr.write("Timeout getting user info\n")
        return None
    except json.JSONDecodeError as e:
        sys.stderr.write(f"JSON decode error: {e}\n")
        return None
    except Exception as e:
        sys.stderr.write(f"Unexpected error: {e}\n")
        return None


def transfer_owner(
    token: str,
    resource_type: str,
    user_open_id: str,
    tenant_access_token: str,
) -> Dict:
    """
    统一的权限转移函数，调用飞书 Drive 权限 API
    API: POST /open-apis/drive/v1/permissions/:token/members/transfer_owner

    Args:
        token: 资源 token（文档 ID / 表格 token / Base token 等）
        resource_type: 飞书 API 的 type 参数（docx / sheet / bitable / file / wiki）
        user_open_id: 新 owner 的 open_id
        tenant_access_token: bot 的 tenant_access_token（当前 owner）
    """
    try:
        # 统一的 Drive 权限转移 API，token 作为路径参数
        url = f"https://open.feishu.cn/open-apis/drive/v1/permissions/{token}/members/transfer_owner"
        headers = {
            "Authorization": f"Bearer {tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        # type: 资源类型，need_notification: 不通知，remove_old_owner: 不移除旧 owner
        params = {
            "type": resource_type,
            "need_notification": "false",
            "remove_old_owner": "false",
        }
        # 请求体：新 owner 信息
        payload = {
            "member_type": "openid",
            "member_id": user_open_id,
        }

        response = requests.post(url, json=payload, headers=headers, params=params, timeout=20)
        data = response.json()

        if data.get("code") == 0:
            return {
                "success": True,
                "resource_type": resource_type,
                "resource_token": token,
                "new_owner": user_open_id,
            }
        else:
            return {
                "success": False,
                "error": data.get("msg", "Unknown error"),
                "code": data.get("code"),
                "resource_type": resource_type,
                "resource_token": token,
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"Unexpected error: {e}",
            "resource_type": resource_type,
            "resource_token": token,
        }


def resolve_resource(args) -> tuple[Optional[str], Optional[str]]:
    """
    从命令行参数中解析资源类型和 token
    支持两种格式：
    1. --resource-type + --resource-token（文档推荐格式）
    2. --doc-id / --sheet-token / --base-token（旧格式，向后兼容）
    """
    if args.resource_type and args.resource_token:
        api_type = RESOURCE_TYPE_MAP.get(args.resource_type)
        if not api_type:
            sys.stderr.write(
                f"Unknown resource type: {args.resource_type}, "
                f"supported: {', '.join(RESOURCE_TYPE_MAP.keys())}\n"
            )
            return None, None
        return api_type, args.resource_token

    # 向后兼容旧的参数格式
    if args.doc_id:
        return "docx", args.doc_id
    if args.sheet_token:
        return "sheet", args.sheet_token
    if args.base_token:
        return "bitable", args.base_token

    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="转移飞书资源的 owner 权限给指定用户（默认当前用户）"
    )

    # 推荐格式：--resource-type + --resource-token
    parser.add_argument("--resource-type", help="资源类型: doc, docx, sheet, spreadsheet, base, bitable, file, wiki")
    parser.add_argument("--resource-token", help="资源 token（文档 ID / 表格 token / Base token 等）")

    # 向后兼容旧格式
    parser.add_argument("--doc-id", help="(旧格式) 飞书云文档 ID")
    parser.add_argument("--sheet-token", help="(旧格式) 飞书电子表格 token")
    parser.add_argument("--base-token", help="(旧格式) 飞书多维表格 token")

    # 可选：指定接收者
    parser.add_argument("--to-open-id", "--target-user-id", help="接收者的 open_id，默认为当前用户")

    args = parser.parse_args()

    # 解析资源类型和 token
    api_type, token = resolve_resource(args)
    if not api_type or not token:
        output = {
            "status": "error",
            "error": "Must specify --resource-type + --resource-token, or one of --doc-id / --sheet-token / --base-token",
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    # 获取 bot 凭证
    app_id, app_secret = get_credentials()
    if not app_id or not app_secret:
        output = {"status": "error", "error": "Failed to get credentials from environment variables"}
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    # 获取 tenant_access_token（bot 身份）
    tenant_access_token = get_tenant_access_token(app_id, app_secret)
    if not tenant_access_token:
        output = {"status": "error", "error": "Failed to get tenant_access_token"}
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    # 获取接收者的 open_id（默认为当前用户）
    to_open_id = args.to_open_id
    if not to_open_id:
        to_open_id = get_user_open_id()
        if not to_open_id:
            output = {"status": "error", "error": "Failed to get user open_id"}
            print(json.dumps(output, ensure_ascii=False))
            sys.exit(1)

    # 调用统一的权限转移 API
    result = transfer_owner(token, api_type, to_open_id, tenant_access_token)

    if result.get("success"):
        output = {
            "status": "success",
            "message": f"Successfully transferred owner for {api_type} ({token})",
            "result": result,
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(0)
    else:
        output = {
            "status": "error",
            "error": result.get("error", "Unknown error"),
            "result": result,
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
