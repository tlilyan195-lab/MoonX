"""Configuration loading and hashing."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "strategy_v1.yaml"


def load_yaml_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config at {cfg_path} must be a mapping")
    return data


def config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class StrategyConfig:
    """Immutable view over strategy knobs used by the engine."""

    raw: dict[str, Any]
    hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "hash", config_hash(self.raw))

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> StrategyConfig:
        return cls(raw=load_yaml_config(path))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyConfig:
        return cls(raw=deepcopy(data))

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def with_overrides(self, overrides: dict[str, Any]) -> StrategyConfig:
        merged = deepcopy(self.raw)

        def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
            for k, v in src.items():
                if isinstance(v, dict) and isinstance(dst.get(k), dict):
                    _merge(dst[k], v)
                else:
                    dst[k] = v

        _merge(merged, overrides)
        return StrategyConfig.from_dict(merged)

    @property
    def fvg_mitigation_mode(self) -> str:
        return str(self.get("fvg", "mitigation_mode", default="close_through"))

    @property
    def poi_selection_mode(self) -> str:
        return str(
            self.get(
                "meta",
                "poi_selection_mode",
                default=self.get("poi_selection_mode", default="best_rr_then_recency"),
            )
        )

    @property
    def require_overlap_for_aplus(self) -> bool:
        return bool(self.get("meta", "require_overlap_for_aplus", default=True))

    @property
    def rr_min(self) -> float:
        return float(self.get("risk", "rr_min", default=2.0))

    @property
    def atr_period(self) -> int:
        return int(self.get("atr", "period", default=14))
