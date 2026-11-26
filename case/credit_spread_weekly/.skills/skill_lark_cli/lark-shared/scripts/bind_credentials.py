#!/usr/bin/env python3
"""
飞书 CLI 配置绑定脚本
通过环境变量中的 FEISHU_APP_ID 和 FEISHU_APP_SECRET 进行配置绑定
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple


def _load_json(path: Path) -> Optional[Dict]:
    """读取 JSON 配置，失败时返回 None，避免坏配置阻塞重新绑定。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _config_app_ids(config: Dict) -> Tuple[str, ...]:
    """提取 config.json 中已有的 appId 列表。"""
    app_ids = []
    if isinstance(config.get("appId"), str) and config["appId"]:
        app_ids.append(config["appId"])
    apps = config.get("apps")
    if isinstance(apps, list):
        for app in apps:
            if isinstance(app, dict) and isinstance(app.get("appId"), str) and app["appId"]:
                app_ids.append(app["appId"])
    return tuple(dict.fromkeys(app_ids))


def _has_non_empty_file(path: Path) -> bool:
    """判断关键文件是否存在且非空。"""
    return path.is_file() and path.stat().st_size > 0


def _find_existing_config(cwd: Path, app_id: str) -> Optional[Path]:
    """
    查找 cwd 下已有且可用的飞书 CLI 配置。
    只认当前工作目录下的 .feishu_cli/.feishu-cli，避免跨会话误用其它目录。
    """
    for dirname in [".feishu_cli", ".feishu-cli"]:
        candidate = cwd / dirname
        config_path = candidate / "config.json"
        keychain_dir = candidate / "lark-cli"
        config = _load_json(config_path)
        if not config or app_id not in _config_app_ids(config):
            continue

        # app 配置至少需要 config.json、master.key 和 appsecret；user token 是登录授权态，不是 app 初始化的必要条件。
        if not _has_non_empty_file(keychain_dir / "master.key"):
            continue
        if not any(p.is_file() and p.stat().st_size > 0 for p in keychain_dir.glob(f"appsecret_{app_id}.enc")):
            continue
        return candidate
    return None


def _find_existing_config_with_other_app(cwd: Path, app_id: str) -> Optional[Dict]:
    """发现 cwd 下已有其它 appId 的可用配置时返回详情，避免静默覆盖旧授权。"""
    for dirname in [".feishu_cli", ".feishu-cli"]:
        candidate = cwd / dirname
        config = _load_json(candidate / "config.json")
        if not config:
            continue
        existing_app_ids = _config_app_ids(config)
        if not existing_app_ids or app_id in existing_app_ids:
            continue
        keychain_dir = candidate / "lark-cli"
        has_master = _has_non_empty_file(keychain_dir / "master.key")
        has_appsecret = any(p.is_file() and p.stat().st_size > 0 for p in keychain_dir.glob("appsecret_*.enc"))
        if has_master and has_appsecret:
            return {
                "path": str(candidate),
                "existing_app_ids": list(existing_app_ids)
            }
    return None


def bind_credentials(app_id: str, app_secret: str, app_name: str = "default", force_rebind: bool = False) -> Dict:
    """
    绑定飞书应用凭证
    lark-cli config init 可能将配置写入 HOME 目录（沙箱本地），
    为确保配置持久化到 TOS，需要将配置复制到 cwd（TOS 挂载路径）下。
    """
    try:
        cwd = Path.cwd()
        home = Path.home()

        # cwd 下已有有效 app 配置时直接复用，避免误跑绑定脚本删除已有 user 授权 token。
        existing_dir = _find_existing_config(cwd, app_id)
        if existing_dir:
            target_dir = cwd / ".feishu_cli"
            if existing_dir.name == ".feishu-cli":
                if target_dir.exists() and target_dir != existing_dir:
                    shutil.rmtree(target_dir)
                existing_dir.rename(target_dir)
                existing_dir = target_dir
            return {
                "status": "success",
                "message": f"Existing credentials for app '{app_name}' are already bound",
                "app_name": app_name,
                "app_id": app_id,
                "config_path": str(existing_dir),
                "config_verified": True,
                "source": "existing_in_cwd",
                "skipped_rebind": True
            }

        existing_other_app = _find_existing_config_with_other_app(cwd, app_id)
        if existing_other_app and not force_rebind:
            return {
                "status": "error",
                "error": "Existing lark-cli config uses a different appId. Refusing to overwrite without LARK_CLI_FORCE_REBIND=1.",
                "app_name": app_name,
                "requested_app_id": app_id,
                "existing_app_ids": existing_other_app["existing_app_ids"],
                "config_path": existing_other_app["path"],
                "hint": "Only set LARK_CLI_FORCE_REBIND=1 when the user explicitly wants to switch the Feishu app."
            }

        # 先清理 cwd 下旧的云端配置，确保 config init 从零生成
        for dirname in [".feishu_cli", ".feishu-cli"]:
            old_dir = cwd / dirname
            if old_dir.exists():
                shutil.rmtree(old_dir)

        # 使用 stdin 传入 app_secret
        proc = subprocess.Popen(
            ["lark-cli", "config", "init", "--app-id", app_id, "--app-secret-stdin", "--brand", "feishu"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        stdout, _ = proc.communicate(input=app_secret + "\n", timeout=50)
        
        # 检查 lark-cli 输出是否表示成功
        if "OK:" in stdout or "Configuration saved" in stdout:
            # 查找 lark-cli 实际写入的配置目录
            source_dir = None
            target_dir = cwd / ".feishu_cli"
            # 按优先级查找：cwd 下（已在 TOS）> home 下（沙箱本地，需复制）
            for base in [cwd, home]:
                for dirname in [".feishu-cli", ".feishu_cli"]:
                    candidate = base / dirname
                    if candidate.exists() and any(candidate.iterdir()):
                        source_dir = candidate
                        break
                if source_dir:
                    break

            # 如果配置写在 HOME 目录（沙箱本地），复制到 cwd（TOS 挂载路径）持久化
            if source_dir and source_dir.parent != cwd:
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.copytree(source_dir, target_dir)
                copied_from = str(source_dir)
            elif source_dir and source_dir.parent == cwd and source_dir.name == ".feishu-cli":
                # 配置在 cwd 下但目录名是 .feishu-cli，重命名为 .feishu_cli 保持一致
                if target_dir.exists() and target_dir != source_dir:
                    shutil.rmtree(target_dir)
                if source_dir != target_dir:
                    source_dir.rename(target_dir)
                copied_from = "renamed"
            else:
                copied_from = "already_in_cwd"

            # 验证最终配置是否存在于 cwd 下
            config_verified = target_dir.exists() and any(target_dir.iterdir())

            return {
                "status": "success",
                "message": f"Successfully bound credentials for app '{app_name}'",
                "app_name": app_name,
                "app_id": app_id,
                "config_path": str(target_dir),
                "config_verified": config_verified,
                "source": copied_from if source_dir else "not_found",
                "cli_output": stdout.strip()
            }
        else:
            return {
                "status": "error",
                "error": stdout.strip() or "Unknown error",
                "exit_code": proc.returncode,
                "cwd": str(cwd)
            }

    except subprocess.TimeoutExpired:
        proc.kill()
        partial_out, _ = proc.communicate()
        return {
            "status": "error",
            "error": "Timeout waiting for lark-cli config init",
            "cli_output": (partial_out or "").strip(),
            "cwd": str(Path.cwd())
        }
    except FileNotFoundError:
        return {
            "status": "error",
            "error": "lark-cli not found in PATH",
            "cwd": str(Path.cwd())
        }
    except Exception as e:
        return {
            "status": "error",
            "error": f"Unexpected error: {e}",
            "cwd": str(Path.cwd())
        }


def main():
    parser = argparse.ArgumentParser(
        description="绑定飞书应用凭证到飞书 CLI 配置"
    )

    parser.add_argument(
        "--app-name",
        default="default",
        help="应用名称（默认为 default）"
    )

    args = parser.parse_args()

    # 从环境变量获取凭证。AppID/AppSecret 只允许平台环境变量注入，避免模型通过参数误覆盖用户已有授权。
    app_id = os.environ.get("FEISHU_APP_ID_7629232824918245402")
    app_secret = os.environ.get("FEISHU_APP_SECRET_7629232824918245402")
    force_rebind = os.environ.get("LARK_CLI_FORCE_REBIND") == "1"

    if not app_id:
        output = {
            "status": "error",
            "error": "App ID is required."
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    if not app_secret:
        output = {
            "status": "error",
            "error": "App Secret is required."
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    # 绑定凭证
    result = bind_credentials(app_id, app_secret, args.app_name, force_rebind=force_rebind)

    if result.get("status") == "success":
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)
    else:
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
