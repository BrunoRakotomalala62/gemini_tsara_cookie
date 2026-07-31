"""
Vercel Serverless Handler — Gemini API Proxy
=============================================
⚠️ Timeout 10s Vercel Hobby → image editing trop lent
   OK pour chat texte (flash ~3-5s)
"""
import json, uuid, hashlib, ssl, time, re, os, sys, base64
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

GEMINI_BL = os.getenv("GEMINI_BL", "boq_assistant-bard-web-server_20260716.08_p0")
TIMEOUT = 30

def load_cookies():
    b64 = os.getenv("GEMINI_COOKIES_B64", "")
    if not b64: return {}
    chunk = b64.split("|||")[0].strip()
    try: raw = base64.b64decode(chunk).decode("utf-8")
    except Exception: return {}
    cookies = {}
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("#") or not line: continue
        parts = line.split("\t")
        if len(parts) >= 7: cookies[parts[5]] = parts[6]
    return cookies

def cookie_header(cookies):
    return "; ".join(f"{k}={v}" for k, v in cookies.items())

def make_sapisidhash(sapisid):
    ts = int(time.time())
    return f"SAPISIDHASH {ts}_{hashlib.sha1(f'{ts} {sapisid} https://gemini.google.com'.encode()).hexdigest()}"

def extract_xsrf(cookies):
    import urllib.request as ur
    headers = {"User-Agent":"Mozilla/5.0","Accept":"text/html","Cookie":cookie_header(cookies)}
    try:
        ctx = ssl.create_default_context()
        req = ur.Request("https://gemini.google.com/app", headers=headers)
        resp = ur.urlopen(req, context=ctx, timeout=15)
        raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[XSRF] Echec: {e}")
        return "", "feeds/nrij2vo2gajxiu"
    m = re.search(r'"SNlM0e"\s*:\s*"([^"]+)"', raw)
    push_id = "feeds/nrij2vo2gajxiu"
    if m:
        pm = re.search(r'"KnDnFf"\s*:\s*"([^"]+)"', raw)
        if pm: push_id = pm.group(1)
        return m.group(1), push_id
    return "", push_id

def upload_image(image_url, cookies, push_id):
    import urllib.request as ur
    parsed = urlparse(image_url)
    filename = os.path.basename(unquote(parsed.path)) or "image.jpg"
    req = ur.Request(image_url, headers={"User-Agent":"Mozilla/5.0"})
    ctx = ssl.create_default_context()
    resp = ur.urlopen(req, context=ctx, timeout=30)
    image_data = resp.read()
    content_type = resp.headers.get("Content-Type", "image/jpeg")
    boundary = "----GemUpload" + str(int(time.time() * 10000))
    cp = f'Content-Disposition: form-data; name="file"; filename="{filename}"'
    header = f"--{boundary}\r\n{cp}\r\nContent-Type: {content_type}\r\n\r\n".encode()
    body = header + image_data + f"\r\n--{boundary}--\r\n".encode()
    req = ur.Request("https://content-push.googleapis.com/upload", data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Tenant-Id":"bard-storage","Origin":"https://gemini.google.com",
        "Referer":"https://gemini.google.com/","Push-ID":push_id,
        "Cookie":cookie_header(cookies),
    }, method="POST")
    resp = ur.urlopen(req, context=ctx, timeout=30)
    return resp.read().decode("utf-8").strip(), filename

MODELS = {"flash":(1,4),"thinking":(2,0),"pro":(3,4),"auto":(4,4)}
RE_WRB = re.compile(r'"wrb\.fr"')
RE_BARD_ERR = re.compile(r'BardErrorInfo\s*\[(\d+)\]')
RE_CODE_CLEAN = re.compile(r'```(?:python|javascript|text)\?code_.*?\n.*?```\n?', re.DOTALL)
RE_CARD = re.compile(r'https?://(?:www\.)?googleusercontent\.com/[^\s"\'<>]+')

def call_gemini(prompt, model, image_url, cookies, xsrf, push_id):
    import urllib.request as ur
    from urllib.parse import urlencode
    model_id, think_mode = MODELS.get(model, (1, 4))
    file_ref = None; filename = None
    if image_url:
        file_ref, filename = upload_image(image_url, cookies, push_id)
    inner = [None] * 80
    if file_ref: inner[0] = [prompt, 0, None, [[[file_ref], filename]], None, None, 0]
    else: inner[0] = [prompt, 0, None, None, None, None, 0]
    inner[1] = ["en"]
    inner[2] = ["", "", "", None, None, None, None, None, None, ""]
    inner[6]=[0]; inner[7]=1; inner[10]=1; inner[11]=0
    inner[17]=[[think_mode]]; inner[18]=0; inner[27]=1
    inner[30]=[4]; inner[41]=[2]; inner[53]=0
    inner[59]=str(uuid.uuid4()); inner[61]=[]; inner[68]=1; inner[79]=model_id
    outer = [None, json.dumps(inner)]
    params = {"f.req": json.dumps(outer)}
    if xsrf: params["at"] = xsrf
    body = urlencode(params)
    reqid = int(time.time() * 1000) % 1000000
    url = (f"https://gemini.google.com/_/BardChatUi/data/"
           f"assistant.lamda.BardFrontendService/StreamGenerate"
           f"?bl={GEMINI_BL}&hl=en&_reqid={reqid}&rt=c")
    sapisid = cookies.get("SAPISID", "")
    headers = {"Content-Type":"application/x-www-form-urlencoded",
               "Origin":"https://gemini.google.com",
               "Referer":"https://gemini.google.com/app","X-Same-Domain":"1",
               "User-Agent":"Mozilla/5.0","Cookie":cookie_header(cookies)}
    if sapisid: headers["Authorization"] = make_sapisidhash(sapisid)
    ctx = ssl.create_default_context()
    req = ur.Request(url, data=body.encode(), headers=headers, method="POST")
    resp = ur.urlopen(req, context=ctx, timeout=TIMEOUT)
    raw = resp.read().decode("utf-8", errors="replace")
    err = RE_BARD_ERR.search(raw)
    if err: raise RuntimeError(f"BardErrorInfo [{err.group(1)}]")
    texts = []
    for line in raw.split("\n"):
        if not RE_WRB.search(line) or len(line) < 200: continue
        try:
            arr = json.loads(line); inner_str = arr[0][2]
            if not inner_str or len(inner_str) < 50: continue
            inner_data = json.loads(inner_str)
            if isinstance(inner_data, list) and len(inner_data)>4 and inner_data[4]:
                for part in inner_data[4]:
                    if isinstance(part, list) and len(part)>1 and part[1] and isinstance(part[1], list):
                        for t in part[1]:
                            if isinstance(t, str) and t: texts.append(t)
        except (json.JSONDecodeError, IndexError, TypeError): pass
    text = RE_CODE_CLEAN.sub('', texts[-1].strip()) if texts else ""
    images = []; seen = set()
    for match in RE_CARD.finditer(raw):
        url = match.group(0).rstrip('\\')
        if url not in seen: seen.add(url); images.append(url)
    return text, images

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        t0 = time.time()
        if parsed.path in ("/api/health", "/"):
            self.send_json({"status":"ok","platform":"vercel","models":list(MODELS.keys())})
            return
        prompt = params.get("prompt", [None])[0]
        uid = params.get("uid", ["anonymous"])[0]
        model = params.get("model", ["flash"])[0]
        image_url = params.get("image_url", [None])[0]
        if not prompt:
            self.send_json({"error": "Parametre 'prompt' requis"}, 400)
            return
        try:
            cookies = load_cookies()
            if not cookies:
                self.send_json({"error": "Cookies non configures. Mets GEMINI_COOKIES_B64 en env var."}, 500)
                return
            xsrf, push_id = extract_xsrf(cookies)
            response, images = call_gemini(prompt, model, image_url, cookies, xsrf, push_id)
            elapsed = round((time.time() - t0) * 1000)
            result = {"success": True, "uid": uid, "prompt": prompt, "model": model,
                      "response": response, "elapsed_ms": elapsed}
            if images: result["images"] = images
            self.send_json(result)
        except Exception as e:
            self.send_json({"success": False, "error": str(e),
                           "elapsed_ms": round((time.time() - t0) * 1000)}, 500)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
