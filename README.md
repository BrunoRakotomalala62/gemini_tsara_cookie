# 🚀 Gemini Tsara Cookie

> Proxy API REST pour Google Gemini utilisant les cookies d'authentification web.
> **Zéro API key, zéro payant** — utilise ta session Google existante.

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## ✨ Fonctionnalités

- 🔄 **Auto-extraction XSRF** — plus besoin de rafraîchir manuellement le token `SNlM0e`
- ⚡ **HTTP/2 Keep-Alive** — pool de connexions réutilisées via `httpx`
- 🧵 **Background Refresh** — thread dédié qui rafraîchit le token XSRF toutes les 30 minutes
- 🎯 **4 modèles** — Flash, Thinking, Pro, Auto
- 📦 **Single-file** — un seul fichier Python, zéro dépendance obligatoire
- 🏠 **Serveur HTTP intégré** — pas besoin de Nginx, Flask, ou FastAPI
- 🔐 **Env vars ready** — cookies en `GEMINI_COOKIES_B64` (base64), compatible Railway/Render/Fly.io

## 📦 Installation

```bash
git clone https://github.com/BrunoRakotomalala62/gemini_tsara_cookie.git
cd gemini_tsara_cookie
pip install httpx  # optionnel mais recommandé
```

## 🔧 Configuration

### Méthode 1 : Variable d'environnement (recommandé pour déploiement)

```bash
# Génère le base64 de ton fichier cookies
export GEMINI_COOKIES_B64=$(base64 -w0 cookies.txt)

# Lance le serveur
python3 gemini-api.py
```

### Méthode 2 : Fichier local

```bash
# Récupère tes cookies depuis gemini.google.com (DevTools → Application → Cookies)
cp cookies.txt.example cookies.txt
# Édite cookies.txt avec tes vrais cookies
python3 gemini-api.py 8080 cookies.txt
```

## 🚀 Déploiement

### Railway

```bash
# Variables d'environnement :
GEMINI_COOKIES_B64=<ton-base64>
PORT=8080

# Start command:
pip install httpx && python3 gemini-api.py
```

### Render

```yaml
# render.yaml ou via le dashboard :
services:
  - type: web
    name: gemini-api
    env: python
    buildCommand: pip install httpx
    startCommand: python3 gemini-api.py
    envVars:
      - key: GEMINI_COOKIES_B64
        sync: false  # secret — à mettre dans le dashboard
```

### Fly.io

```bash
fly secrets set GEMINI_COOKIES_B64="$(base64 -w0 cookies.txt)"
fly deploy
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY gemini-api.py requirements.txt ./
RUN pip install httpx
EXPOSE 8080
CMD ["python3", "gemini-api.py"]
```

```bash
docker build -t gemini-api .
docker run -p 8080:8080 -e GEMINI_COOKIES_B64="$(base64 -w0 cookies.txt)" gemini-api
```

## 📡 Endpoints

### `GET /api/gemini`

| Paramètre | Requis | Défaut | Description |
|-----------|--------|--------|-------------|
| `prompt` | ✅ oui | — | Le texte à envoyer à Gemini |
| `uid` | non | `anonymous` | Identifiant utilisateur (logs) |
| `model` | non | `flash` | `flash` / `thinking` / `pro` / `auto` |

```bash
curl "http://localhost:8080/api/gemini?prompt=Bonjour&uid=123"
```

```json
{
  "success": true,
  "uid": "123",
  "prompt": "Bonjour",
  "model": "flash",
  "response": "Bonjour ! Comment puis-je vous aider ?",
  "elapsed_ms": 4123
}
```

### `GET /api/health`

```bash
curl "http://localhost:8080/api/health"
```

```json
{
  "status": "ok",
  "models": ["flash", "thinking", "pro", "auto"],
  "xsrf_cached": true,
  "cookies_loaded": 13,
  "http_backend": "httpx",
  "cookie_source": "env:GEMINI_COOKIES_B64"
}
```

## 🧠 Architecture

```
┌──────────────────┐    GEMINI_COOKIES_B64     ┌──────────────────┐
│  Env vars        │ ─────────────────────────→│  load_cookies()  │
│  (base64)        │                           │  → dict Python   │
└──────────────────┘                           └──────────────────┘
                                                        │
┌──────────────┐     auto-extraction      ┌──────────────────┐
│  Cache XSRF  │ ←──────────────────────── │ gemini.google.com │
│  (mémoire)   │     toutes les 30min      │   (page HTML)    │
└──────────────┘                           └──────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│  API v3 (port 8080)                                      │
│  GET /api/gemini?prompt=...&uid=...&model=flash           │
└──────────────────────────────────────────────────────────┘
```

## 🔒 Sécurité

- ⚠️ **Ne jamais committer `cookies.txt`** — les cookies donnent accès à ton compte Google
- Utilise **GEMINI_COOKIES_B64** en variable d'environnement (jamais dans le code)
- Les plateformes de déploiement (Railway, Render) ont des "secrets" chiffrés

## ⚡ Performance

| Métrique | Sans httpx | Avec httpx |
|----------|-----------|------------|
| Temps moyen | ~5s | ~4s |
| Connexions | 1 par requête | Pool de 10 réutilisées |
| Protocole | HTTP/1.1 | HTTP/2 |

> Le goulot principal (~2-3s) est le temps de génération de Gemini — incompressible.

## 📄 Licence

MIT

---

Fait avec ❤️ par [BrunoRakotomalala62](https://github.com/BrunoRakotomalala62)
