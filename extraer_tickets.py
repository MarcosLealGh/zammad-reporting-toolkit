"""
extraer_tickets.py
==================
Extrae tickets de un grupo de Zammad vía API REST y calcula métricas
de servicio reales: tiempo de primera respuesta, tiempo de resolución,
tickets sin respuesta y resúmenes agregados por campo custom.

Diseñado para instancias de Zammad con campos custom por grupo
(ej. área técnica, sucursal).

Uso:
    export ZAMMAD_URL="https://tu-servidor-zammad"
    export ZAMMAD_TOKEN="tu_token_api"
    zammad-report --group 3

El token se genera en Zammad: Perfil → Token de acceso (permiso ticket.agent).
Las funciones de cálculo de métricas (calcular_tiempos, construir_resumen)
son puras y no dependen de configuración global — se pueden importar y testear.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

import requests

log = logging.getLogger("zammad_report")

# IDs de estado estándar de Zammad que nunca deben entrar a un reporte.
DEFAULT_EXCLUDED_STATES = (5, 7)  # 5=merged, 7=spam
DEFAULT_RESOLVED_STATE = "closed"
DEFAULT_CUSTOM_FIELDS = {
    "area_tecnica": "area_tecnica",
    "sucursal": "sucursal",
}


# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────

@dataclass
class Config:
    """Configuración de una ejecución. Se arma desde CLI + variables de entorno."""

    url: str
    token: str
    group_id: int
    excluded_states: tuple[int, ...] = DEFAULT_EXCLUDED_STATES
    resolved_state: str = DEFAULT_RESOLVED_STATE
    # Clave = nombre del campo en la API, valor = etiqueta para el CSV.
    custom_fields: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CUSTOM_FIELDS))
    # Verificación TLS activada por defecto (secure-by-default). Las instancias
    # internas con certificado autofirmado deben desactivarla explícitamente con
    # ZAMMAD_VERIFY_SSL=false, asumiendo el riesgo de forma consciente.
    verify_ssl: bool = True
    output_dir: str = "."

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Token token={self.token}"}


def load_config(argv: list[str] | None = None) -> Config:
    """Construye la configuración desde argumentos de CLI y variables de entorno."""
    parser = argparse.ArgumentParser(
        description="Extrae tickets de Zammad y calcula métricas de servicio."
    )
    parser.add_argument(
        "--group", type=int, required=True,
        help="ID del grupo a extraer (GET /api/v1/groups para listarlos).",
    )
    parser.add_argument(
        "--exclude-states", type=int, nargs="*", default=list(DEFAULT_EXCLUDED_STATES),
        help="IDs de estado a excluir (default: 5 7 = merged, spam).",
    )
    parser.add_argument(
        "--resolved-state", default=DEFAULT_RESOLVED_STATE,
        help='Nombre del estado que cuenta como resuelto (default: "closed").',
    )
    parser.add_argument(
        "--output-dir", default=".",
        help="Directorio donde escribir los CSV (default: directorio actual).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Log en nivel DEBUG.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    url = os.environ.get("ZAMMAD_URL")
    token = os.environ.get("ZAMMAD_TOKEN")
    if not url:
        parser.error("Variable de entorno ZAMMAD_URL no definida.")
    if not token:
        parser.error("Variable de entorno ZAMMAD_TOKEN no definida.")

    verify_ssl = os.environ.get("ZAMMAD_VERIFY_SSL", "true").lower() != "false"
    if not verify_ssl:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log.warning("Verificación TLS desactivada (ZAMMAD_VERIFY_SSL=false).")

    return Config(
        url=url.rstrip("/"),
        token=token,
        group_id=args.group,
        excluded_states=tuple(args.exclude_states),
        resolved_state=args.resolved_state,
        verify_ssl=verify_ssl,
        output_dir=args.output_dir,
    )


# ──────────────────────────────────────────────
# CLIENTE DE API
# ──────────────────────────────────────────────

class ZammadClient:
    """Envoltorio mínimo sobre la API REST de Zammad."""

    def __init__(self, config: Config):
        self.config = config

    def get(self, endpoint: str, params: dict | None = None):
        url = f"{self.config.url}/api/v1/{endpoint}"
        try:
            r = requests.get(
                url, headers=self.config.headers, params=params,
                verify=self.config.verify_ssl, timeout=30,
            )
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            log.error("Fallo GET %s: %s", endpoint, e)
            return None

    def get_all_tickets(self) -> list[dict]:
        tickets: list[dict] = []
        page = 1
        while True:
            log.debug("Descargando tickets — página %d", page)
            data = self.get("tickets", params={"page": page, "per_page": 50, "expand": "true"})
            if not data:
                break
            tickets.extend(
                t for t in data
                if t.get("state_id") not in self.config.excluded_states
                and t.get("group_id") == self.config.group_id
            )
            if len(data) < 50:
                break
            page += 1
        log.info("Tickets descargados: %d", len(tickets))
        return tickets

    def get_articles(self, ticket_id: int) -> list[dict]:
        data = self.get(f"ticket_articles/by_ticket/{ticket_id}")
        if not data:
            return []
        data.sort(key=lambda a: a.get("created_at", ""))
        return data


# ──────────────────────────────────────────────
# CÁLCULO DE MÉTRICAS DE TIEMPO (funciones puras)
# ──────────────────────────────────────────────

def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def calcular_tiempos(ticket: dict, articles: list[dict]) -> dict:
    """Calcula métricas de tiempo de un ticket a partir de sus artículos."""
    creado_en = parse_dt(ticket.get("created_at"))
    cerrado_en = parse_dt(ticket.get("close_at") or ticket.get("closed_at"))

    total_mensajes = len(articles)
    total_respuestas = 0
    primera_resp_dt = None

    for art in articles:
        if art.get("sender", "").lower() == "agent":
            total_respuestas += 1
            if primera_resp_dt is None:
                primera_resp_dt = parse_dt(art.get("created_at"))

    t_primera = ""
    if creado_en and primera_resp_dt:
        t_primera = round((primera_resp_dt - creado_en).total_seconds() / 60, 1)

    t_resolucion = ""
    if creado_en and cerrado_en:
        t_resolucion = round((cerrado_en - creado_en).total_seconds() / 60, 1)

    return {
        "total_mensajes": total_mensajes,
        "total_respuestas_agente": total_respuestas,
        "tiempo_primera_respuesta_min": t_primera,
        "tiempo_resolucion_min": t_resolucion,
    }


def construir_resumen(
    filas: list[dict], campo: str, resolved_state: str = DEFAULT_RESOLVED_STATE,
    label_vacio: str = "Sin clasificar",
) -> list[dict]:
    """Agrega métricas por el valor de un campo. Función pura, testeable."""
    grupos: dict[str, dict] = {}

    for f in filas:
        clave = f.get(campo) or label_vacio
        g = grupos.setdefault(clave, {
            "total": 0, "resueltos": 0, "pendientes": 0, "sin_resp": 0,
            "_sum_pr": 0, "_n_pr": 0, "_sum_re": 0, "_n_re": 0,
        })
        g["total"] += 1

        if f.get("estado") == resolved_state:
            g["resueltos"] += 1
        else:
            g["pendientes"] += 1

        if f.get("total_respuestas_agente", 0) == 0:
            g["sin_resp"] += 1

        tpr = f.get("tiempo_primera_respuesta_min")
        if tpr != "" and tpr is not None:
            g["_sum_pr"] += tpr
            g["_n_pr"] += 1

        tr = f.get("tiempo_resolucion_min")
        if tr != "" and tr is not None:
            g["_sum_re"] += tr
            g["_n_re"] += 1

    resumen = []
    for clave, g in sorted(grupos.items()):
        resumen.append({
            "categoria": clave,
            "total_tickets": g["total"],
            "tickets_resueltos": g["resueltos"],
            "tickets_pendientes": g["pendientes"],
            "tickets_sin_respuesta_agente": g["sin_resp"],
            "promedio_primera_respuesta_min": round(g["_sum_pr"] / g["_n_pr"], 1) if g["_n_pr"] else "",
            "promedio_tiempo_resolucion_min": round(g["_sum_re"] / g["_n_re"], 1) if g["_n_re"] else "",
        })
    return resumen


# ──────────────────────────────────────────────
# EXPORTACIÓN CSV
# ──────────────────────────────────────────────

COLS_RESUMEN = [
    "categoria", "total_tickets", "tickets_resueltos", "tickets_pendientes",
    "tickets_sin_respuesta_agente", "promedio_primera_respuesta_min",
    "promedio_tiempo_resolucion_min",
]


def construir_filas(client: ZammadClient, tickets: list[dict]) -> list[dict]:
    """Enriquece cada ticket con sus artículos y métricas de tiempo."""
    filas = []
    total = len(tickets)
    campos = client.config.custom_fields
    for i, t in enumerate(tickets, 1):
        ticket_id = t.get("id")
        log.debug("Procesando ticket %d/%d (ID %s)", i, total, ticket_id)
        metricas = calcular_tiempos(t, client.get_articles(ticket_id))
        filas.append({
            "id": ticket_id,
            "numero": t.get("number", ""),
            "titulo": t.get("title", ""),
            "estado": t.get("state", ""),
            "prioridad": t.get("priority", ""),
            "agente": t.get("owner", ""),
            "cliente": t.get("customer", ""),
            "creado_en": t.get("created_at", ""),
            "cerrado_en": t.get("close_at") or t.get("closed_at", ""),
            "actualizado_en": t.get("updated_at", ""),
            **{label: (t.get(campo) or "") for campo, label in campos.items()},
            **metricas,
        })
    return filas


def guardar_tickets(filas: list[dict], config: Config) -> str:
    columnas = [
        "id", "numero", "titulo", "estado", "prioridad",
        *config.custom_fields.values(),
        "agente", "cliente", "creado_en", "cerrado_en", "actualizado_en",
        "total_mensajes", "total_respuestas_agente",
        "tiempo_primera_respuesta_min", "tiempo_resolucion_min",
    ]
    ruta = os.path.join(config.output_dir, "tickets.csv")
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        writer.writerows(filas)
    log.info("Guardado: %s", ruta)
    return ruta


def guardar_resumen(resumen: list[dict], ruta: str, titulo: str) -> None:
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLS_RESUMEN)
        writer.writeheader()
        writer.writerows(resumen)
    log.info("Guardado: %s", ruta)

    # El resumen legible va a stdout: es la salida del comando, no diagnóstico.
    print(f"\n{titulo}")
    for r in resumen:
        pr = f"{r['promedio_primera_respuesta_min']} min" if r["promedio_primera_respuesta_min"] != "" else "N/D"
        re_ = f"{r['promedio_tiempo_resolucion_min']} min" if r["promedio_tiempo_resolucion_min"] != "" else "N/D"
        print(
            f"  {r['categoria']:<28} total={r['total_tickets']:>4} "
            f"resueltos={r['tickets_resueltos']:>4} sin_resp={r['tickets_sin_respuesta_agente']:>3} "
            f"1ª_resp={pr:>10} resolución={re_:>10}"
        )


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def run(config: Config) -> int:
    client = ZammadClient(config)

    log.info("Extractor de tickets Zammad — grupo %d", config.group_id)
    tickets = client.get_all_tickets()
    if not tickets:
        log.error("Sin tickets. Verifica token, URL y --group.")
        return 1

    filas = construir_filas(client, tickets)
    guardar_tickets(filas, config)

    for campo, label in config.custom_fields.items():
        resumen = construir_resumen(filas, label, config.resolved_state)
        ruta = os.path.join(config.output_dir, f"resumen_{label}.csv")
        guardar_resumen(resumen, ruta, f"RESUMEN — POR {label.upper()}")

    return 0


def main() -> None:
    config = load_config()
    sys.exit(run(config))


if __name__ == "__main__":
    main()
