# -*- coding: utf-8 -*-
"""dispatch 阶段：任务自己的推送（飞书/邮件）

契约：退出码 0 成功；失败时框架只报警不中断（stages[].optional）。
不需要推送的任务，把 pipeline 里的 dispatch 阶段整段删掉即可。
"""


def main():
    print("模板任务不发任何消息；接推送时在这里读 out/report.md 发出去")


if __name__ == "__main__":
    main()
