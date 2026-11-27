#!/bin/bash
# 沧海一土狗每日归档 - crontab 调用入口
# 每天 23:30 执行，抓取当天第三类文章上传到 IMA 知识库
#
# 2026-11-13 迁移：原目录 /Coze/Drive/飞书_cli 变成只读挂载，无法写入日志和 state，
#                 整体迁到工作目录下运行。同时按大佬要求改为「成功静默、失败才告警」。

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1   # 2026-11-27 去云盘化：以脚本所在目录为根

# 飞书 CLI 环境变量（crontab 环境下需要显式指定）
export LARKSUITE_CLI_CONFIG_DIR="/app/data/所有对话/主对话/.feishu_cli"
export LARKSUITE_CLI_DATA_DIR="/app/data/所有对话/主对话/.feishu_cli"
export LARKSUITE_CLI_LOG_DIR="/tmp"

# 飞书通知群
ALERT_CHAT="oc_5971f823bf987081f722f2942bbc8d71"
# 土狗群chat_id
CHAT_ID="oc_0ca59fa0674e77c608e843d9d55a6757"
# 测试用wiki文章（用于API预检）
TEST_WIKI="https://my.feishu.cn/wiki/GBdWwKTCyioHGkkPNntcEfernSd"

# 日志文件
LOG_FILE="$HERE/logs/daily_$(date +%Y%m%d).log"
mkdir -p logs

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 开始执行 ===" >> "$LOG_FILE"

# 预检1: 飞书CLI用户授权检查（拉一条群消息）
echo "[预检] 检查飞书 CLI 用户授权..." >> "$LOG_FILE"
CLI_CHECK=$(lark-cli im +chat-messages-list --chat-id "$CHAT_ID" --page-size 1 --as user 2>&1)
if echo "$CLI_CHECK" | grep -q '"ok": true'; then
    echo "[预检] 飞书 CLI 用户授权正常" >> "$LOG_FILE"
else
    echo "[预检] 飞书 CLI 用户授权失效" >> "$LOG_FILE"
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') 执行结束（CLI授权失效） ===" >> "$LOG_FILE"
    lark-cli im +messages-send \
        --chat-id "$ALERT_CHAT" \
        --text "⚠️ 土狗每日归档异常 $(date '+%Y-%m-%d')
原因: 飞书 CLI 用户授权失效，无法拉取群消息
需要重新授权
日志: $LOG_FILE" \
        --as bot 2>/dev/null
    exit 1
fi

# 预检2: Wiki API 可用性检查
echo "[预检] 检查 Wiki 文档抓取 API..." >> "$LOG_FILE"
DOC_CHECK=$(lark-cli docs +fetch --doc "$TEST_WIKI" --doc-format markdown --as user 2>&1)
if echo "$DOC_CHECK" | grep -q '"ok": true'; then
    echo "[预检] Wiki 文档抓取 API 正常" >> "$LOG_FILE"
else
    echo "[预检] Wiki 文档抓取 API 失败" >> "$LOG_FILE"
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') 执行结束（Wiki API失败） ===" >> "$LOG_FILE"
    DOC_ERR=$(echo "$DOC_CHECK" | grep -o '"message": "[^"]*"' | head -1 | cut -d'"' -f4)
    lark-cli im +messages-send \
        --chat-id "$ALERT_CHAT" \
        --text "⚠️ 土狗每日归档异常 $(date '+%Y-%m-%d')
原因: Wiki 文档抓取 API 失败，无法获取文章正文
${DOC_ERR:-未知错误}
日志: $LOG_FILE" \
        --as bot 2>/dev/null
    exit 1
fi

# 执行主任务
echo "[执行] 开始每日增量归档..." >> "$LOG_FILE"
python3 tugou_archive.py --mode daily >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
echo "=== $(date '+%Y-%m-%d %H:%M:%S') 执行结束，退出码: $EXIT_CODE ===" >> "$LOG_FILE"

# ====== 告警判断：只有失败才告警 ======
# 1. 获取失败（CLI授权失效 / Wiki API失败）—— 预检阶段已经直接发告警退出了
# 2. 抓取报错 / 上传失败
NEED_ALERT=0
ALERT_REASON=""

if grep -q "ERROR" "$LOG_FILE"; then
    NEED_ALERT=1
    FAIL_MSG=$(grep "ERROR" "$LOG_FILE" | head -1 | sed 's/.*\[ERROR\] //' | cut -c1-100)
    ALERT_REASON="抓取失败: ${FAIL_MSG:-未知错误}"
fi

if grep -q "失败: [1-9]" "$LOG_FILE"; then
    NEED_ALERT=1
    FAIL_COUNT=$(grep "失败:" "$LOG_FILE" | tail -1 | grep -oP '失败:\s*\K\d+' || echo "0")
    ALERT_REASON="${ALERT_REASON} 上传失败 ${FAIL_COUNT} 篇"
fi

# 注意：当天0篇不告警（周末/节假日正常断更）

# ====== 发送失败告警 ======
if [ "$NEED_ALERT" -eq 1 ]; then
    lark-cli im +messages-send \
        --chat-id "$ALERT_CHAT" \
        --text "⚠️ 土狗每日归档异常 $(date '+%Y-%m-%d')
原因: ${ALERT_REASON}
日志: $LOG_FILE" \
        --as bot 2>/dev/null
fi

# ====== 兜底告警：脚本异常退出但没被上面规则捕获时也要报 ======
if [ "$EXIT_CODE" -ne 0 ] && [ "$NEED_ALERT" -eq 0 ]; then
    lark-cli im +messages-send \
        --chat-id "$ALERT_CHAT" \
        --text "⚠️ 土狗每日归档异常 $(date '+%Y-%m-%d')
原因: 脚本非正常退出（退出码 $EXIT_CODE），但未捕获到具体错误
日志: $LOG_FILE" \
        --as bot 2>/dev/null
fi

# ====== 成功静默：同步成功只写日志，不发消息（2026-11-13 大佬要求）======
SUCCESS_COUNT=$(grep "成功:" "$LOG_FILE" | tail -1 | grep -oP '成功:\s*\K\d+' || echo "0")
if [ "$SUCCESS_COUNT" -gt 0 ]; then
    SKIPPED_COUNT=$(grep "跳过:" "$LOG_FILE" | tail -1 | grep -oP '跳过:\s*\K\d+' || echo "0")
    echo "[静默] 成功上传 $SUCCESS_COUNT 篇，跳过 $SKIPPED_COUNT 篇（按设定不发送通知）" >> "$LOG_FILE"
fi

exit $EXIT_CODE
