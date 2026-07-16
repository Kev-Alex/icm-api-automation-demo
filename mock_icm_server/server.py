"""
Servidor "maqueta" (mock) de una plataforma ICM, construido solo con la libreria
estandar de Python (`http.server`). No requiere instalar nada.

Reproduce, de forma minima pero fiel, los endpoints internos que una aplicacion
de compensacion variable expone y que el cliente automatiza:

    GET    /api/v1/tables
    GET    /api/v1/customtables/{table}
    GET    /api/v1/customtables/{table}/inputforms/0/data?offset&limit&filter=
    DELETE /api/v1/customtables/{table}/data
    POST   /api/v1/rpc/imports/runAdHoc      (importacion asincrona)
    GET    /api/v1/liveactivities            (cola de procesos)

Autenticacion: exige el header  Authorization: Bearer <token>.
Toda la data vive en memoria y es sintetica (ver synthetic_data.py).

Uso:
    python -m mock_icm_server.server            # escucha en 127.0.0.1:8000
    ICM_TOKEN=xyz ICM_PORT=9000 python -m mock_icm_server.server
"""

from __future__ import annotations

import os
import re
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

try:  # permite ejecutar como modulo o como script suelto
    from .synthetic_data import build_seed_tables
except ImportError:  # pragma: no cover
    from synthetic_data import build_seed_tables


DEFAULT_TOKEN = os.environ.get("ICM_TOKEN", "demo-token")
IMPORT_DURATION_SEC = float(os.environ.get("ICM_IMPORT_SECONDS", "2"))

# Estado global en memoria, protegido por un lock (el server es multihilo).
_LOCK = threading.Lock()
TABLES = build_seed_tables()
ACTIVITIES: list[dict] = []  # cada una: {"id", "name", "endsAt"}
_ACTIVITY_SEQ = 0


# --------------------------------------------------------------------------- #
# Decodificacion del filtro propietario  (inverso de generate_filter_route)
# --------------------------------------------------------------------------- #
def decode_filter(raw: str) -> dict[str, list[str]]:
    """Convierte 'YearTxt%3D2024%3BPeriod%3D202401%5C,202402' -> dict."""
    if not raw:
        return {}
    # Doble unquote para tolerar doble codificacion introducida por el cliente HTTP.
    s = unquote(unquote(raw))
    out: dict[str, list[str]] = {}
    for field_chunk in s.split(";"):
        if "=" not in field_chunk:
            continue
        field, values_part = field_chunk.split("=", 1)
        values = [v for v in values_part.replace("\\", "").split(",") if v != ""]
        out[field.strip()] = values
    return out


def _row_matches(row: dict, filters: dict[str, list[str]]) -> bool:
    for field, values in filters.items():
        if field not in row:
            return False
        cell = str(row[field])
        # Rango: valores tipo ["[start", "end]"]
        if len(values) == 2 and values[0].startswith("[") and values[1].endswith("]"):
            lo = values[0].lstrip("[")
            hi = values[1].rstrip("]")
            if not (_le(lo, cell) and _le(cell, hi)):
                return False
        elif cell not in {str(v) for v in values}:
            return False
    return True


def _le(a: str, b: str) -> bool:
    """<= numerico si ambos parsean; si no, lexicografico."""
    try:
        return float(a) <= float(b)
    except ValueError:
        return a <= b


# --------------------------------------------------------------------------- #
# Parser tolerante de la sentencia SELECT * FROM (VALUES ...) AS t("c1","c2")
# --------------------------------------------------------------------------- #
def parse_values_query(query: str) -> tuple[list[str], list[list[str]]]:
    cols_match = re.search(r"AS\s+\w+\s*\((.*)\)\s*$", query, re.S)
    columns = []
    if cols_match:
        columns = [c.strip().strip('"') for c in cols_match.group(1).split(",")]

    rows: list[list[str]] = []
    values_block = re.search(r"VALUES(.*?)\)\s*AS", query, re.S)
    if values_block:
        for tup in re.findall(r"\(([^()]*)\)", values_block.group(1)):
            cells = [c.strip() for c in re.split(r",(?=(?:[^']*'[^']*')*[^']*$)", tup)]
            cells = [None if c == "NULL" else c.strip("'").replace("''", "'") for c in cells]
            rows.append(cells)
    return columns, rows


# --------------------------------------------------------------------------- #
# Actividades asincronas (la "cola")
# --------------------------------------------------------------------------- #
def _prune_activities() -> None:
    now = time.time()
    ACTIVITIES[:] = [a for a in ACTIVITIES if a["endsAt"] > now]


def _enqueue(name: str) -> int:
    global _ACTIVITY_SEQ
    _ACTIVITY_SEQ += 1
    ACTIVITIES.append({"id": _ACTIVITY_SEQ, "name": name, "endsAt": time.time() + IMPORT_DURATION_SEC})
    return _ACTIVITY_SEQ


# --------------------------------------------------------------------------- #
# Handler HTTP
# --------------------------------------------------------------------------- #
class IcmMockHandler(BaseHTTPRequestHandler):
    server_version = "MockICM/1.0"

    # -- helpers -- #
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {DEFAULT_TOKEN}":
            self._send(401, {"error": "unauthorized", "detail": "Bearer token invalido o ausente"})
            return False
        return True

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def log_message(self, fmt, *args):  # silenciar el log ruidoso por defecto
        return

    # -- rutas -- #
    def do_GET(self):
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query, keep_blank_values=True)

        if path == "/api/v1/tables":
            with _LOCK:
                tables = [
                    {"name": name, "rowCount": len(spec["rows"]),
                     "columns": [c["name"] for c in spec["columns"]]}
                    for name, spec in TABLES.items()
                ]
            return self._send(200, {"tables": tables})

        if path == "/api/v1/liveactivities":
            with _LOCK:
                _prune_activities()
                return self._send(200, {"activities": list(ACTIVITIES)})

        m = re.match(r"^/api/v1/customtables/([^/]+)$", path)
        if m:
            table = m.group(1)
            with _LOCK:
                if table not in TABLES:
                    return self._send(404, {"error": "table not found", "table": table})
                spec = TABLES[table]
                return self._send(200, {"table": {
                    "name": table,
                    "columns": spec["columns"],
                    "rowCount": len(spec["rows"]),
                }})

        m = re.match(r"^/api/v1/customtables/([^/]+)/inputforms/0/data$", path)
        if m:
            table = m.group(1)
            return self._serve_data(table, qs)

        return self._send(404, {"error": "not found", "path": path})

    def do_DELETE(self):
        if not self._authorized():
            return
        m = re.match(r"^/api/v1/customtables/([^/]+)/data$", urlparse(self.path).path)
        if m:
            table = m.group(1)
            with _LOCK:
                if table not in TABLES:
                    return self._send(404, {"error": "table not found", "table": table})
                deleted = len(TABLES[table]["rows"])
                TABLES[table]["rows"] = []
            return self._send(200, {"status": "emptied", "table": table, "rowsDeleted": deleted})
        return self._send(404, {"error": "not found", "path": self.path})

    def do_POST(self):
        if not self._authorized():
            return
        path = urlparse(self.path).path

        if path == "/api/v1/rpc/imports/runAdHoc":
            body = self._read_json()
            table = body.get("table")
            query = body.get("query", "")
            with _LOCK:
                if table not in TABLES:
                    return self._send(404, {"error": "table not found", "table": table})
                columns, rows = parse_values_query(query)
                col_names = [c["name"] for c in TABLES[table]["columns"]]
                imported = 0
                for row in rows:
                    record = dict(zip(columns or col_names, row))
                    TABLES[table]["rows"].append({c: record.get(c) for c in col_names})
                    imported += 1
                activity_id = _enqueue(f"import:{table}")
            return self._send(202, {
                "status": "accepted",
                "table": table,
                "rowsImported": imported,
                "activityId": activity_id,
            })

        return self._send(404, {"error": "not found", "path": path})

    # -- data endpoint con filtro + paginacion -- #
    def _serve_data(self, table: str, qs: dict) -> None:
        with _LOCK:
            if table not in TABLES:
                return self._send(404, {"error": "table not found", "table": table})
            spec = TABLES[table]
            columns = [c["name"] for c in spec["columns"]]
            rows = spec["rows"]

        raw_filter = qs.get("filter", [""])[0]
        filters = decode_filter(raw_filter)
        matched = [r for r in rows if _row_matches(r, filters)]

        offset = int(qs.get("offset", ["0"])[0])
        limit = int(qs.get("limit", ["10000"])[0])
        page = matched[offset:offset + limit]

        return self._send(200, {
            "columnDefinitions": [{"name": c} for c in columns],
            "data": [[r.get(c) for c in columns] for r in page],
            "total": len(matched),
            "offset": offset,
            "limit": limit,
        })


def run(host: str = "127.0.0.1", port: int | None = None) -> None:
    port = port or int(os.environ.get("ICM_PORT", "8000"))
    httpd = ThreadingHTTPServer((host, port), IcmMockHandler)
    print(f"Mock ICM server escuchando en http://{host}:{port}  (token='{DEFAULT_TOKEN}')")
    print("Ctrl+C para detener.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo servidor...")
        httpd.shutdown()


if __name__ == "__main__":
    run()
