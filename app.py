"""
Backend de la operadora virtual.
Expone los endpoints que Vapi llamará como Custom Tools.

Endpoints:
- GET  /                  Health check
- POST /buscar_directorio Busca persona o departamento en el directorio
- POST /tomar_mensaje     Recibe un recado y lo envía por email
"""

import os
import json
import urllib.request
import urllib.error
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuración (lee variables de entorno de Railway)
# ---------------------------------------------------------------------------
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_DESTINO = os.environ.get("EMAIL_DESTINO", "")

# ---------------------------------------------------------------------------
# Directorio de prueba (Clasquin).
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


def extraer_tool_call(data: dict):
    """Devuelve (tool_call_id, arguments_dict). Lanza KeyError/IndexError si no encaja."""
    tool_call = data["message"]["toolCalls"][0]
    tool_call_id = tool_call["id"]
    args = tool_call["function"].get("arguments", {})
    if isinstance(args, str):
        args = json.loads(args)
    return tool_call_id, args


def enviar_email_recado(asunto: str, cuerpo_html: str) -> bool:
    """Envía un email usando la API HTTP de Resend. Devuelve True si OK."""
    if not RESEND_API_KEY or not EMAIL_DESTINO:
        print("[email] Falta RESEND_API_KEY o EMAIL_DESTINO en variables de entorno", flush=True)
        return False

    payload = {
        # 'onboarding@resend.dev' es el remitente de pruebas que da Resend sin verificar dominio.
        # Cuando tengas tu propio dominio verificado en Resend, cambia esto por noreply@tudominio.com
        "from": "Operadora Virtual <onboarding@resend.dev>",
        "to": [EMAIL_DESTINO],
        "subject": asunto,
        "html": cuerpo_html,
    }

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[email] Enviado OK status={resp.status}", flush=True)
            return True
    except urllib.error.HTTPError as e:
        print(f"[email] HTTPError {e.code}: {e.read().decode('utf-8', errors='ignore')}", flush=True)
        return False
    except Exception as e:
        print(f"[email] Error: {e}", flush=True)
        return False


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/")
def health():
    return jsonify({"status": "ok", "service": "operadora-backend"})


# ---------------------------------------------------------------------------
# Tool: buscar_directorio
# ---------------------------------------------------------------------------
@app.post("/buscar_directorio")
def buscar_directorio():
    data = request.get_json(silent=True) or {}
    print("[buscar_directorio] payload:", data, flush=True)

    try:
        tool_call_id, args = extraer_tool_call(data)
        consulta = args.get("consulta", "")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        print(f"[buscar_directorio] error parseando: {e}", flush=True)
        return jsonify({"results": []}), 400

    coincidencias = buscar_en_directorio(consulta)
    print(f"[buscar_directorio] '{consulta}' -> {coincidencias}", flush=True)

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
# Tool: tomar_mensaje
# Recibe los datos del recado y lo envía por email al destinatario.
# ---------------------------------------------------------------------------
@app.post("/tomar_mensaje")
def tomar_mensaje():
    data = request.get_json(silent=True) or {}
    print("[tomar_mensaje] payload:", data, flush=True)

    try:
        tool_call_id, args = extraer_tool_call(data)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        print(f"[tomar_mensaje] error parseando: {e}", flush=True)
        return jsonify({"results": []}), 400

    nombre = args.get("nombre_llamante", "(no facilitado)")
    empresa = args.get("empresa", "(no facilitada)")
    destino = args.get("destino_deseado", "(no especificado)")
    motivo = args.get("motivo", "(sin detalle)")
    telefono = args.get("telefono_contacto", "(no facilitado)")
    urgencia = args.get("urgencia", "normal")

    asunto = f"[Recado] {nombre} pregunta por {destino}"
    if urgencia.lower() == "urgente":
        asunto = "🔴 URGENTE · " + asunto

    cuerpo = f"""
    <h2>Nuevo recado de la operadora</h2>
    <p><b>Quién llama:</b> {nombre}<br>
    <b>Empresa:</b> {empresa}<br>
    <b>Pregunta por:</b> {destino}<br>
    <b>Motivo:</b> {motivo}<br>
    <b>Teléfono de contacto:</b> {telefono}<br>
    <b>Urgencia:</b> {urgencia}</p>
    """

    ok = enviar_email_recado(asunto, cuerpo)
    print(f"[tomar_mensaje] email_enviado={ok}", flush=True)

    if ok:
        resultado = (
            "Mensaje registrado y enviado al destinatario correctamente. "
            "Confirma al llamante que se le devolverá la llamada lo antes posible."
        )
    else:
        resultado = (
            "El mensaje se ha recogido pero hubo un problema al notificarlo. "
            "Despide al llamante con normalidad; el incidente queda registrado."
        )

    return jsonify(
        {"results": [{"toolCallId": tool_call_id, "result": resultado}]}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)