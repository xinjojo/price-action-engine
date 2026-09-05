"""引擎配置加载。

用标准库 tomllib 读 configs/engine.toml —— 不为读配置引入第三方依赖。

调用方：features/*、market/china_a/*、data/bridge.py。
删掉它会损失什么：参数会散落到各个模块里，回测无法复现（配置哈希无法计算）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "engine.toml"


@dataclass(frozen=True)
class FeatureConfig:
    atr_period: int
    cap_atr: float


@dataclass(frozen=True)
class MarketConfig:
    tick: Decimal
    limit_tolerance: Decimal


@dataclass(frozen=True)
class SetupConfig:
    max_lookback_bars: int


@dataclass(frozen=True)
class DataConfig:
    schema_version: str
    tushare_no_limit_up: Decimal
    tushare_no_limit_down: Decimal


@dataclass(frozen=True)
class EngineConfig:
    features: FeatureConfig
    market: MarketConfig
    setup: SetupConfig
    data: DataConfig


def load_config(path: Path | None = None) -> EngineConfig:
    path = path or _CONFIG_PATH
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    feat = raw["features"]
    mkt = raw["market"]
    setup = raw["setup"]
    data = raw["data"]

    return EngineConfig(
        features=FeatureConfig(
            atr_period=int(feat["atr_period"]),
            cap_atr=float(feat["cap_atr"]),
        ),
        market=MarketConfig(
            tick=Decimal(mkt["tick"]),
            limit_tolerance=Decimal(mkt["limit_tolerance"]),
        ),
        setup=SetupConfig(max_lookback_bars=int(setup["max_lookback_bars"])),
        data=DataConfig(
            schema_version=str(data["schema_version"]),
            tushare_no_limit_up=Decimal(data["tushare_no_limit_up"]),
            tushare_no_limit_down=Decimal(data["tushare_no_limit_down"]),
        ),
    )


CONFIG = load_config()
