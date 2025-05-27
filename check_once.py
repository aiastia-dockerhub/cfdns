import os
import socket
import requests
import re
from dotenv import load_dotenv
from collections import defaultdict

# 加载 .env 配置
load_dotenv()

def is_ip(address):
    return re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", address) is not None

CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
ZONE_ID_CACHE = {}  # 缓存域名到Zone ID的映射
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# 获取主域名 (支持多级域名)
def get_main_domain(subdomain):
    parts = subdomain.split(".")
    tlds = ["com", "net", "org", "io", "me", "co", "uk", "xyz"]  # 根据需要扩展
    if len(parts) > 2 and parts[-2] in tlds:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])

# 获取Zone ID (带缓存)
def get_zone_id(domain):
    if domain in ZONE_ID_CACHE:
        return ZONE_ID_CACHE[domain]
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"https://api.cloudflare.com/client/v4/zones?name={domain}"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        result = resp.json()
        if result["result"]:
            zone_id = result["result"][0]["id"]
            ZONE_ID_CACHE[domain] = zone_id
            return zone_id
    print(f"自动获取 {domain} 的 Zone ID 失败: {resp.text}")
    return None

def resolve_ip(target, dns_servers=None):
    try:
        if dns_servers:
            import dns.resolver
            resolver = dns.resolver.Resolver()
            resolver.nameservers = dns_servers
            answer = resolver.resolve(target, 'A')
            return answer[0].to_text()
        else:
            return socket.gethostbyname(target)
    except Exception as e:
        print(f"无法解析 {target}: {e}")
        return None

def http_check(target, port=443, path="/"):
    try:
        protocol = "https" if port == 443 else "http"
        url = f"{protocol}://{target}{path}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            print(f"HTTP 状态码异常: {resp.status_code}")
            return False
    except Exception as e:
        print(f"HTTP 检测失败: {e}")
        return False

def tcp_check(target, port=22, timeout=5):
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return True
    except Exception as e:
        print(f"TCP 检测失败: {e}")
        return False

def get_dns_content(sub):
    main_domain = get_main_domain(sub)
    zone_id = get_zone_id(main_domain)
    if not zone_id:
        return None
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={sub}"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        result = resp.json()
        if result["result"]:
            return result["result"][0]["content"]
    return None

def notify_tg(message):
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            data = {"chat_id": TG_CHAT_ID, "text": message}
            requests.post(url, data=data)
        except Exception as e:
            print(f"TG 通知失败: {e}")

def update_dns(sub, content, use_cdn=True, notify_msg=None):
    main_domain = get_main_domain(sub)
    zone_id = get_zone_id(main_domain)
    if not zone_id:
        print(f"无法获取 {sub} 的 Zone ID，跳过更新")
        return
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    record_type = "A" if is_ip(content) else "CNAME"
    data = {
        "type": record_type,
        "name": sub,
        "content": content,
        "ttl": 1,
        "proxied": use_cdn
    }
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?name={sub}"
    resp_get = requests.get(url, headers=headers)
    record_id = None
    if resp_get.status_code == 200:
        result = resp_get.json()
        if result["result"]:
            record_id = result["result"][0]["id"]
    if record_id:
        url_update = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
        resp = requests.put(url_update, json=data, headers=headers)
        print(f"更新 {sub} -> {content} 状态: {resp.status_code}")
    else:
        add_url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
        add_resp = requests.post(add_url, json=data, headers=headers)
        print(f"新建 {sub} -> {content} 状态: {add_resp.status_code}")
    if notify_msg:
        notify_tg(notify_msg)

# 自动识别多组业务配置
def load_groups_from_env():
    groups = defaultdict(dict)
    for key, value in os.environ.items():
        if "_" in key and key.endswith(("MAIN_IP", "BACKUP_IP", "CHECK_PORT", "SUBDOMAINS")):
            prefix, conf = key.split("_", 1)
            groups[prefix][conf] = value
    return groups

groups = load_groups_from_env()

for group_name, conf in groups.items():
    main_ip = conf.get("MAIN_IP")
    backup_ip = conf.get("BACKUP_IP")
    port = int(conf.get("CHECK_PORT", "443"))
    subdomains = [s.strip() for s in conf.get("SUBDOMAINS", "").split(",") if s.strip()]
    if not main_ip or not backup_ip or not subdomains:
        continue
    print(f"\n=== 业务组 {group_name} ===")
    for sub in subdomains:
        print(f"\n--- 检查 {sub} ---")
        content = get_dns_content(sub)
        print(f"DNS 记录内容: {content}")
        if not content:
            print(f"未获取到 {sub} 的 DNS 记录，跳过")
            continue
        if content == main_ip:
            print(f"{sub} 当前为主，检测网站...")
            ok = http_check(sub, port, path="/")
            print(f"HTTP 检测结果: {'正常' if ok else '异常'}")
            if not ok:
                msg = f"[切换通知] {sub} 检测异常，已切换到备IP {backup_ip}"
                print(f"检测异常，切换 {sub} 到备IP {backup_ip}")
                update_dns(sub, backup_ip, use_cdn=True, notify_msg=msg)
        elif content == backup_ip:
            print(f"{sub} 当前为备，检测主IP {main_ip}...")
            ip = resolve_ip(main_ip)
            if not ip:
                print(f"主IP解析失败")
                continue
            ok = tcp_check(ip, port)
            print(f"主IP TCP 检测结果: {'正常' if ok else '异常'}")
            if ok:
                msg = f"[切换通知] {sub} 检测主IP恢复，已切换回主IP {main_ip}"
                print(f"主IP恢复，切换 {sub} 回主IP {main_ip}")
                update_dns(sub, main_ip, use_cdn=True, notify_msg=msg)
        else:
            print(f"{sub} 当前指向未知内容: {content}")