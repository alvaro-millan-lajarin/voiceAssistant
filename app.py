"""
Backend de la operadora virtual.
Expone los endpoints que Vapi llamará como Custom Tools.

Por ahora solo: /buscar_directorio
Próximos pasos: /tomar_mensaje  (la transferencia la haremos con la built-in de Vapi)
"""

import os
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Directorio de prueba (Clasquin).
# Más adelante esto vendrá de una base de datos, una por cliente.
# ---------------------------------------------------------------------------
DIRECTORIO = [
    {
        "id": "ana_aduanas",
        "nombre": "Ana",
        "departamento": "Aduanas",
        "alias": ["aduanas", "ana de aduanas", "ana aduanas"],
    },
    {
        "id": "ana_export",
        "nombre": "Ana",
        "departamento": "Exportación",
        "alias": ["exportacion", "exportación", "export", "ana export"],
    },
    {
        "id": "carlos_comercial",
        "nombre": "Carlos",
        "departamento": "Comercial",
        "alias": ["comercial", "ventas", "carlos"],
    },
    {
        "id": "recepcion_general",
        "nombre": "Recepción",
        "departamento": "General",
        "alias": ["general", "información", "recepción", "recepcion"],
    },
]


def buscar_en_directorio(consulta: str):
    """Devuelve las entradas del directorio que coinciden con la consulta."""
    if not consulta:
        return []
    q = consulta.lower().strip()
    coincidencias = []
    for p in DIRECTORIO:
        if (
            q in p["nombre"].lower()
            or q in p["departamento"].lower()
            or any(q in a or a in q for a in p["alias"])
        ):
            coincidencias.append(
                {
                    "id": p["id"],
                    "nombre": p["nombre"],
                    "departamento": p["departamento"],
                }
            )
    return coincidencias


# ---------------------------------------------------------------------------
# Health check, útil para Railway, UptimeRobot, y para que tú compruebes
# en el navegador que la app está viva.
# ---------------------------------------------------------------------------
@app.get("/")
def health():
    return jsonify({"status": "ok", "service": "operadora-backend"})


# ---------------------------------------------------------------------------
# Tool: buscar_directorio
# Vapi envía un POST con esta estructura aproximada:
# {
#   "message": {
#     "toolCalls": [
#       {
#         "id": "call_abc123",
#         "function": {
#           "name": "buscar_directorio",
#           "arguments": { "consulta": "Ana de aduanas" }
#         }
#       }
#     ]
#   }
# }
#
# Y espera una respuesta con esta forma:
# {
#   "results": [
#     { "toolCallId": "call_abc123", "result": <lo que sea> }
#   ]
# }
# ---------------------------------------------------------------------------
@app.post("/buscar_directorio")
def buscar_directorio():
    data = request.get_json(silent=True) or {}
    print("[buscar_directorio] payload recibido:", data, flush=True)

    try:
        tool_call = data["message"]["toolCalls"][0]
        tool_call_id = tool_call["id"]
        # Vapi a veces manda 'arguments' como dict y a veces como string JSON.
        args = tool_call["function"].get("arguments", {})
        if isinstance(args, str):
            import json
            args = json.loads(args)
        consulta = args.get("consulta", "")
    except (KeyError, IndexError, TypeError) as e:
        print(f"[buscar_directorio] error parseando payload: {e}", flush=True)
        return jsonify({"results": []}), 400

    coincidencias = buscar_en_directorio(consulta)
    print(f"[buscar_directorio] consulta='{consulta}' -> {coincidencias}", flush=True)

    if not coincidencias:
        resultado = (
            "Sin coincidencias. Ofrece al llamante transferir a recepción "
            "general o tomar un mensaje."
        )
    elif len(coincidencias) == 1:
        resultado = {"match": "unico", "persona": coincidencias[0]}
    else:
        resultado = {"match": "varios", "personas": coincidencias}

    return jsonify(
        {"results": [{"toolCallId": tool_call_id, "result": resultado}]}
    )


# ---------------------------------------------------------------------------
# Arranque local. En Railway se usa gunicorn (ver Procfile).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
