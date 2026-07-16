# ICM API Automation Demo — descubrimiento y consumo de una API interna

Automatización de una plataforma **ICM** (Incentive Compensation Management / gestión
de compensación variable) mediante **ingeniería inversa de su API REST interna**:
descubrir los endpoints que la aplicación web usa por detrás, reconstruir su esquema
de autenticación y de filtros, y consumirlos directamente para hacer en segundos lo
que en la interfaz tomaría horas de trabajo manual (cargar, vaciar y extraer tablas).

> **Nota sobre este repositorio.** Es una versión **demostrativa, genérica y sin datos
> reales**, inspirada en un trabajo que realicé en un entorno profesional. No contiene
> credenciales, información de clientes ni datos de ningún sistema productivo: incluye
> un **servidor "maqueta" (mock)** con datos **sintéticos** para que todo el flujo
> corra de punta a punta en tu máquina, sin depender de ninguna plataforma externa.

---

## ¿Qué técnica demuestra?

Es la misma metodología que aplico para hacer scraping/automatización sobre
aplicaciones web con "APIs ocultas":

1. **Inspección del tráfico de red** (DevTools → Network) mientras se opera la app,
   para identificar las llamadas XHR/fetch que dispara cada acción de la interfaz.
2. **Reconstrucción de la API**: método HTTP, ruta, headers de autenticación
   (`Authorization: Bearer …`, header de modelo), forma del payload y de la respuesta.
3. **Decodificación de un esquema de filtros propietario** que la UI envía
   *percent-encoded* en la URL — no está documentado; se dedujo observando llamadas
   reales (ver [`generate_filter_route`](icm_client/client.py)).
4. **Automatización robusta**: paginación automática, **control de cola** de procesos
   asíncronos (la plataforma limita las importaciones concurrentes) y **carga masiva**
   generando sentencias `SELECT * FROM (VALUES …)` para importar sin subir archivos.

La misma caja de herramientas aplica a scraping web clásico; este proyecto se enfoca
en el ángulo de **API interna/oculta**.

---

## Arranque rápido (30 segundos)

Requisitos: **Python 3.10+** y `requests` (única dependencia externa).

```bash
git clone https://github.com/<tu-usuario>/icm-api-automation-demo.git
cd icm-api-automation-demo
pip install -r requirements.txt

# Corre la demo completa (levanta el mock server embebido automáticamente):
python -m examples.demo
```

> Si el puerto 8000 está reservado en tu sistema (común en Windows), usa otro:
> `ICM_BASE_URL=http://127.0.0.1:8765 python -m examples.demo`

### Salida esperada (resumida)

```
1) Tablas disponibles en el modelo
   dtSalesCrew        120 filas  columnas=['PayeeID', 'RouteID', ...]
   dtIndicators       360 filas  columnas=['PayeeID', 'Period', ...]

3) Extracción con filtro codificado + paginación
   filtro -> {'Period': ['202401'], 'IndicatorID': ['VOL', 'NPS']}
   URL    -> .../data?offset=0&limit=50&filter=Period%3D202401%3BIndicatorID%3DVOL%5C,NPS
   66 filas recibidas.

4) Recarga masiva con control de cola (max 1 proceso simultáneo)
   dtSalesCrew tenía 120 filas; vaciando... -> 0 filas
   importando 3 lotes respetando la cola:
     -> dtSalesCrew__lote1: accepted
        cola llena (1/1); esperando 1s...
     -> dtSalesCrew__lote2: accepted
     -> dtSalesCrew__lote3: accepted

5) Verificación final
   dtSalesCrew recargada: 120 filas
Demo completada correctamente. ✔
```

---

## Ejecutar el servidor por separado

```bash
# Terminal 1 — levanta el mock server:
python -m mock_icm_server.server            # http://127.0.0.1:8000

# Terminal 2 — corre la demo apuntando a ese server:
python -m examples.demo --external
```

Puedes explorar la API a mano (recuerda el token):

```bash
curl -H "Authorization: Bearer demo-token" http://127.0.0.1:8000/api/v1/tables
```

---

## Pruebas

Los tests validan la codificación de filtros y la generación de `VALUES` sin
necesidad de servidor:

```bash
python -m unittest discover -s tests -v
# o, si prefieres pytest:  pytest -q
```

---

## Estructura del proyecto

```
icm-api-automation-demo/
├── icm_client/
│   └── client.py          # Cliente reutilizable (la pieza principal)
├── mock_icm_server/
│   ├── server.py          # API maqueta (solo stdlib: http.server)
│   └── synthetic_data.py  # Generador de datos sintéticos (semilla fija)
├── examples/
│   └── demo.py            # Flujo de automatización de punta a punta
├── tests/
│   └── test_client.py     # Pruebas unitarias (unittest)
├── requirements.txt
├── .env.example
└── README.md
```

## Endpoints reproducidos por el mock

| Método   | Ruta                                                    | Rol                                   |
|----------|---------------------------------------------------------|---------------------------------------|
| `GET`    | `/api/v1/tables`                                        | Listar tablas y conteos               |
| `GET`    | `/api/v1/customtables/{t}`                              | Estructura (columnas, llaves)         |
| `GET`    | `/api/v1/customtables/{t}/inputforms/0/data`            | Datos con filtro + paginación         |
| `DELETE` | `/api/v1/customtables/{t}/data`                         | Vaciar tabla                          |
| `POST`   | `/api/v1/rpc/imports/runAdHoc`                          | Importación masiva asíncrona          |
| `GET`    | `/api/v1/liveactivities`                                | Cola de procesos en ejecución         |

Todos exigen `Authorization: Bearer <token>`.

---

## Stack y conceptos

**Python** · **requests** · **REST API** · reverse engineering de APIs · paginación ·
control de concurrencia/cola · autenticación por token · `http.server` (stdlib) ·
datos sintéticos · pruebas unitarias.

## Aviso

Repositorio con fines **educativos y de portafolio**. Automatiza únicamente sistemas
sobre los que tengas autorización explícita y respeta los términos de servicio y las
políticas de cada plataforma.

## Licencia

[MIT](LICENSE).
