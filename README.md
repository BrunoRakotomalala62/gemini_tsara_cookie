# 🚀 Gemini Tsara Cookie

> Proxy API REST pour Google Gemini utilisant les cookies web.
> **Zéro API key, zéro payant** — session Google existante.

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## ✨ Features

- 🔄 **Auto-XSRF** — token SNlM0e rafraîchi automatiquement
- ⚡ **HTTP/2 Keep-Alive** — via `httpx`
- 🧵 **Background Refresh** — XSRF toutes les 30min
- 🎯 **4 modèles** — Flash, Thinking, Pro, Auto
- 📦 **Single-file** — 0 dépendance obligatoire
- 🔐 **Env vars** — `GEMINI_COOKIES_B64` en base64

## 📦 Quick Start

```bash
git clone https://github.com/BrunoRakotomalala62/gemini_tsara_cookie.git
cd gemini_tsara_cookie
pip install httpx
export GEMINI_COOKIES_B64=$(base64 -w0 cookies.txt)
python3 gemini-api.py
```

## 🚀 Déploiement

### 🆓 Render (GRATUIT)

1. Fork ce repo → [render.com](https://render.com) → New Web Service
2. Env var : `GEMINI_COOKIES_B64=<ton-base64>`
3. Build : `pip install httpx` / Start : `python3 gemini-api.py`
4. Plan : **Free** (⚠️ sleep après 15min d'inactivité)

### 🆓 Koyeb (GRATUIT, toujours actif)

1. [koyeb.com](https://koyeb.com) → Create App → ce repo
2. Env var : `GEMINI_COOKIES_B64=<ton-base64>`
3. Instance : **Nano (gratuit)**

### 💰 Fly.io (~$2/mois)

```bash
fly launch
fly secrets set GEMINI_COOKIES_B64="$(base64 -w0 cookies.txt)"
fly deploy
```

## 📡 API

```bash
# Chat
curl "http://localhost:8080/api/gemini?prompt=Bonjour&uid=123"

# Health
curl "http://localhost:8080/api/health"
```

| Param | Req | Défaut | Description |
|-------|-----|--------|-------------|
| `prompt` | ✅ | — | Texte à envoyer |
| `uid` | non | anonymous | ID utilisateur |
| `model` | non | flash | flash/thinking/pro/auto |

## 🔒 Sécurité

⚠️ **Ne jamais commit `cookies.txt`** — les cookies = ton compte Google.
Utilise `GEMINI_COOKIES_B64` en variable d'environnement.

## 📄 Licence

MIT
