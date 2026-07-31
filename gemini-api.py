#!/usr/bin/env python3
"""
Gemini API Proxy v4 — Image support + déploiement-ready
=====================================================================
- GEMINI_COOKIES_B64 : cookies en base64 (format Netscape)
- GEMINI_BL           : build label Gemini (optionnel)
- PORT                : port du serveur (défaut 8080)

Usage:
  # Local (fichier)
  python3 gemini-api.py [port] [cookie_file]

  # Déploiement (env var)
  GEMINI_COOKIES_B64=$(base64 -w0 cookies.txt) python3 gemini-api.py

Endpoints:
  GET /api/gemini?prompt=TEXTE&uid=ID&model=MODELE
  GET /api/gemini?prompt=TEXTE&image_url=URL&uid=ID
  GET /api/health
"""
import json, uuid, hashlib, ssl, time, re, os, sys, threading, base64, io
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs, unquote

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG (override via env vars)
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG = {
    "port": int(os.getenv("PORT", "8080")),
    "host": "0.0.0.0",
    "cookie_file": "cookies.txt",
    "gemini_bl": os.getenv("GEMINI_BL", "boq_assistant-bard-web-server_20260716.08_p0"),
    "timeout": 60,  # augmenté pour les uploads d'image
    "xsrf_refresh": 3600,
    "pool_connections": 10,
    "pool_maxsize": 20,
}

# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL CACHE
# ═══════════════════════════════════════════════════════════════════════════════

_cache_lock = threading.Lock()
_cookies = {}
_xsrf_token = ""
_xsrf_ts = 0
_push_id = "feeds/nrij2vo2gajxiu"  # fallback — extrait de la page Gemini
_http_client = None

def load_cookies(cookie_file=None):
    raw = None
    b64 = os.getenv("GEMINI_COOKIES_B64", "")
    if b64:
        try:
            raw = base64.b64decode(b64).decode("utf-8")
            print(f"✓ Cookies chargés depuis GEMINI_COOKIES_B64 ({len(b64)} chars base64)")
        except Exception as e:
            print(f"⚠ GEMINI_COOKIES_B64 invalide: {e}")
    if not raw and cookie_file:
        if os.path.exists(cookie_file):
            with open(cookie_file) as f:
                raw = f.read()
            print(f"✓ Cookies chargés depuis {cookie_file}")
        else:
            print(f"⚠ Fichier introuvable: {cookie_file}")
    if not raw:
        print("💥 Aucun cookie trouvé ! Mets GEMINI_COOKIES_B64=<base64>")
        sys.exit(1)
    cookies = {}
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("#") or not line: continue
        parts = line.split("\t")
        if len(parts) >= 7: cookies[parts[5]] = parts[6]
    if not cookies:
        print("💥 Aucun cookie parsé !")
        sys.exit(1)
    return cookies

def cookie_header(cookies):
    return "; ".join(f"{k}={v}" for k, v in cookies.items())

def make_sapisidhash(sapisid):
    ts = int(time.time())
    h = hashlib.sha1(f"{ts} {sapisid} https://gemini.google.com".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{h}"

def extract_xsrf_from_page():
    global _cookies, _http_client, _push_id
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
        pm = re.search(r'"KnDnFf"\s*:\s*"([^"]+)"', raw)
        if pm:
            _push_id = pm.group(1)
            print(f"[XSRF] Push-ID extrait: {_push_id}")
        return m.group(1)
    print("[XSRF] Token non trouvé dans la page")
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
        try: get_xsrf()
        except Exception as e: print(f"[BG] Erreur refresh: {e}")

MODELS = {
    "flash":    (1, 4),
    "thinking": (2, 0),
    "pro":      (3, 4),
    "auto":     (4, 4),
}

RE_WRB = re.compile(r'"wrb\.fr"')
RE_BARD_ERR = re.compile(r'BardErrorInfo\s*\[(\d+)\]')
RE_CODE_CLEAN = re.compile(r'```(?:python|javascript|text)\?code_.*?\n.*?```\n?', re.DOTALL)

def upload_image_to_gemini(image_url):
    """Télécharge une image depuis image_url, l'upload à Gemini, retourne le file_ref."""
    global _cookies, _push_id
    import urllib.request as ur
    parsed_url = urlparse(image_url)
    filename = os.path.basename(unquote(parsed_url.path)) or "image.jpg"
    if len(filename) > 100 or not re.match(r'[\w.-]+$', filename):
        filename = "image.jpg"
    req = ur.Request(image_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    ctx = ssl.create_default_context()
    resp = ur.urlopen(req, context=ctx, timeout=30)
    image_data = resp.read()
    content_type = resp.headers.get("Content-Type", "image/jpeg")
    print(f"[IMG] Téléchargé {len(image_data)} bytes ({content_type}) depuis {image_url[:60]}...")
    boundary = "----GeminiUploadBoundary" + str(int(time.time() * 10000))
    header_part = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    footer_part = f"\r\n--{boundary}--\r\n".encode()
    body = header_part + image_data + footer_part
    upload_url = "https://content-push.googleapis.com/upload"
    upload_headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Tenant-Id": "bard-storage",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
        "Push-ID": _push_id,
        "Cookie": cookie_header(_cookies),
    }
    req = ur.Request(upload_url, data=body, headers=upload_headers, method="POST")
    resp = ur.urlopen(req, context=ctx, timeout=30)
    file_ref = resp.read().decode("utf-8").strip()
    print(f"[IMG] Uploadé → {file_ref[:60]}...")
    return file_ref, filename

def call_gemini(prompt, model="flash", image_url=None):
    global _cookies, _http_client
    model_id, think_mode = MODELS.get(model, (1, 4))
    xsrf = get_xsrf()
    file_ref = None
    filename = None
    if image_url:
        try:
            file_ref, filename = upload_image_to_gemini(image_url)
        except Exception as e:
            raise RuntimeError(f"Image upload failed: {e}")
    inner = [None] * 80
    if file_ref:
        inner[0] = [prompt, 0, None, [[[file_ref], filename]], None, None, 0]
    else:
        inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6] = [0]; inner[7] = 1; inner[10] = 1; inner[11] = 0
    inner[17] = [[think_mode]]; inner[18] = 0; inner[27] = 1
    inner[30] = [4]; inner[41] = [2]; inner[53] = 0
    inner[59] = str(uuid.uuid4()); inner[61] = []; inner[68] = 1; inner[79] = model_id
    outer = [None, json.dumps(inner)]
    from urllib.parse import urlencode
    params = {"f.req": json.dumps(outer)}
    if xsrf: params["at"] = xsrf
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
    if sapisid: headers["Authorization"] = make_sapisidhash(sapisid)
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
    if err: raise RuntimeError(f"BardErrorInfo [{err.group(1)}]")
    texts = []
    for line in raw.split("\n"):
        if not RE_WRB.search(line) or len(line) < 200: continue
        try:
            arr = json.loads(line)
            inner_str = arr[0][2]
            if not inner_str or len(inner_str) < 50: continue
            inner_data = json.loads(inner_str)
            if isinstance(inner_data, list) and len(inner_data) > 4 and inner_data[4]:
                for part in inner_data[4]:
                    if isinstance(part, list) and len(part) > 1 and part[1] and isinstance(part[1], list):
                        for t in part[1]:
                            if isinstance(t, str) and t: texts.append(t)
        except (json.JSONDecodeError, IndexError, TypeError): pass
    if texts: return RE_CODE_CLEAN.sub('', texts[-1].strip())
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
            image_url = params.get("image_url", [None])[0]
            if not prompt:
                self.send_json({"error": "Paramètre 'prompt' requis"}, 400)
                return
            try:
                response = call_gemini(prompt, model, image_url=image_url)
                elapsed = round((time.time() - t0) * 1000)
                self.send_json({
                    "success": True, "uid": uid, "prompt": prompt,
                    "model": model, "response": response, "elapsed_ms": elapsed,
                })
            except Exception as e:
                self.send_json({"success": False, "error": str(e), "elapsed_ms": round((time.time() - t0) * 1000)}, 500)
        elif parsed.path == "/api/health":
            self.send_json({
                "status": "ok",
                "models": list(MODELS.keys()),
                "xsrf_cached": bool(_xsrf_token),
                "xsrf_age_s": int(time.time() - _xsrf_ts) if _xsrf_token else None,
                "cookies_loaded": len(_cookies),
                "http_backend": "httpx" if HAS_HTTPX else "urllib",
                "gemini_bl": CONFIG["gemini_bl"],
                "cookie_source": "env:GEMINI_COOKIES_B64" if os.getenv("GEMINI_COOKIES_B64") else "file",
                "push_id": _push_id,
            })
        elif parsed.path == "/":
            self.send_json({
                "service": "Gemini API Proxy v4",
                "features": ["Auto-XSRF", "Keep-Alive HTTP/2", "Env var support", "Image recognition"],
                "usage": "GET /api/gemini?prompt=TEXTE&uid=ID&model=MODELE&image_url=URL",
                "models": list(MODELS.keys()),
            })
        else:
            self.send_json({"error": "Not found"}, 404)

def main():
    global _cookies, _http_client
    import argparse
    parser = argparse.ArgumentParser(description="Gemini API Proxy v4")
    parser.add_argument("port", nargs="?", type=int, default=None)
    parser.add_argument("cookie_file", nargs="?", default="cookies.txt")
    parser.add_argument("--no-daemon", action="store_true")
    parser.add_argument("--bl", help="Build label override")
    args = parser.parse_args()
    if args.port: CONFIG["port"] = args.port
    if args.bl: CONFIG["gemini_bl"] = args.bl
    _cookies = load_cookies(args.cookie_file)
    print(f"✓ {len(_cookies)} cookies chargés")
    print(f"  SID: {'✓' if 'SID' in _cookies else '✗'}  "
          f"1PSID: {'✓' if '__Secure-1PSID' in _cookies else '✗'}  "
          f"COMPASS: {'✓' if 'COMPASS' in _cookies else '✗'}  "
          f"SAPISID: {'✓' if 'SAPISID' in _cookies else '✗'}")
    if HAS_HTTPX:
        limits = httpx.Limits(max_keepalive_connections=CONFIG["pool_connections"],
                              max_connections=CONFIG["pool_maxsize"], keepalive_expiry=30)
        try:
            _http_client = httpx.Client(http2=True, limits=limits, timeout=CONFIG["timeout"])
        except ImportError:
            _http_client = httpx.Client(http2=False, limits=limits, timeout=CONFIG["timeout"])
        print(f"✓ httpx HTTP/2 (pool: {CONFIG['pool_connections']})")
    else:
        print("⚠ pip install httpx recommandé")
    print("⏳ Extraction XSRF...")
    token = get_xsrf()
    print(f"{'✓' if token else '⚠'} Token XSRF{' prêt' if token else ' — sera extrait à la 1ère requête'}")
    threading.Thread(target=background_xsrf_refresh, daemon=True).start()
    print("✓ BG refresh lancé (toutes les 30min)")
    class TS(ThreadingMixIn, HTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    server = TS((CONFIG["host"], CONFIG["port"]), GeminiHandler)
    print(f"""
╔══════════════════════════════════════════════════════════╗
║     🚀 Gemini API Proxy v4 (Image support)               ║
║     http://0.0.0.0:{CONFIG['port']}                                    ║
║                                                        ║
║  GET /api/gemini?prompt=...&image_url=...&uid=123        ║
║  GET /api/health                                        ║
╚══════════════════════════════════════════════════════════╝
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Arrêt")
        if _http_client: _http_client.close()
        server.shutdown()

if __name__ == "__main__":
    main()
