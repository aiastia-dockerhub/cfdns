import os
import time
import socket
import requests
import re
from dotenv import load_dotenv
from collections import defaultdict

# 加载 .env 配置
load_dotenv()

def is_ip(address):
    return re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", address) is not None

def load_groups_from_env():
    groups = defaultdict(dict)
    for key, value in os.environ.items():
        if "_" in key and key.endswith(("MAIN_IP", "BACKUP_IP", "CHECK_PORT", "SUBDOMAINS")):
            prefix, conf = key.split("_", 1)
            groups[prefix][conf] = value
    return groups

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
            return 0
        else:
            print(f"HTTP 状态码异常: {resp.status_code}")
            return 100
    except Exception as e:
        print(f"HTTP 检测失败: {e}")
        return 100

def tcp_check(target, port=22, timeout=5):
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return 0
    except Exception as e:
        print(f"TCP 检测失败: {e}")
        return 100

def notify_tg(message, TG_BOT_TOKEN, TG_CHAT_ID):
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            data = {"chat_id": TG_CHAT_ID, "text": message}
            requests.post(url, data=data)
        except Exception as e:
            print(f"TG 通知失败: {e}")

def get_zone_id(domain, CLOUDFLARE_API_TOKEN):
    url = f"https://api.cloudflare.com/client/v4/zones?name={domain}"
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            result = resp.json()
            if result["result"]:
                return result["result"][0]["id"]
            else:
                print(f"未找到域名 {domain} 的 Zone ID")
        else:
            print(f"获取 Zone ID 失败: {resp.status_code}")
    except Exception as e:
        print(f"获取 Zone ID 异常: {e}")
    return None

def update_dns(subdomains, record_type, content, use_cdn, ZONE_ID, CLOUDFLARE_API_TOKEN, TG_BOT_TOKEN, TG_CHAT_ID, using_backup, MAIN_TARGET, BACKUP_TARGET):
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    for sub in subdomains:
        # 避免 CNAME 指向自身，改为指向 MAIN_IP 或 BACKUP_IP
        if content == sub:
            real_content = MAIN_TARGET if not using_backup else BACKUP_TARGET
        else:
            real_content = content
        data = {
            "type": record_type,
            "name": sub,
            "content": real_content,
            "ttl": 1,
            "proxied": use_cdn
        }
        print(f"ZONE_ID: {ZONE_ID}, 检查并更新/删除 DNS 记录: {sub} -> {real_content} (CDN: {use_cdn})")
        found_same_type = False
        for t in ["A", "AAAA", "CNAME"]:
            url = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records?type={t}&name={sub}"
            resp_get = requests.get(url, headers=headers)
            if resp_get.status_code == 200:
                result = resp_get.json()
                for rec in result["result"]:
                    if rec["type"] == record_type:
                        record_id = rec["id"]
                        url_update = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records/{record_id}"
                        resp = requests.put(url_update, json=data, headers=headers)
                        print(f"DNS 更新 {sub} -> {real_content} (CDN: {use_cdn}) Status: {resp.status_code}")
                        print(f"Cloudflare 返回: {resp.text}")
                        if resp.status_code == 200 or resp.status_code == 201:
                            notify_tg(f"[DNS] {sub} 更新为 {real_content}，CDN: {'启用' if use_cdn else '关闭'}", TG_BOT_TOKEN, TG_CHAT_ID)
                        else:
                            print(f"DNS 更新失败: {sub}")
                        found_same_type = True
                    else:
                        del_id = rec["id"]
                        del_url = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records/{del_id}"
                        del_resp = requests.delete(del_url, headers=headers)
                        print(f"已删除不同类型({t})记录: {sub}, 状态: {del_resp.status_code}")
        if not found_same_type:
            add_url = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records"
            add_resp = requests.post(add_url, json=data, headers=headers)
            print(f"新建 DNS 记录: {sub} -> {real_content} (CDN: {use_cdn}) 状态: {add_resp.status_code}")
            print(f"Cloudflare 返回: {add_resp.text}")
            if add_resp.status_code == 200 or add_resp.status_code == 201:
                notify_tg(f"[DNS] {sub} 新建为 {real_content}，CDN: {'启用' if use_cdn else '关闭'}", TG_BOT_TOKEN, TG_CHAT_ID)
            else:
                print(f"DNS 新建失败: {sub}")

# 公共配置
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
FAILURE_THRESHOLD = int(os.getenv("FAILURE_THRESHOLD", 3))
RECOVERY_THRESHOLD = int(os.getenv("RECOVERY_THRESHOLD", 2))
CHECK_INTERVAL = 60
PING_COUNT = 5
CUSTOM_DNS = ["1.1.1.1", "1.0.0.1"]

groups = load_groups_from_env()

# 每组业务维护独立状态
group_states = {}
for group_name, conf in groups.items():
    main_ip = conf.get("MAIN_IP")
    backup_ip = conf.get("BACKUP_IP")
    port = int(conf.get("CHECK_PORT", "443"))
    subdomains = [s.strip() for s in conf.get("SUBDOMAINS", "").split(",") if s.strip()]
    if not main_ip or not backup_ip or not subdomains:
        continue
    # 判断记录类型
    if is_ip(main_ip) and is_ip(backup_ip):
        record_type = "A"
    elif not is_ip(main_ip) and not is_ip(backup_ip):
        record_type = "CNAME"
    else:
        record_type = "A" if is_ip(main_ip) else "CNAME"
    # 自动获取 ZONE_ID
    main_domain = subdomains[0].split(".", 1)[-1]
    zone_id = get_zone_id(main_domain, CLOUDFLARE_API_TOKEN)
    group_states[group_name] = {
        "main_ip": main_ip,
        "backup_ip": backup_ip,
        "port": port,
        "subdomains": subdomains,
        "record_type": record_type,
        "zone_id": zone_id,
        "fail_count": 0,
        "success_count": 0,
        "using_backup": False
    }

while True:
    for group_name, state in group_states.items():
        main_ip = state["main_ip"]
        backup_ip = state["backup_ip"]
        port = state["port"]
        subdomains = state["subdomains"]
        record_type = state["record_type"]
        zone_id = state["zone_id"]
        fail_count = state["fail_count"]
        success_count = state["success_count"]
        using_backup = state["using_backup"]
        print(f"\n=== 业务组 {group_name} ===")
        if not using_backup:
            all_failed = True
            for sub in subdomains:
                resolved_ip = resolve_ip(sub, dns_servers=CUSTOM_DNS)
                if not resolved_ip:
                    loss = 100
                else:
                    loss = http_check(sub, port, path="/")
                print(f"检测主目标 {sub}（解析 IP: {resolved_ip}），丢包率: {loss}%")
                if loss < 60:
                    all_failed = False
            if all_failed:
                state["fail_count"] += 1
                state["success_count"] = 0
                print(f"主目标全部失败，连续失败次数：{state['fail_count']}")
                if state["fail_count"] >= FAILURE_THRESHOLD:
                    state["using_backup"] = True
                    update_dns(subdomains, record_type, backup_ip, True, zone_id, CLOUDFLARE_API_TOKEN, TG_BOT_TOKEN, TG_CHAT_ID, True, main_ip, backup_ip)
                    notify_tg(f"[故障转移] 业务组 {group_name} 主目标全部故障，切换至备用目标 {backup_ip}", TG_BOT_TOKEN, TG_CHAT_ID)
            else:
                state["fail_count"] = 0
                print("主目标正常")
        else:
            resolved_ip = resolve_ip(main_ip, dns_servers=CUSTOM_DNS)
            if not resolved_ip:
                loss = 100
            else:
                loss = tcp_check(resolved_ip, port)
            print(f"检测主 IP {main_ip}（解析 IP: {resolved_ip}），丢包率: {loss}%")
            if loss < 60:
                state["success_count"] += 1
                print(f"主 IP 恢复正常，连续成功：{state['success_count']}")
                if state["success_count"] >= RECOVERY_THRESHOLD:
                    state["using_backup"] = False
                    update_dns(subdomains, record_type, main_ip, True, zone_id, CLOUDFLARE_API_TOKEN, TG_BOT_TOKEN, TG_CHAT_ID, False, main_ip, backup_ip)
                    notify_tg(f"[恢复] 业务组 {group_name} 主 IP 恢复，切回主目标 {main_ip}", TG_BOT_TOKEN, TG_CHAT_ID)
                    state["fail_count"] = 0
            else:
                state["success_count"] = 0
                print("主 IP 仍然异常")
    time.sleep(CHECK_INTERVAL)
