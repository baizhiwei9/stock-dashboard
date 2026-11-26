#!/usr/bin/env python3
"""
邮件发送脚本
用法：python3 send_email.py --subject "主题" --body "正文"
配置：自动读取config.json中的发件人/授权码/收件人
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import json
import os
import sys
import argparse

# 配置文件路径
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.json')

def load_config():
    """加载邮件配置"""
    if not os.path.exists(CONFIG_PATH):
        print(f"错误：配置文件不存在: {CONFIG_PATH}")
        print("请先创建config.json，格式：")
        print('{')
        print('  "smtp_server": "smtp.163.com",')
        print('  "smtp_port": 465,')
        print('  "sender": "your_email@163.com",')
        print('  "password": "your_authorization_code",')
        print('  "receiver": "410119229@qq.com"')
        print('}')
        sys.exit(1)
    
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    required = ['smtp_server', 'smtp_port', 'sender', 'password', 'receiver']
    missing = [k for k in required if k not in config]
    if missing:
        print(f"错误：config.json缺少字段: {missing}")
        sys.exit(1)
    
    return config

def send_email(subject, body, use_ssl=True):
    """发送邮件"""
    config = load_config()
    
    # 构建邮件
    msg = MIMEMultipart('alternative')
    msg['From'] = Header(config['sender'], 'utf-8')
    msg['To'] = Header(config['receiver'], 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    
    # 添加正文
    body_part = MIMEText(body, 'plain', 'utf-8')
    msg.attach(body_part)
    
    try:
        # 连接SMTP服务器
        if use_ssl:
            server = smtplib.SMTP_SSL(config['smtp_server'], config['smtp_port'])
        else:
            server = smtplib.SMTP(config['smtp_server'], config['smtp_port'])
            server.starttls()
        
        # 登录并发送
        server.login(config['sender'], config['password'])
        server.sendmail(config['sender'], [config['receiver']], msg.as_string())
        server.quit()
        
        print(f"✓ 邮件发送成功")
        print(f"  收件人: {config['receiver']}")
        print(f"  主题: {subject}")
        return True
        
    except Exception as e:
        print(f"✗ 邮件发送失败: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='发送邮件')
    parser.add_argument('--subject', '-s', required=True, help='邮件主题')
    parser.add_argument('--body', '-b', required=True, help='邮件正文')
    parser.add_argument('--no-ssl', action='store_true', help='不使用SSL（使用STARTTLS）')
    
    args = parser.parse_args()
    
    success = send_email(args.subject, args.body, use_ssl=not args.no_ssl)
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
