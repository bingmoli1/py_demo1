# 检查 init.json 中纯 url 订阅条目是否存活
# 判定:明文节点/base64 解码出节点/clash yaml = 活;404/空/HTML/乱码 = 死
import json
import subprocess
import base64
import re
import time

PROXY = "socks5h://127.0.0.1:10808"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
MIRROR = "https://ghfast.top/"


def curl(url, out="tmp_body.bin", proxy=None, timeout=25):
    cmd = ["curl", "-sL", "--connect-timeout", "8", "--max-time", str(timeout),
           "-A", UA, "-o", out, "-w", "%{http_code}"]
    if proxy:
        cmd += ["-x", proxy]
    r = subprocess.run(cmd + [url], capture_output=True, text=True)
    try:
        body = open(out, "rb").read()
    except OSError:
        body = b""
    return r.stdout.strip(), body


def proxy_alive():
    code, _ = curl("https://www.google.com/generate_204", proxy=PROXY, timeout=8)
    return code == "204"


def schemes_ok(data):
    found = {}
    for m in re.finditer(rb'(vmess|vless|ss|trojan|hysteria2?|hy2)://', data):
        k = m.group(1).lower()
        found[k] = found.get(k, 0) + 1
    if any(k in found for k in (b'vmess', b'vless', b'trojan',
                                b'hysteria', b'hysteria2', b'hy2')):
        return True
    return found.get(b'ss', 0) >= 3


def classify(body):
    if len(body) < 20:
        return "empty"
    head = body[:4000].lower()
    if (b'<!doctype html' in head or b'<html' in head
            or b'just a moment' in head):
        return "html"
    if schemes_ok(body):
        return "plain"
    s = b''.join(body.split())
    if len(s) > 100:
        try:
            dec = base64.b64decode(s + b'=' * (-len(s) % 4))
            if schemes_ok(dec):
                return "base64"
        except Exception:
            pass
    if b'proxies:' in body[:8000] or b'"proxies"' in body[:8000]:
        return "clash"
    return "unknown"


def test_url(url, use_proxy):
    already_mirrored = (url.startswith(MIRROR)
                        or 'cdn.jsdelivr.net' in url
                        or 'proxy.v2gh.com' in url)
    is_gh = ('raw.githubusercontent.com' in url
             or 'github.com' in url)
    methods = []
    if use_proxy:
        methods.append(("proxy", url, PROXY))
    if is_gh and not already_mirrored:
        methods.append(("ghfast", MIRROR + url, None))
    methods.append(("direct", url, None))
    tries = []
    for name, u, px in methods:
        code, body = curl(u, proxy=px)
        v = classify(body)
        if v in ("plain", "base64", "clash"):
            return {"verdict": v, "via": name, "http": code}
        tries.append({"via": name, "http": code, "seen": v,
                      "bytes": len(body)})
        time.sleep(1)
    return {"verdict": "dead", "tries": tries}


def main():
    data = json.load(open("init.json", encoding="utf-8"))
    only = [v["url"] for v in data["select"] if set(v.keys()) == {"url"}]
    print(f"待检查 {len(only)} 条", flush=True)

    results = {}
    for rnd in (1, 2):
        if rnd == 2:
            dead = [u for u, r in results.items()
                    if r["verdict"] == "dead"]
            if not dead:
                break
            print(f"--- 第二轮重试 {len(dead)} 条 ---", flush=True)
            time.sleep(3)
        use_proxy = proxy_alive()
        if rnd == 1:
            print(f"本地代理可用: {use_proxy}", flush=True)
        todo = only if rnd == 1 else dead
        for i, u in enumerate(todo):
            r = test_url(u, use_proxy)
            results[u] = r
            print(f"[{rnd}|{i + 1}/{len(todo)}] {r['verdict']:7s} "
                  f"via={r.get('via', r.get('tries', '')[0]['via'] if r.get('tries') else '?'):6s} "
                  f"{u}", flush=True)

    dead = [u for u, r in results.items() if r["verdict"] == "dead"]
    json.dump(results, open("check_results.json", "w"),
              ensure_ascii=False, indent=1)
    print(f"=== 完成: 活 {len(results) - len(dead)} / 死 {len(dead)} ===",
          flush=True)
    for u in dead:
        print("DEAD:", u, flush=True)


if __name__ == "__main__":
    main()
