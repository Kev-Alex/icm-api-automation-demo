"""
Cliente reutilizable para automatizar una plataforma de Incentive Compensation
Management (ICM) a traves de su API REST interna.

Este cliente es una version *saneada y generica* de una herramienta que desarrolle
en un entorno profesional para automatizar cargas y extracciones masivas sobre una
plataforma SPM/ICM comercial. Toda logica especifica de cliente, credenciales y
datos reales fueron removidos; los endpoints aqui apuntan a un servidor "maqueta"
incluido en este mismo repositorio (`mock_icm_server`), de modo que el proyecto
corre de punta a punta sin depender de ningun sistema externo.

Tecnica que demuestra
---------------------
- Descubrimiento de endpoints "ocultos": inspeccionando el trafico de red de la
  aplicacion web (DevTools -> Network) se identifican las llamadas que la interfaz
  hace por detras, y se reconstruye la API para consumirla directamente.
- Reconstruccion de un esquema de codificacion de filtros propio de la plataforma
  (ver `generate_filter_route`), que la UI envia percent-encoded en la URL.
- Paginacion automatica, control de cola de procesos asincronos y carga masiva de
  datos generando sentencias `SELECT ... FROM (VALUES ...)`.

Dependencias: solo `requests` (libreria estandar para el resto).
"""

from __future__ import annotations

import os
import csv
import json
import time
import datetime
from typing import Any, Callable, Dict, List, Optional, Union

import requests


class IcmApiClient:
    """Cliente ligero para la API REST de una plataforma ICM (version demo)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        auth_token: str,
        verify: bool = True,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.auth_token = auth_token
        self.verify = verify
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # Infra
    # ------------------------------------------------------------------ #
    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Model": self.model,
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _get(self, path: str) -> requests.Response:
        r = requests.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            verify=self.verify,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r

    # ------------------------------------------------------------------ #
    # Lectura de metadatos
    # ------------------------------------------------------------------ #
    def list_tables(self) -> List[Dict[str, Any]]:
        """Lista las tablas disponibles en el modelo con su conteo de filas."""
        return self._get("/api/v1/tables").json()["tables"]

    def get_table_info(self, table: str) -> Dict[str, Any]:
        """Devuelve la estructura (columnas, tipos, llaves) de una tabla."""
        return self._get(f"/api/v1/customtables/{table}").json()["table"]

    def get_live_activities(self) -> List[Dict[str, Any]]:
        """Procesos asincronos en ejecucion (la 'cola' del servidor)."""
        return self._get("/api/v1/liveactivities").json()["activities"]

    # ------------------------------------------------------------------ #
    # Extraccion de datos (con paginacion automatica)
    # ------------------------------------------------------------------ #
    def get_view_data(
        self,
        table: str,
        filters: Optional[Dict[str, List[str]]] = None,
        limit_rows: int = 10_000,
        offset: int = 0,
        all_rows: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Descarga los datos de una tabla/vista como lista de diccionarios.

        Reproduce el endpoint que la UI usa para poblar sus grids, aplicando el
        mismo esquema de filtros codificados en la URL y paginando de forma
        transparente hasta traer todas las filas.
        """
        filters = filters or {}
        all_rows_data: List[List[Any]] = []
        columns: Optional[List[str]] = None

        while True:
            path = (
                f"/api/v1/customtables/{table}/inputforms/0/data"
                f"?offset={offset}&limit={limit_rows}"
            )
            path += self.generate_filter_route(filters)

            data = self._get(path).json()
            if columns is None:
                columns = [c["name"] for c in data["columnDefinitions"]]

            page = data.get("data") or []
            all_rows_data.extend(page)

            if not all_rows or len(page) < limit_rows:
                break
            offset += limit_rows

        return [dict(zip(columns, row)) for row in all_rows_data]

    # ------------------------------------------------------------------ #
    # Escritura / carga masiva
    # ------------------------------------------------------------------ #
    def empty_table(self, table: str) -> Dict[str, Any]:
        """Vacia por completo una tabla."""
        r = requests.delete(
            f"{self.base_url}/api/v1/customtables/{table}/data",
            headers=self._headers(),
            verify=self.verify,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def bulk_import(
        self,
        table: str,
        records: Union[str, List[Dict[str, Any]]],
        date_format: str = "DayFirst",
    ) -> Dict[str, Any]:
        """
        Carga masiva de datos a una tabla.

        `records` puede ser una ruta a un archivo delimitado o una lista de dicts.
        Internamente se construye una sentencia `SELECT ... FROM (VALUES ...)` (el
        mismo mecanismo que usa la plataforma para importar sin subir un archivo
        fisico) y se envia al endpoint asincrono de importacion.
        """
        rows = self._load_records(records)
        query = self.to_sql_values_query(rows)
        if query is None:
            return {"status": "skipped", "reason": "no rows"}

        payload = {
            "importType": "DBImport",
            "table": table,
            "query": query,
            "dateFormat": date_format,
            "model": self.model,
        }
        r = requests.post(
            f"{self.base_url}/api/v1/rpc/imports/runAdHoc",
            headers=self._headers(),
            data=json.dumps(payload),
            verify=self.verify,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def process_tables_with_queue_control(
        self,
        tables: List[str],
        process_table_func: Callable[..., Any],
        process_func_kwargs: Optional[dict] = None,
        max_live_activities: int = 1,
        waiting_time: int = 3,
    ) -> None:
        """
        Ejecuta `process_table_func(table, **kwargs)` sobre cada tabla respetando un
        limite de procesos asincronos simultaneos.

        La plataforma solo permite un numero acotado de importaciones concurrentes;
        este control de cola consulta las actividades en vivo y espera hasta que se
        libera un espacio antes de disparar la siguiente carga. Asi se automatizan
        decenas de tablas sin saturar el servidor ni suponer tiempos fijos.
        """
        process_func_kwargs = process_func_kwargs or {}
        started = datetime.datetime.now()

        for table in tables:
            done = False
            while not done:
                activities = self.get_live_activities()
                if len(activities) < max_live_activities:
                    try:
                        result = process_table_func(table, **process_func_kwargs)
                        status = (result or {}).get("status", "ok")
                        print(f"  -> {table}: {status}")
                    except Exception as exc:  # no abortar todo el lote por una tabla
                        print(f"  -> {table}: ERROR ({exc})")
                    done = True
                else:
                    print(
                        f"     cola llena ({len(activities)}/{max_live_activities}); "
                        f"esperando {waiting_time}s..."
                    )
                    time.sleep(waiting_time)

        print(f"Lote terminado en {datetime.datetime.now() - started}")

    # ------------------------------------------------------------------ #
    # Utilidades (estaticas, testeables sin servidor)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_records(records: Union[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        if isinstance(records, list):
            return records
        if isinstance(records, str) and os.path.isfile(records):
            delimiter = "\t" if records.lower().endswith(".tsv") else ","
            with open(records, newline="", encoding="utf-8") as fh:
                return list(csv.DictReader(fh, delimiter=delimiter))
        raise TypeError("records debe ser una lista de dicts o una ruta de archivo valida")

    @staticmethod
    def to_sql_values_query(
        rows: List[Dict[str, Any]], table_alias: str = "t"
    ) -> Optional[str]:
        """
        Convierte una lista de dicts en una consulta `SELECT * FROM (VALUES ...)`.

        Fuerza todo a texto para preservar ceros a la izquierda (comun en IDs de
        empleado/ruta) y escapa comillas simples para evitar romper la sentencia.
        """
        if not rows:
            return None

        columns = list(rows[0].keys())
        tuples = []
        for row in rows:
            cells = []
            for col in columns:
                val = row.get(col)
                if val is None or str(val).lower() in ("", "nan", "none"):
                    cells.append("NULL")
                else:
                    cells.append("'" + str(val).replace("'", "''") + "'")
            tuples.append("(" + ", ".join(cells) + ")")

        values = ",\n      ".join(tuples)
        cols = ", ".join(f'"{c}"' for c in columns)
        return f"SELECT *\nFROM (VALUES\n      {values}\n) AS {table_alias}({cols})"

    # --- Codificacion del filtro propietario de la plataforma --------- #
    @staticmethod
    def _flatten(items: List[Any]) -> List[Any]:
        out: List[Any] = []
        for it in items:
            out.extend(IcmApiClient._flatten(it)) if isinstance(it, list) else out.append(it)
        return out

    @staticmethod
    def _format_filter_values(values: List[str]) -> str:
        """Codifica la lista de valores de un campo al formato de la plataforma."""
        first = values[0]
        middle = [v + "%5C" for v in values[1:-1]]
        if len(values) > 1:
            first = "%3D" + first + "%5C"
            last = values[-1]
        else:
            first = "%3D" + first
            last = []
        return ",".join(IcmApiClient._flatten([first, middle, last]))

    @staticmethod
    def generate_filter_route(filters: Dict[str, List[str]]) -> str:
        """
        Reconstruye la cadena de filtro percent-encoded que la UI adjunta a la URL.

        Ejemplo:
            {"YearTxt": ["2024"], "Period": ["202401", "202402"]}
        produce:
            &filter=YearTxt%3D2024%3BPeriod%3D202401%5C,202402

        (`%3D` = '=', `%3B` = ';', `%5C` = '\\'). Este esquema no esta documentado;
        se dedujo observando las llamadas reales de la aplicacion.
        """
        if not filters:
            return ""
        parts = []
        for i, (field, values) in enumerate(filters.items()):
            sep = "" if i == 0 else "%3B"
            parts.append(sep + field + IcmApiClient._format_filter_values(values))
        return "&filter=" + "".join(parts)

    @staticmethod
    def build_range_filter(field: str, start: str, end: str) -> Dict[str, List[str]]:
        """
        Construye un filtro de rango (fechas/numeros) compatible con
        `generate_filter_route`. Codifica a `field%3D[start%5C,end]`.
        """
        return {field: [f"[{start}", f"{end}]"]}
