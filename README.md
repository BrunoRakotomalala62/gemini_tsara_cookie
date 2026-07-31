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

## 📦 Installation

```bash
# Optionnel mais recommandé pour les perfs (HTTP/2)
pip install httpx

# Sinon, l'API fonctionne avec urllib (stdlib Python)
```

## 🔧 Configuration

### 1. Récupérer tes cookies Google

1. Va sur [gemini.google.com](https://gemini.google.com) et connecte-toi
2. Ouvre DevTools (F12) → Application → Cookies → `https://gemini.google.com`
3. Copie les cookies suivants : `SID`, `__Secure-1PSID`, `HSID`, `SSID`, `APISID`, `SAPISID`, `COMPASS`
4. Utilise une extension "Export Cookies" ou copie-les manuellement

### 2. Créer le fichier cookies

```bash
cp cookies.txt.example cookies.txt
# Édite cookies.txt avec tes vrais cookies
```

Format (Netscape) :
```
.google.com    TRUE    /    FALSE    9999999999    SID    g.a000BAl...
.google.com    TRUE    /    TRUE     9999999999    __Secure-1PSID    g.a000BAl...
```

## 🚀 Lancement

```bash
python3 gemini-api.py [port] [cookies.txt]

# Exemples :
python3 gemini-api.py                          # port 8080, cookies.txt
python3 gemini-api.py 3000 mon-cookies.txt     # port 3000, fichier perso
```

## 📡 Endpoints

### `GET /api/gemini`

Envoie un prompt à Gemini et retourne la réponse.

| Paramètre | Requis | Défaut | Description |
|-----------|--------|--------|-------------|
| `prompt` | ✅ oui | — | Le texte à envoyer à Gemini |
| `uid` | non | `anonymous` | Identifiant utilisateur (logs) |
| `model` | non | `flash` | `flash` / `thinking` / `pro` / `auto` |

```bash
# Test simple
curl "http://localhost:8080/api/gemini?prompt=Bonjour&uid=123"

# Avec modèle thinking
curl "http://localhost:8080/api/gemini?prompt=Explique%20la%20relativité&model=thinking"
```

**Réponse :**
```json
{
  "success": true,
  "uid": "123",
  "prompt": "Bonjour",
  "model": "flash",
  "response": "Bonjour ! Comment puis-je vous aider aujourd'hui ?",
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
  "xsrf_age_s": 42,
  "cookies_loaded": 13,
  "http_backend": "httpx"
}
```

## 🧠 Architecture

```
┌──────────────┐     auto-extraction      ┌──────────────────┐
│  Cache XSRF  │ ←──────────────────────── │ gemini.google.com │
│  (mémoire)   │     toutes les 30min      │   (page HTML)    │
└──────────────┘                           └──────────────────┘
       │
       │ fournit token SNlM0e
       ▼
┌──────────────────────────────────────────────────────────┐
│  API v2 (port 8080)                                      │
│  GET /api/gemini?prompt=...&uid=...&model=flash           │
│  → HTTP/2 keep-alive vers Gemini StreamGenerate           │
│  → Parsing optimisé de la réponse                         │
│  → ~4s par requête (dont ~2-3s génération Gemini)         │
└──────────────────────────────────────────────────────────┘
```

## 🔒 Sécurité

- ⚠️ **Ne jamais committer `cookies.txt`** — les cookies donnent accès à ton compte Google !
- Le `.gitignore` exclut automatiquement les fichiers de cookies
- Les cookies SID/__Secure-1PSID sont des tokens de session — traite-les comme des mots de passe
- Utilise un compte Google dédié si tu exposes cette API sur un réseau

## 🛠 Maintenance

### Token XSRF

Le token `SNlM0e` expire toutes les quelques heures. **L'API l'extrait automatiquement** — aucun refresh manuel nécessaire.

### Session Google

Les cookies SID expirent en théorie en 2027-2028, mais Google peut révoquer la session. Pour éviter ça, une tâche planifiée (cron) peut visiter Gemini périodiquement :

```bash
# Toutes les 4h, touche la session
0 */4 * * * curl -s -b cookies.txt https://gemini.google.com/app > /dev/null
```

### Build label (`gemini_bl`)

Si l'API retourne des 400, le build label a peut-être changé. Mets-le à jour :

```bash
python3 gemini-api.py --bl "boq_assistant-bard-web-server_YYYYMMDD.xx_p0"
```

## 📋 Dépendances

| Package | Requis | Rôle |
|---------|--------|------|
| `httpx` | Optionnel | HTTP/2 + keep-alive (recommandé) |
| stdlib Python | ✅ | urllib, json, hashlib, threading |

## ⚡ Performance

| Métrique | Sans httpx | Avec httpx |
|----------|-----------|------------|
| Temps moyen | ~5s | ~4s |
| Connexions | 1 par requête | Pool de 10 réutilisées |
| Protocole | HTTP/1.1 | HTTP/2 |
| Parsing | Re-compilé | Pré-compilé |

> Le goulot principal (~2-3s) est le temps de génération de Gemini — incompressible.

## 📄 Licence

MIT — utilise, modifie, partage librement.

---

Fait avec ❤️ par [BrunoRakotomalala62](https://github.com/BrunoRakotomalala62)
