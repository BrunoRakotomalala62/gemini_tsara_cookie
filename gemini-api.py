#!/usr/bin/env python3
"""
Gemini API Proxy v2 — Optimisé : cookies persistants + réponse rapide
=====================================================================
- Auto-extraction du token XSRF (SNlM0e) depuis la page Gemini
- Cache du XSRF avec refresh automatique en background
- httpx avec connection pooling (HTTP/2 keep-alive)
- Parsing optimisé de la réponse StreamGenerate
- ThreadPool pour requêtes concurrentes

Usage:
  python3 gemini-api.py [port] [cookie_file] [--no-daemon]

Endpoints:
  GET /api/gemini?prompt=TEXTE&uid=ID&model=MODELE
  GET /api/health
"""
import json, uuid, hashlib, ssl, time, re, os, sys, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    print("[WARN] httpx non installé → fallback urllib (pip install httpx pour perf optimale)")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG = {
    "port": 8080,
    "host": "0.0.0.0",
    "cookie_file": "cookies.txt",
    "gemini_bl": "boq_assistant-bard-web-server_20260716.08_p0",
    "timeout": 45,
    "xsrf_refresh": 3600,
    "pool_connections": 10,
    "pool_maxsize": 20,
}

_cache_lock = threading.Lock()
_cookies = {}
_xsrf_token = ""
_xsrf_ts = 0
_http_client = None

def parse_netscape_cookies(path):
    cookies = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
    return cookies

def cookie_header(cookies):
    return "; ".join(f"{k}={v}" for k, v in cookies.items())

def make_sapisidhash(sapisid):
    ts = int(time.time())
    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{h}"

def extract_xsrf_from_page():
    global _cookies, _http_client
    url = "https://gemini.google.com/app"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Cookie": cookie_header(_cookies),
    }
    try:
        if HAS_HTTPX and _http_client:
            r = _http_client.get(url, headers=headers, timeout=15, follow_redirects=True)
            raw = r.text
        else:
            import urllib.request as ur
            req = ur.Request(url, headers=headers)
            ctx = ssl.create_default_context()
            resp = ur.urlopen(req, context=ctx, timeout=15)
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[XSRF] Échec extraction: {e}")
        return None
    m = re.search(r'"SNlM0e"\s*:\s*"([^"]+)"', raw)
    if m:
        print(f"[XSRF] Token extrait: {m.group(1)[:30]}...")
        return m.group(1)
    print("[XSRF] Token non trouvé")
    return None

def get_xsrf():
    global _xsrf_token, _xsrf_ts
    now = time.time()
    if not _xsrf_token or (now - _xsrf_ts) > CONFIG["xsrf_refresh"]:
        with _cache_lock:
            if not _xsrf_token or (now - _xsrf_ts) > CONFIG["xsrf_refresh"]:
                token = extract_xsrf_from_page()
                if token:
                    _xsrf_token = token
                    _xsrf_ts = now
    return _xsrf_token

def background_xsrf_refresh():
    while True:
        time.sleep(CONFIG["xsrf_refresh"] // 2)
        try:
            get_xsrf()
        except Exception as e:
            print(f"[BG] Erreur: {e}")

MODELS = {
    "flash":    (1, 4),
    "thinking": (2, 0),
    "pro":      (3, 4),
    "auto":     (4, 4),
}

RE_WRB = re.compile(r'"wrb\.fr"')
RE_BARD_ERR = re.compile(r'BardErrorInfo\s*\[(\d+)\]')
RE_CODE_CLEAN = re.compile(r'```(?:python|javascript|text)\?code_.*?\n.*?```\n?', re.DOTALL)

def call_gemini(prompt, model="flash"):
    global _cookies, _http_client
    model_id, think_mode = MODELS.get(model, (1, 4))
    xsrf = get_xsrf()
    inner = [None] * 80
    inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [0]
    inner[7] = 1
    inner[10] = 1
    inner[11] = 0
    inner[17] = [[think_mode]]
    inner[18] = 0
    inner[27] = 1
    inner[30] = [4]
    inner[41] = [2]
    inner[53] = 0
    inner[59] = str(uuid.uuid4())
    inner[61] = []
    inner[68] = 1
    inner[79] = model_id
    outer = [None, json.dumps(inner)]
    from urllib.parse import urlencode
    params = {"f.req": json.dumps(outer)}
    if xsrf:
        params["at"] = xsrf
    body = urlencode(params)
    reqid = int(time.time() * 1000) % 1000000
    url = (f"https://gemini.google.com/_/BardChatUi/data/"
           f"assistant.lamda.BardFrontendService/StreamGenerate"
           f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c")
    sapisid = _cookies.get("SAPISID", "")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/app",
        "X-Same-Domain": "1",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": cookie_header(_cookies),
    }
    if sapisid:
        headers["Authorization"] = make_sapisidhash(sapisid)
    try:
        if HAS_HTTPX and _http_client:
            r = _http_client.post(url, content=body, headers=headers, timeout=CONFIG["timeout"])
            raw = r.text
        else:
            import urllib.request as ur
            req = ur.Request(url, data=body.encode(), headers=headers, method="POST")
            ctx = ssl.create_default_context()
            resp = ur.urlopen(req, context=ctx, timeout=CONFIG["timeout"])
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"HTTP error: {e}")
    err = RE_BARD_ERR.search(raw)
    if err:
        raise RuntimeError(f"BardErrorInfo [{err.group(1)}]")
    texts = []
    for line in raw.split("\n"):
        if not RE_WRB.search(line) or len(line) < 200:
            continue
        try:
            arr = json.loads(line)
            inner_str = arr[0][2]
            if not inner_str or len(inner_str) < 50:
                continue
            inner_data = json.loads(inner_str)
            if isinstance(inner_data, list) and len(inner_data) > 4 and inner_data[4]:
                for part in inner_data[4]:
                    if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                        for t in part[1]:
                            if isinstance(t, str) and t:
                                texts.append(t)
        except (json.JSONDecodeError, IndexError, TypeError):
            pass
    if texts:
        text = texts[-1].strip()
        return RE_CODE_CLEAN.sub('', text)
    return ""

class GeminiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} {fmt % args}")
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        t0 = time.time()
        if parsed.path == "/api/gemini":
            prompt = params.get("prompt", [None])[0]
            uid = params.get("uid", ["anonymous"])[0]
            model = params.get("model", ["flash"])[0]
            if not prompt:
                self.send_json({"error": "Paramètre 'prompt' requis"}, 400)
                return
            try:
                response = call_gemini(prompt, model)
                elapsed = round((time.time() - t0) * 1000)
                self.send_json({"success": True, "uid": uid, "prompt": prompt, "model": model, "response": response, "elapsed_ms": elapsed})
            except Exception as e:
                self.send_json({"success": False, "error": str(e), "elapsed_ms": round((time.time()-t0)*1000)}, 500)
        elif parsed.path == "/api/health":
            self.send_json({"status": "ok", "models": list(MODELS.keys()), "xsrf_cached": bool(_xsrf_token), "xsrf_age_s": int(time.time()-_xsrf_ts) if _xsrf_token else None, "cookies_loaded": len(_cookies), "http_backend": "httpx" if HAS_HTTPX else "urllib"})
        elif parsed.path == "/":
            self.send_json({"service": "Gemini API Proxy v2", "features": ["Auto-XSRF", "Keep-Alive HTTP/2", "Background Refresh"], "usage": "GET /api/gemini?prompt=TEXTE&uid=ID&model=MODELE", "models": list(MODELS.keys())})
        else:
            self.send_json({"error": "Not found"}, 404)

def main():
    global _cookies, _http_client
    import argparse
    parser = argparse.ArgumentParser(description="Gemini API Proxy v2")
    parser.add_argument("port", nargs="?", type=int, default=8080)
    parser.add_argument("cookie_file", nargs="?", default="cookies.txt")
    parser.add_argument("--no-daemon", action="store_true")
    parser.add_argument("--bl", help="Build label override")
    args = parser.parse_args()
    if args.bl:
        CONFIG["gemini_bl"] = args.bl
    if not os.path.exists(args.cookie_file):
        print(f"Fichier introuvable: {args.cookie_file}")
        sys.exit(1)
    _cookies = parse_netscape_cookies(args.cookie_file)
    print(f"✓ {len(_cookies)} cookies chargés")
    if HAS_HTTPX:
        limits = httpx.Limits(max_keepalive_connections=CONFIG["pool_connections"], max_connections=CONFIG["pool_maxsize"], keepalive_expiry=30)
        _http_client = httpx.Client(http2=True, limits=limits, timeout=CONFIG["timeout"])
        print(f"✓ httpx HTTP/2 (pool: {CONFIG['pool_connections']})")
    else:
        print("⚠ pip install httpx recommandé")
    print("⏳ Extraction XSRF...")
    token = get_xsrf()
    print(f"{'✓' if token else '⚠'} Token XSRF{' prêt' if token else ' non extrait'}")
    threading.Thread(target=background_xsrf_refresh, daemon=True).start()
    print("✓ BG refresh lancé")
    class TS(ThreadingMixIn, HTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    server = TS((CONFIG["host"], args.port), GeminiHandler)
    print(f"\n🚀 Gemini API Proxy v2 → http://0.0.0.0:{args.port}")
    print(f"   GET /api/gemini?prompt=bonjour&uid=123&model=flash")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Arrêt")
        if _http_client:
            _http_client.close()
        server.shutdown()

if __name__ == "__main__":
    main()
