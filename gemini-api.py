#!/usr/bin/env python3
"""
Gemini API Proxy v6 — Multi-Comptes + Rotation Auto sur Quota
==============================================================
- GEMINI_COOKIES_B64 : cookies (base64 Netscape), multi-comptes avec |||
  Ex: base64_compte1|||base64_compte2|||base64_compte3
- GEMINI_BL           : build label Gemini (optionnel)
- PORT                : port du serveur (défaut 8080)

Endpoints:
  GET /api/gemini?prompt=TEXTE&uid=ID&model=MODELE&image_url=URL
  GET /api/health
"""
import json, uuid, hashlib, ssl, time, re, os, sys, threading, base64, io
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs, unquote

try: import httpx; HAS_HTTPX = True
except ImportError: HAS_HTTPX = False

CONFIG = {"port":int(os.getenv("PORT","8080")),"host":"0.0.0.0","cookie_file":"cookies.txt",
          "gemini_bl":os.getenv("GEMINI_BL","boq_assistant-bard-web-server_20260716.08_p0"),
          "timeout":90,"xsrf_refresh":3600,"pool_connections":10,"pool_maxsize":20}

_cache_lock = threading.Lock()
_account_lock = threading.Lock()
_accounts = []
_active_idx = 0
_cookies = {}
_xsrf_token = ""; _xsrf_ts = 0
_push_id = "feeds/nrij2vo2gajxiu"
_http_client = None

RE_QUOTA = re.compile(r'(limit\s*resets?|create\s*more\s*images?|usage\s*limit|quota\s*exceeded)', re.IGNORECASE)

def parse_cookie_raw(raw_text):
    cookies = {}
    for line in raw_text.split("\n"):
        line = line.strip()
        if line.startswith("#") or not line: continue
        parts = line.split("\t")
        if len(parts) >= 7: cookies[parts[5]] = parts[6]
    return cookies

def load_cookies(cookie_file=None):
    raw_sets = []
    b64 = os.getenv("GEMINI_COOKIES_B64", "")
    if b64:
        for chunk in b64.split("|||"):
            chunk = chunk.strip()
            if not chunk: continue
            try: raw_sets.append(base64.b64decode(chunk).decode("utf-8"))
            except Exception as e: print(f"⚠ Chunk base64 invalide: {e}")
        print(f"✓ {len(raw_sets)} compte(s) chargé(s) depuis GEMINI_COOKIES_B64")
    if not raw_sets and cookie_file:
        if os.path.exists(cookie_file):
            with open(cookie_file) as f: raw_sets.append(f.read())
            print(f"✓ 1 compte chargé depuis {cookie_file}")
    if not raw_sets:
        print("💥 Aucun cookie ! Utilise GEMINI_COOKIES_B64=<base64> ou GEMINI_COOKIES_B64=c1|||c2")
        sys.exit(1)
    accounts = []
    for i, raw in enumerate(raw_sets):
        c = parse_cookie_raw(raw)
        if not c: print(f"⚠ Compte {i+1}: aucun cookie parsé, ignoré"); continue
        sid_ok = "✓" if "SID" in c else "✗"; psid_ok = "✓" if "__Secure-1PSID" in c else "✗"
        print(f"  Compte {i+1}: {len(c)} cookies | SID={sid_ok} __Secure-1PSID={psid_ok}")
        accounts.append(c)
    if not accounts: print("💥 Aucun compte valide !"); sys.exit(1)
    return accounts

def cookie_header(cookies): return "; ".join(f"{k}={v}" for k, v in cookies.items())

def make_sapisidhash(sapisid):
    ts = int(time.time())
    return f"SAPISIDHASH {ts}_{hashlib.sha1(f'{ts} {sapisid} https://gemini.google.com'.encode()).hexdigest()}"

def rotate_account():
    global _cookies, _active_idx, _xsrf_token, _xsrf_ts
    with _account_lock:
        old = _active_idx
        _active_idx = (_active_idx + 1) % len(_accounts)
        _cookies = _accounts[_active_idx]
        _xsrf_token = ""; _xsrf_ts = 0
    print(f"🔄 Rotation: compte {old+1} → compte {_active_idx+1}")
    return _active_idx

def extract_xsrf_from_page():
    global _push_id
    url = "https://gemini.google.com/app"
    headers = {"User-Agent":"Mozilla/5.0","Accept":"text/html","Cookie":cookie_header(_cookies)}
    try:
        if HAS_HTTPX and _http_client:
            r = _http_client.get(url,headers=headers,timeout=15,follow_redirects=True); raw = r.text
        else:
            import urllib.request as ur
            req = ur.Request(url,headers=headers); ctx = ssl.create_default_context()
            resp = ur.urlopen(req,context=ctx,timeout=15); raw = resp.read().decode("utf-8",errors="replace")
    except Exception as e: print(f"[XSRF] Échec: {e}"); return None
    m = re.search(r'"SNlM0e"\s*:\s*"([^"]+)"', raw)
    if m:
        print(f"[XSRF] Token: {m.group(1)[:30]}... (compte {_active_idx+1})")
        pm = re.search(r'"KnDnFf"\s*:\s*"([^"]+)"', raw)
        if pm: _push_id = pm.group(1)
        return m.group(1)
    return None

def get_xsrf():
    global _xsrf_token, _xsrf_ts
    now = time.time()
    if not _xsrf_token or (now - _xsrf_ts) > CONFIG["xsrf_refresh"]:
        with _cache_lock:
            if not _xsrf_token or (now - _xsrf_ts) > CONFIG["xsrf_refresh"]:
                t = extract_xsrf_from_page()
                if t: _xsrf_token = t; _xsrf_ts = now
    return _xsrf_token

def background_xsrf_refresh():
    while True:
        time.sleep(CONFIG["xsrf_refresh"] // 2)
        try: get_xsrf()
        except Exception as e: print(f"[BG] Erreur: {e}")

MODELS = {"flash":(1,4),"thinking":(2,0),"pro":(3,4),"auto":(4,4)}
RE_WRB = re.compile(r'"wrb\.fr"')
RE_BARD_ERR = re.compile(r'BardErrorInfo\s*\[(\d+)\]')
RE_CODE_CLEAN = re.compile(r'```(?:python|javascript|text)\?code_.*?\n.*?```\n?', re.DOTALL)
RE_CARD_CONTENT = re.compile(r'https?://(?:www\.)?googleusercontent\.com/[^\s"\'<>]+')

def upload_image_to_gemini(image_url):
    import urllib.request as ur
    parsed_url = urlparse(image_url)
    filename = os.path.basename(unquote(parsed_url.path)) or "image.jpg"
    if len(filename) > 100 or not re.match(r'[\w.-]+$', filename): filename = "image.jpg"
    req = ur.Request(image_url,headers={"User-Agent":"Mozilla/5.0"})
    ctx = ssl.create_default_context()
    resp = ur.urlopen(req,context=ctx,timeout=30)
    image_data = resp.read()
    content_type = resp.headers.get("Content-Type","image/jpeg")
    print(f"[IMG] Téléchargé {len(image_data)} bytes ({content_type})")
    boundary = "----GemUpload" + str(int(time.time() * 10000))
    cp = f'Content-Disposition: form-data; name="file"; filename="{filename}"'
    header_part = f"--{boundary}\r\n{cp}\r\nContent-Type: {content_type}\r\n\r\n".encode()
    body = header_part + image_data + f"\r\n--{boundary}--\r\n".encode()
    req = ur.Request("https://content-push.googleapis.com/upload",data=body,headers={
        "Content-Type":f"multipart/form-data; boundary={boundary}",
        "X-Tenant-Id":"bard-storage","Origin":"https://gemini.google.com",
        "Referer":"https://gemini.google.com/","Push-ID":_push_id,"Cookie":cookie_header(_cookies),
    },method="POST")
    resp = ur.urlopen(req,context=ctx,timeout=30)
    file_ref = resp.read().decode("utf-8").strip()
    print(f"[IMG] Uploadé → {file_ref[:60]}...")
    return file_ref, filename

def call_gemini_once(prompt, model, image_url):
    global _cookies, _http_client
    model_id, think_mode = MODELS.get(model,(1,4))
    xsrf = get_xsrf()
    file_ref = None; filename = None
    if image_url: file_ref, filename = upload_image_to_gemini(image_url)
    inner = [None] * 80
    if file_ref: inner[0] = [prompt,0,None,[[[file_ref],filename]],None,None,0]
    else: inner[0] = [prompt,0,None,None,None,None,0]
    inner[1] = ["en"]
    inner[2] = ["","","",None,None,None,None,None,None,""]
    inner[6]=[0]; inner[7]=1; inner[10]=1; inner[11]=0
    inner[17]=[[think_mode]]; inner[18]=0; inner[27]=1
    inner[30]=[4]; inner[41]=[2]; inner[53]=0
    inner[59]=str(uuid.uuid4()); inner[61]=[]; inner[68]=1; inner[79]=model_id
    outer = [None,json.dumps(inner)]
    from urllib.parse import urlencode
    params = {"f.req":json.dumps(outer)}
    if xsrf: params["at"] = xsrf
    body = urlencode(params)
    reqid = int(time.time()*1000)%1000000
    url = (f"https://gemini.google.com/_/BardChatUi/data/"
           f"assistant.lamda.BardFrontendService/StreamGenerate"
           f"?bl={CONFIG['gemini_bl']}&hl=en&_reqid={reqid}&rt=c")
    sapisid = _cookies.get("SAPISID","")
    headers = {"Content-Type":"application/x-www-form-urlencoded","Origin":"https://gemini.google.com",
               "Referer":"https://gemini.google.com/app","X-Same-Domain":"1",
               "User-Agent":"Mozilla/5.0","Cookie":cookie_header(_cookies)}
    if sapisid: headers["Authorization"] = make_sapisidhash(sapisid)
    try:
        if HAS_HTTPX and _http_client:
            r = _http_client.post(url,content=body,headers=headers,timeout=CONFIG["timeout"]); raw = r.text
        else:
            import urllib.request as ur
            req = ur.Request(url,data=body.encode(),headers=headers,method="POST")
            ctx = ssl.create_default_context()
            resp = ur.urlopen(req,context=ctx,timeout=CONFIG["timeout"]); raw = resp.read().decode("utf-8",errors="replace")
    except Exception as e: raise RuntimeError(f"HTTP error: {e}")
    err = RE_BARD_ERR.search(raw)
    if err: raise RuntimeError(f"BardErrorInfo [{err.group(1)}]")
    texts = []
    for line in raw.split("\n"):
        if not RE_WRB.search(line) or len(line) < 200: continue
        try:
            arr = json.loads(line); inner_str = arr[0][2]
            if not inner_str or len(inner_str) < 50: continue
            inner_data = json.loads(inner_str)
            if isinstance(inner_data,list) and len(inner_data)>4 and inner_data[4]:
                for part in inner_data[4]:
                    if isinstance(part,list) and len(part)>1 and part[1] and isinstance(part[1],list):
                        for t in part[1]:
                            if isinstance(t,str) and t: texts.append(t)
        except (json.JSONDecodeError,IndexError,TypeError): pass
    text = RE_CODE_CLEAN.sub('',texts[-1].strip()) if texts else ""
    images = []; seen = set()
    for match in RE_CARD_CONTENT.finditer(raw):
        url = match.group(0).rstrip('\\')
        if url not in seen: seen.add(url); images.append(url)
    return text, images

def call_gemini(prompt, model="flash", image_url=None):
    max_rotation = len(_accounts)
    for attempt in range(max_rotation):
        text, images = call_gemini_once(prompt, model, image_url)
        if image_url and RE_QUOTA.search(text) and len(_accounts)>1 and attempt < max_rotation-1:
            print(f"⚠ Quota image épuisé compte {_active_idx+1}, rotation...")
            rotate_account()
            continue
        return text, images
    text, images = call_gemini_once(prompt, model, image_url)
    return text, images

class GeminiHandler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): print(f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} {fmt % args}")
    def send_json(self,data,status=200):
        body = json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin","*"); self.send_header("Content-Length",str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path); t0 = time.time()
        if parsed.path == "/api/gemini":
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self.send_json({"error": "Corps de requête vide"}, 400); return
            
            try:
                post_data = self.rfile.read(content_length)
                params = json.loads(post_data.decode('utf-8'))
            except Exception as e:
                self.send_json({"error": f"JSON invalide: {str(e)}"}, 400); return

            prompt = params.get("prompt")
            uid = params.get("uid", "anonymous")
            model = params.get("model", "flash")
            image_url = params.get("image_url")
            
            if not prompt:
                self.send_json({"error": "Paramètre 'prompt' requis"}, 400); return
            
            try:
                response, images = call_gemini(prompt, model, image_url=image_url)
                elapsed = round((time.time() - t0) * 1000)
                result = {"success": True, "uid": uid, "prompt": prompt, "model": model,
                          "response": response, "elapsed_ms": elapsed, "account": _active_idx + 1}
                if images: result["images"] = images
                self.send_json(result)
            except Exception as e:
                self.send_json({"success": False, "error": str(e), "elapsed_ms": round((time.time() - t0) * 1000)}, 500)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path); params = parse_qs(parsed.query); t0 = time.time()
        if parsed.path == "/api/gemini":
            prompt = params.get("prompt",[None])[0]
            uid = params.get("uid",["anonymous"])[0]
            model = params.get("model",["flash"])[0]
            image_url = params.get("image_url",[None])[0]
            if not prompt: self.send_json({"error":"Paramètre 'prompt' requis"},400); return
            try:
                response, images = call_gemini(prompt, model, image_url=image_url)
                elapsed = round((time.time()-t0)*1000)
                result = {"success":True,"uid":uid,"prompt":prompt,"model":model,
                          "response":response,"elapsed_ms":elapsed,"account":_active_idx+1}
                if images: result["images"] = images
                self.send_json(result)
            except Exception as e: self.send_json({"success":False,"error":str(e),"elapsed_ms":round((time.time()-t0)*1000)},500)
        elif parsed.path == "/api/health":
            self.send_json({"status":"ok","models":list(MODELS.keys()),
                "xsrf_cached":bool(_xsrf_token),"xsrf_age_s":int(time.time()-_xsrf_ts) if _xsrf_token else None,
                "total_accounts":len(_accounts),"active_account":_active_idx+1,"active_cookies":len(_cookies),
                "http_backend":"httpx" if HAS_HTTPX else "urllib","gemini_bl":CONFIG["gemini_bl"],
                "cookie_source":"env:GEMINI_COOKIES_B64" if os.getenv("GEMINI_COOKIES_B64") else "file",
                "push_id":_push_id})
        elif parsed.path == "/":
            self.send_json({"service":"Gemini API Proxy v6",
                "features":["Chat","Image Recognition","Image Editing","Multi-Account Rotation","Auto-XSRF"],
                "accounts":len(_accounts),"active_account":_active_idx+1,
                "usage":"GET /api/gemini?prompt=...&image_url=...&uid=ID"})
        else: self.send_json({"error":"Not found"},404)

def main():
    global _accounts, _cookies, _http_client
    import argparse
    parser = argparse.ArgumentParser(description="Gemini API Proxy v6 — Multi-Comptes")
    parser.add_argument("port",nargs="?",type=int,default=None)
    parser.add_argument("cookie_file",nargs="?",default="cookies.txt")
    parser.add_argument("--no-daemon",action="store_true")
    parser.add_argument("--bl",help="Build label override")
    args = parser.parse_args()
    if args.port: CONFIG["port"] = args.port
    if args.bl: CONFIG["gemini_bl"] = args.bl
    _accounts = load_cookies(args.cookie_file)
    _cookies = _accounts[0]
    print(f"✓ Compte actif: #1 ({len(_cookies)} cookies)")
    if HAS_HTTPX:
        limits = httpx.Limits(max_keepalive_connections=CONFIG["pool_connections"],
                              max_connections=CONFIG["pool_maxsize"],keepalive_expiry=30)
        try: _http_client = httpx.Client(http2=True,limits=limits,timeout=CONFIG["timeout"])
        except ImportError: _http_client = httpx.Client(http2=False,limits=limits,timeout=CONFIG["timeout"])
        print(f"✓ httpx (pool:{CONFIG['pool_connections']})")
    else: print("⚠ pip install httpx recommandé")
    print("⏳ Extraction XSRF...")
    token = get_xsrf()
    print(f"{'✓' if token else '⚠'} Token XSRF{' prêt' if token else ' — première requête'}")
    threading.Thread(target=background_xsrf_refresh,daemon=True).start()
    print("✓ BG refresh (30min)")
    class TS(ThreadingMixIn,HTTPServer): daemon_threads=True; allow_reuse_address=True
    server = TS((CONFIG["host"],CONFIG["port"]),GeminiHandler)
    print(f"\n🚀 Gemini API Proxy v6 → http://0.0.0.0:{CONFIG['port']}")
    print(f"   {len(_accounts)} compte(s) | Rotation auto sur quota image")
    try: server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Arrêt")
        if _http_client: _http_client.close()
        server.shutdown()

if __name__ == "__main__": main()
