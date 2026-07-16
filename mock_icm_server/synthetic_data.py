"""
Generador de datos *sinteticos* para el servidor maqueta.

Nada aqui proviene de un sistema real: los identificadores, rutas y montos se
generan de forma pseudoaleatoria y determinista (semilla fija) para que la demo
sea reproducible. El esquema imita, de forma generica, el tipo de tablas que se
manejan en un modelo de compensacion variable (payees, rutas, indicadores).
"""

from __future__ import annotations

import random
from typing import Any, Dict, List

SEED = 42

REGIONS = ["Norte", "Centro", "Sur", "Occidente", "Sureste"]
PROFILES = ["Prevendedor", "Repartidor", "Supervisor", "Lider de Ruta"]
ROUTE_TYPES = ["Preventa", "Autoventa", "Mayoreo"]
PERIODS = ["202401", "202402", "202403"]


def _payee_id(rng: random.Random) -> str:
    # ID numerico como texto, con ceros a la izquierda para probar su preservacion.
    return str(rng.randint(1_000_000, 3_999_999)).zfill(8)


def _route_id(rng: random.Random) -> str:
    return "R" + str(rng.randint(1, 999)).zfill(4)


def build_seed_tables() -> Dict[str, Dict[str, Any]]:
    """Devuelve el estado inicial de la 'base de datos' en memoria del servidor."""
    rng = random.Random(SEED)

    crew: List[Dict[str, Any]] = []
    for _ in range(120):
        crew.append(
            {
                "PayeeID": _payee_id(rng),
                "RouteID": _route_id(rng),
                "RouteType": rng.choice(ROUTE_TYPES),
                "Region": rng.choice(REGIONS),
                "Profile": rng.choice(PROFILES),
                "Status": rng.choice(["ACTIVO", "ACTIVO", "ACTIVO", "INACTIVO"]),
            }
        )

    indicators: List[Dict[str, Any]] = []
    for member in crew:
        for period in PERIODS:
            indicators.append(
                {
                    "PayeeID": member["PayeeID"],
                    "Period": period,
                    "IndicatorID": rng.choice(["VOL", "COB", "EJEC", "NPS"]),
                    "Target": round(rng.uniform(80, 120), 2),
                    "Actual": round(rng.uniform(60, 130), 2),
                    "Amount": round(rng.uniform(500, 15000), 2),
                }
            )

    return {
        "dtSalesCrew": {
            "columns": [
                {"name": "PayeeID", "type": "Text", "isKey": True},
                {"name": "RouteID", "type": "Text", "isKey": False},
                {"name": "RouteType", "type": "Text", "isKey": False},
                {"name": "Region", "type": "Text", "isKey": False},
                {"name": "Profile", "type": "Text", "isKey": False},
                {"name": "Status", "type": "Text", "isKey": False},
            ],
            "rows": crew,
        },
        "dtIndicators": {
            "columns": [
                {"name": "PayeeID", "type": "Text", "isKey": True},
                {"name": "Period", "type": "Text", "isKey": True},
                {"name": "IndicatorID", "type": "Text", "isKey": True},
                {"name": "Target", "type": "Numeric", "isKey": False},
                {"name": "Actual", "type": "Numeric", "isKey": False},
                {"name": "Amount", "type": "Numeric", "isKey": False},
            ],
            "rows": indicators,
        },
    }


if __name__ == "__main__":
    tables = build_seed_tables()
    for name, spec in tables.items():
        print(f"{name}: {len(spec['rows'])} filas, columnas={[c['name'] for c in spec['columns']]}")
