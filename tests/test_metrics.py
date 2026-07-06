"""Tests de las funciones puras de métricas — sin red, sin API."""
from extraer_tickets import calcular_tiempos, construir_resumen, parse_dt


def test_parse_dt_acepta_z_y_offset():
    assert parse_dt("2026-03-23T10:00:00Z") is not None
    assert parse_dt("2026-03-23T10:00:00+00:00") is not None


def test_parse_dt_invalido_retorna_none():
    assert parse_dt("") is None
    assert parse_dt(None) is None
    assert parse_dt("no-es-fecha") is None


def test_calcular_tiempos_primera_respuesta_y_resolucion():
    ticket = {
        "created_at": "2026-03-23T10:00:00Z",
        "close_at": "2026-03-23T12:00:00Z",
    }
    articles = [
        {"sender": "Customer", "created_at": "2026-03-23T10:00:00Z"},
        {"sender": "Agent", "created_at": "2026-03-23T10:30:00Z"},
        {"sender": "Agent", "created_at": "2026-03-23T11:00:00Z"},
    ]
    m = calcular_tiempos(ticket, articles)
    assert m["total_mensajes"] == 3
    assert m["total_respuestas_agente"] == 2
    assert m["tiempo_primera_respuesta_min"] == 30.0
    assert m["tiempo_resolucion_min"] == 120.0


def test_calcular_tiempos_sin_respuesta_agente():
    ticket = {"created_at": "2026-03-23T10:00:00Z"}
    articles = [{"sender": "Customer", "created_at": "2026-03-23T10:00:00Z"}]
    m = calcular_tiempos(ticket, articles)
    assert m["total_respuestas_agente"] == 0
    assert m["tiempo_primera_respuesta_min"] == ""
    assert m["tiempo_resolucion_min"] == ""  # nunca cerrado


def test_construir_resumen_agrupa_y_cuenta():
    filas = [
        {"area": "soporte", "estado": "closed", "total_respuestas_agente": 1,
         "tiempo_primera_respuesta_min": 10, "tiempo_resolucion_min": 60},
        {"area": "soporte", "estado": "open", "total_respuestas_agente": 0,
         "tiempo_primera_respuesta_min": "", "tiempo_resolucion_min": ""},
        {"area": "redes", "estado": "closed", "total_respuestas_agente": 1,
         "tiempo_primera_respuesta_min": 20, "tiempo_resolucion_min": 40},
    ]
    resumen = {r["categoria"]: r for r in construir_resumen(filas, "area", resolved_state="closed")}

    soporte = resumen["soporte"]
    assert soporte["total_tickets"] == 2
    assert soporte["tickets_resueltos"] == 1
    assert soporte["tickets_pendientes"] == 1
    assert soporte["tickets_sin_respuesta_agente"] == 1
    assert soporte["promedio_primera_respuesta_min"] == 10.0  # solo la fila con dato

    redes = resumen["redes"]
    assert redes["total_tickets"] == 1
    assert redes["promedio_tiempo_resolucion_min"] == 40.0


def test_construir_resumen_valor_vacio_usa_label():
    filas = [{"area": "", "estado": "closed", "total_respuestas_agente": 1,
              "tiempo_primera_respuesta_min": "", "tiempo_resolucion_min": ""}]
    resumen = construir_resumen(filas, "area", label_vacio="Sin clasificar")
    assert resumen[0]["categoria"] == "Sin clasificar"
