"""
Demostracion de punta a punta del cliente contra el servidor maqueta.

Levanta (o se conecta a) el mock server y ejecuta un flujo real de automatizacion:

    1. Autentica y lista las tablas del modelo.
    2. Inspecciona la estructura de una tabla.
    3. Extrae datos aplicando el filtro codificado propietario (con paginacion).
    4. Vacia una tabla y recarga datos con importaciones masivas asincronas,
       respetando el control de cola (max. N procesos simultaneos).
    5. Verifica el resultado volviendo a consultar.

Ejecucion (dos opciones):

    # A) Todo en un solo comando: la demo levanta el server embebido.
    python -m examples.demo

    # B) Server aparte, y la demo se conecta por HTTP:
    python -m mock_icm_server.server          # en una terminal
    python -m examples.demo --external        # en otra
"""

from __future__ import annotations

import os
import sys
import time
import threading

from icm_client import IcmApiClient

BASE_URL = os.environ.get("ICM_BASE_URL", "http://127.0.0.1:8000")
MODEL = os.environ.get("ICM_MODEL", "DemoModel")
TOKEN = os.environ.get("ICM_TOKEN", "demo-token")


def _maybe_start_embedded_server() -> None:
    """Arranca el mock server en un hilo daemon si no se pidio --external."""
    if "--external" in sys.argv:
        return
    from mock_icm_server.server import run

    port = int(BASE_URL.rsplit(":", 1)[-1])
    thread = threading.Thread(target=lambda: run(port=port), daemon=True)
    thread.start()
    time.sleep(0.6)  # dar tiempo a que el socket quede escuchando


def banner(text: str) -> None:
    print("\n" + "=" * 68)
    print(text)
    print("=" * 68)


def main() -> None:
    _maybe_start_embedded_server()
    client = IcmApiClient(base_url=BASE_URL, model=MODEL, auth_token=TOKEN)

    banner("1) Tablas disponibles en el modelo")
    for t in client.list_tables():
        print(f"   {t['name']:<16} {t['rowCount']:>5} filas  columnas={t['columns']}")

    banner("2) Estructura de 'dtIndicators'")
    info = client.get_table_info("dtIndicators")
    for col in info["columns"]:
        key = " (llave)" if col["isKey"] else ""
        print(f"   - {col['name']:<12} {col['type']}{key}")

    banner("3) Extraccion con filtro codificado + paginacion")
    filters = {"Period": ["202401"], "IndicatorID": ["VOL", "NPS"]}
    route = IcmApiClient.generate_filter_route(filters)
    print(f"   filtro -> {filters}")
    print(f"   URL    -> .../data?offset=0&limit=50{route}")
    rows = client.get_view_data("dtIndicators", filters=filters, limit_rows=50)
    print(f"   {len(rows)} filas recibidas. Muestra:")
    for r in rows[:3]:
        print(f"     {r}")

    banner("4) Recarga masiva con control de cola (max 1 proceso simultaneo)")
    # Extraemos el universo de una tabla, la vaciamos y la recargamos por lotes,
    # simulando el patron real de "vaciar + reimportar" sin subir archivos.
    crew = client.get_view_data("dtSalesCrew")
    print(f"   dtSalesCrew tenia {len(crew)} filas; vaciando...")
    client.empty_table("dtSalesCrew")
    print(f"   ahora tiene {len(client.get_view_data('dtSalesCrew'))} filas")

    # Partimos en 3 lotes para ejercitar el control de cola.
    batches = {
        "dtSalesCrew__lote1": crew[0:40],
        "dtSalesCrew__lote2": crew[40:80],
        "dtSalesCrew__lote3": crew[80:120],
    }

    def import_batch(batch_key: str) -> dict:
        return client.bulk_import("dtSalesCrew", batches[batch_key])

    print("   importando 3 lotes respetando la cola:")
    client.process_tables_with_queue_control(
        tables=list(batches.keys()),
        process_table_func=import_batch,
        max_live_activities=1,
        waiting_time=1,
    )

    banner("5) Verificacion final")
    restored = client.get_view_data("dtSalesCrew")
    print(f"   dtSalesCrew recargada: {len(restored)} filas")
    activos = [r for r in restored if r["Status"] == "ACTIVO"]
    print(f"   de las cuales {len(activos)} estan ACTIVO")
    print("\nDemo completada correctamente. ✔")


if __name__ == "__main__":
    main()
