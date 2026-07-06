# 🔥 Meme Radar

Denný meme inšpiračný feed pre **Nie Som Idealista**. Každé ráno GitHub Actions
stiahne najpopulárnejšie memes zo sveta, ku každému vygeneruje krátky AI komentár
(Claude, vision) s nápadom na SK adaptáciu, publikuje mobilnú galériu na GitHub
Pages a pošle push notifikáciu cez ntfy.

## Ako to funguje

```
GitHub Actions cron (denne ~06:30 CET) → scripts/build.py
  1. Reddit top memes (meme-api.com — r/memes, r/dankmemes, r/me_irl, ...)
  2. Know Your Meme trending formáty
  3. Imgflip najpoužívanejšie templaty
  4. Dedup proti data/seen.json (30 dní histórie), NSFW filter
  5. AI komentár ku každému meme (claude-haiku-4-5, vision)
  6. HTML galéria → docs/index.html + docs/archive/YYYY-MM-DD.html
  7. Commit + GitHub Pages + ntfy push s linkom
```

## Setup

- **GitHub Pages:** main branch, `/docs` folder
- **Secrets:**
  - `ANTHROPIC_API_KEY` — Claude API kľúč (AI komentáre; bez neho beží galéria bez komentárov)
  - `NTFY_TOPIC` — ntfy.sh topic pre push notifikácie (telefón musí odoberať v ntfy appke)

## Lokálny beh

```
pip install -r requirements.txt
python scripts/build.py    # výstup v docs/index.html
```
