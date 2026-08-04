# MoonX — Trading Signal Bot (V1)

Bot de **signaux** Forex + BTC/ETH futures (SMC/ICT objectivé).  
**Il ne place aucun ordre** et ne doit jamais disposer de permissions BUY/SELL/ORDER/TRANSFER/WITHDRAWAL.

Hypothèse de recherche V1 — aucune garantie de rentabilité ni de win rate.

## État du projet

| Étape | Statut |
|-------|--------|
| 1–3 Stratégie + CDC | Validés |
| **4 Moteur de backtest** | **En cours (ce dépôt)** |
| 5–10 Tests live, paper, alertes, deploy | À venir |

## Architecture

```
DATA → INDICATORS → MARKET STRUCTURE → STRATEGY ENGINE
  → SIGNAL VALIDATION → RISK/RR → (later) ALERTS
```

Package principal : `trading_signal_bot/`  
Config gelable : `config/strategy_v1.yaml`  
Spécification : `docs/SPEC_V1.md`

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copier `.env.example` → `.env` (clés **read-only** uniquement).

## Backtest (ÉTAPE 4)

Données synthétiques intégrées pour valider le moteur (providers réels = D1/D2 ouverts) :

```bash
python main.py backtest --symbol EURUSD --bars 3000 --seed 42
python main.py backtest --symbol EURUSD --bars 3000 --splits
python main.py backtest --symbol BTCUSDT --bars 3000
```

## Tests

```bash
pytest -q
```

## Règles critiques V1

- Décision déterministe : `SIGNAL_LONG` | `SIGNAL_SHORT` | `NO_TRADE`
- Bougies **clôturées** uniquement (anti look-ahead)
- Sweep + POI (FVG/OB) + confirm 5M + RR min + sessions/vol
- TP liquidité strict (E2) — pas de TP synthétique
- Time-stop `max_hold_bars_5m` calibrable (E1)
- Variantes = runs séparés ; sélection TRAIN/VAL → gel → OOS untouched
- Alertes futures : A+/A seulement (B journal)

## Sécurité

- Secrets dans `.env` (gitignored)
- Interfaces `MarketDataProvider` / `EconomicCalendarProvider` sans endpoints trading
- `.gitignore` exclut `.env`, tokens, logs sensibles, caches

## Docker

```bash
docker build -t trading-signal-bot .
```

Le conteneur n’inclut pas de secrets ; les injecter via l’environnement au runtime.
