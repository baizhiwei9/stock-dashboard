# -*- coding: utf-8 -*-
"""飞书图片上传与卡片落盘（case 内自足实现，不依赖其它仓的帮助模块）。

只用标准库 + `feishu_creds`（stock-dashboard / precious-task 的 `case/_lib` 都有）：
- `token()`        取 tenant_access_token（发图片需要应用身份）
- `upload_image()` 上传 PNG 拿 img_key
- `dump_card()`    把卡片原样写到 out/card.json + card.md，供展示页复用
"""
import hashlib
import json
import os
import urllib.request

try:                                                     # 仓内凭据库
    import feishu_creds
except Exception:                                        # noqa: BLE001
    feishu_creds = None

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/images"


def _cred(name, env):
    """凭据取值：环境变量优先，其次仓内 feishu_creds。"""
    val = os.environ.get(env)
    if val:
        return val
    if feishu_creds is not None:
        return getattr(feishu_creds, name, "") or ""
    return ""


def token():
    app_id = _cred("APP_ID", "FEISHU_APP_ID")
    app_secret = _cred("APP_SECRET", "FEISHU_APP_SECRET")
    if not (app_id and app_secret):
        raise RuntimeError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET（或 case/_lib 凭据库），无法上传图片")
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    res = json.loads(urllib.request.urlopen(req, timeout=20).read())
    if res.get("code") != 0:
        raise RuntimeError(f"飞书取 token 失败：{res.get('code')} {res.get('msg')}")
    return res["tenant_access_token"]


def upload_image(tok, path):
    """上传图片，成功返回 img_key，失败返回 None（缺图不阻断文字卡片）。"""
    boundary = "----x" + os.urandom(8).hex()
    raw = open(path, "rb").read()
    body = (("--%s\r\n" % boundary).encode()
            + b'Content-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n'
            + ("--%s\r\n" % boundary).encode()
            + b'Content-Disposition: form-data; name="image"; filename="i.png"\r\n'
              b"Content-Type: image/png\r\n\r\n" + raw
            + ("\r\n--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(
        UPLOAD_URL, data=body,
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=90).read())
        if res.get("code") == 0:
            return res["data"]["image_key"]
        print("[飞书] 图片上传失败:", res.get("code"), res.get("msg"))
    except Exception as exc:                             # noqa: BLE001
        print("[飞书] 图片上传异常:", str(exc)[:120])
    return None


def dump_card(card, out_dir):
    """卡片原文落盘（展示页读 card.json；card.md 作纯文本兜底）。"""
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "card.json"), "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        lines = []
        for el in card.get("card", {}).get("elements", []):
            tag = el.get("tag")
            if tag == "div" and "text" in el:
                lines.append(el["text"].get("content", ""))
            elif tag == "div" and "fields" in el:
                lines.extend(x["text"]["content"].replace("\n", " ") for x in el["fields"])
            elif tag == "img":
                lines.append(f"[图] {el.get('alt', {}).get('content', '')}")
            elif tag == "note":
                lines.append("— " + " ".join(x.get("content", "") for x in el.get("elements", [])))
        with open(os.path.join(out_dir, "card.md"), "w", encoding="utf-8") as f:
            f.write(card.get("card", {}).get("header", {}).get("title", {}).get("content", "") + "\n\n")
            f.write("\n".join(lines) + "\n")
        return True
    except Exception as exc:                             # noqa: BLE001
        print("[飞书] 卡片落盘失败:", str(exc)[:120])
        return False


def fingerprint(card):
    return hashlib.md5(json.dumps(card, ensure_ascii=False).encode()).hexdigest()[:8]