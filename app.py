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
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "")

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
    """
    Devuelve (tool_call_id, arguments_dict).
    Soporta dos formatos de Vapi:
    - API Request (actual): el body son los argumentos directamente,
      p. ej. {"consulta": "Ana"} o {"nombre_llamante": "...", ...}
    - Custom Function (antiguo): {"message": {"toolCalls": [{...}]}}
    """
    # Formato antiguo (envuelto en message.toolCalls)
    if isinstance(data, dict) and "message" in data and "toolCalls" in data.get("message", {}):
        tool_call = data["message"]["toolCalls"][0]
        tool_call_id = tool_call["id"]
        args = tool_call["function"].get("arguments", {})
        if isinstance(args, str):
            args = json.loads(args)
        return tool_call_id, args

    # Formato API Request (plano): el body son los argumentos
    # No tenemos toolCallId, devolvemos None y respondemos sin él
    return None, (data or {})


def enviar_email_recado(asunto: str, cuerpo_html: str, destino: str) -> bool:
    """Envía un email usando Gmail SMTP. Devuelve True si OK."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not GMAIL_USER or not GMAIL_PASSWORD:
        print("[email] Falta GMAIL_USER o GMAIL_PASSWORD en variables de entorno", flush=True)
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"] = f"Operadora Virtual <{GMAIL_USER}>"
        msg["To"] = destino
        msg.attach(MIMEText(cuerpo_html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, destino, msg.as_string())

        print(f"[email] Enviado OK a {destino}", flush=True)
        return True
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

    # Formato de respuesta: si hay toolCallId, envolver; si no, plano.
    if tool_call_id:
        return jsonify({"results": [{"toolCallId": tool_call_id, "result": resultado}]})
    return jsonify({"result": resultado})


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

    ok = enviar_email_recado(asunto, cuerpo, GMAIL_USER)
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

    if tool_call_id:
        return jsonify({"results": [{"toolCallId": tool_call_id, "result": resultado}]})
    return jsonify({"result": resultado})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)