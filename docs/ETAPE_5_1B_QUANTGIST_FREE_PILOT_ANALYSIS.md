# ÉTAPE 5.1B′ — QuantGist FREE pilot analysis

**Free ≠ 3y confirmation.**

| Champ | Valeur |
|---|---|
| **QUANTGIST_FREE_PILOT** | **FAIL** |
| **3Y_HISTORY** | **DOC_ONLY** |
| **LOOKAHEAD_SAFETY** | **FAIL** / non évaluable (pas de data) |
| **STARTER_PRICE** | **$19/month** (doc officielle `pricing.md`) ; conflit JSON-LD `$29` non tranché |
| **PAYMENT_RECOMMENDATION** | **NO** |

## Preuve API (sample poussé `5851d51`)

Fichier : `docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_SAMPLE.json`

| Section | HTTP | Détail |
|---|---|---|
| `calendar_range` | **401** | `API key not found or has been revoked` |
| `history_probe_2024` | **401** | idem |
| `events_backtest_safe` | **401** | idem |

**0 événement** reçu. Aucun champ schéma (timestamp, currency, country, event, impact, id, first_print, …) **ABSENT**.

### Clarification du message local précédent

`QUANTGIST_API_KEY present — wrote sample` signifiait seulement que la **variable `.env` était non vide**, pas que l’API avait accepté la clé. Le gate est corrigé pour renvoyer **FAIL** si HTTP 401/403.

## Ce qui est réellement validé

| Élément | Statut |
|---|---|
| Auth obligatoire | **API confirmé** |
| Clé actuelle utilisable | **API FAIL** (révoquée / introuvable) |
| Schéma Free réel | **NON VÉRIFIÉ** |
| EUR/USD/GBP/JPY high-impact | **NON VÉRIFIÉ** |
| Timezone / pagination / first_print | **NON VÉRIFIÉ** |
| Historique Free (plus ancienne date) | **NON VÉRIFIÉ** |
| Historique 3 ans | **DOC_ONLY** — Free ne confirme jamais 3 ans |
| Look-ahead / backtest_safe | **FAIL** sur ce sample (401) |

## Action requise (sans coller la clé dans le chat)

1. https://quantgist.com/dashboard/keys → **révoquer l’ancienne** si besoin → **générer une nouvelle** `qg_live_...`
2. Remplacer la valeur dans `.env` local uniquement (`QUANTGIST_API_KEY=...`)
3. Relancer :

```powershell
cd C:\Users\Lilyan\Documents\moonx
python scripts\quantgist_free_pilot_gate.py
# attendre: QUANTGIST_FREE_PILOT = PASS_SAMPLE_WRITTEN  (pas FAIL auth)
python scripts\analyze_quantgist_free_pilot.py
git add docs/ETAPE_5_1B_QUANTGIST_FREE_PILOT_SAMPLE.json
git status   # .env non staged
git commit -m "chore(data): refresh QuantGist Free pilot sample (auth OK)"
git push origin cursor/etape5-1b-qg-free-analyze-9238
```

4. Répondre **« sample poussé »** seulement si le gate affiche `PASS_SAMPLE_WRITTEN`.

Aucun paiement Starter tant que le Free pilot n’a pas de réponses **200 + events**.
