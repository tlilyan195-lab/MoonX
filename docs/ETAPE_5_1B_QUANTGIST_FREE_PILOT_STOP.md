# ÉTAPE 5.1B′ — QuantGist free pilot STOP (clé indispensable)

**STOP.** Aucun paiement. Aucun TRAIN/VAL/OOS. Aucun SMC. Aucun OHLC 3 ans.  
Aucune clé demandée dans le chat.

## Verdicts demandés

| Champ | Valeur |
|---|---|
| **QUANTGIST_FREE_PILOT** | **FAIL** |
| **3Y_HISTORY** | **DOC_ONLY** |
| **LOOKAHEAD_SAFETY** | **UNCONFIRMED** |
| **STARTER_PRICE** | **$19/month** (source officielle `https://quantgist.com/pricing.md`) — *note: JSON-LD Terms page Offer Starter=`29` USD = conflit doc à vérifier au checkout* |
| **PAYMENT_RECOMMENDATION** | **NO** |

## Pourquoi FAIL

Tous les endpoints calendrier/events utiles renvoient **HTTP 401** sans `X-API-Key`.  
Seul `/v1/health` est public (200) — **insuffisant** pour schéma, profondeur, EUR/USD/GBP/JPY, ou look-ahead.

Preuves live (2026-08-06) :

| Endpoint | Status |
|---|---|
| `GET /v1/health` | 200 |
| `GET /v1/calendar?...` | **401** |
| `GET /v1/calendar/range?...` | **401** |
| `GET /v1/events?...&backtest_safe=true` | **401** |
| `QUANTGIST_API_KEY` in env | **absent** |

## Distinction API / DOC / NON VÉRIFIÉ

| Élément | Statut |
|---|---|
| Auth obligatoire pour calendar/events | **API confirmé** (401) |
| Health public | **API confirmé** (200) |
| Free = 100 req/jour, **1 an** d’historique | **DOC only** (`pricing.md`, `faq.md`, `docs` Plan Limits) |
| Starter = **3 ans**, 5 000 req/jour, ~1 min delay | **DOC only** (`pricing.md`, `docs`) — **pas** confirmé par API |
| Starter price **$19/mo** | **DOC officielle** `pricing.md` |
| Starter price $29 | **DOC conflict** (schema.org Offer dans HTML `/terms`) — non tranché |
| `impact` high/medium/low ; UTC ; currency/country | **DOC / OpenAPI** — non vu en réponse réelle |
| `backtest_safe` / `first_print_only` / v2 `as_of` | **DOC / OpenAPI** — **LOOKAHEAD UNCONFIRMED** sans clé |
| Plus ancienne date Free | **NON VÉRIFIÉ** (pas de réponse data) |
| Couverture high-impact EUR/USD/GBP/JPY | **NON VÉRIFIÉ** |
| Annulation | **DOC** : `POST /billing/portal` → Stripe Customer Portal « manage or cancel » (`https://quantgist.com/docs`) |

## Procédure — compte gratuit + clé (à faire de ton côté)

**Ne colle jamais la clé dans le chat.** Mets-la uniquement dans `.env` local (déjà gitignoré).

1. Ouvre **https://quantgist.com/signup** (Free, sans carte selon pricing/FAQ officiels).
2. Crée le compte (email + mot de passe).
3. Va sur **https://quantgist.com/dashboard/keys** (ou `/dashboard` → Keys).
4. Génère une clé **`qg_live_...`** — elle n’est affichée **qu’une fois** ; copie-la immédiatement.
5. Dans le repo MoonX, localement :

```bash
cp -n .env.example .env   # si besoin
# édite .env :
# QUANTGIST_API_KEY=qg_live_...
```

6. Réponds simplement ici : **« clé QuantGist dans .env »** (sans coller la valeur).  
   Je relancerai alors uniquement le **petit pilote Free** (échantillon calendrier, pas OHLC 3 ans).

Header attendu par l’API : `X-API-Key: <ta clé>`.

## Critères paiement (rappel) — non évaluables tant que le pilote Free n’a pas passé

Historique ≥3 ans confirmé API · EUR/USD/GBP/JPY · timestamp · impact · no look-ahead · stabilité · ≤$25/mo · annulation post-snapshot.  
→ **PAYMENT_RECOMMENDATION = NO** jusqu’à PASS du pilote Free + preuves API.
