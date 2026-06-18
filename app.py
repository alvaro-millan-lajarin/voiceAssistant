"""
Backend de la operadora virtual.

Endpoints Vapi (Custom Tools):
- POST /buscar_directorio     Busca persona y devuelve teléfono para warm transfer
- POST /tomar_mensaje         Guarda recado en BD y envía email

Endpoints Admin (requieren cabecera X-API-Key):
- GET    /admin/directorio          Lista el directorio completo
- POST   /admin/directorio          Añade persona
- PUT    /admin/directorio/<id>     Edita persona (nombre, teléfono, alias…)
- DELETE /admin/directorio/<id>     Elimina persona
- GET    /admin/recados             Lista recados recibidos (últimos 200)
"""

import os
import json
import smtplib
from dotenv import load_dotenv

load_dotenv()
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
_db_url = os.environ.get("DATABASE_URL", "sqlite:///operadora.db")
# Railway a veces entrega postgres:// — SQLAlchemy 2.x necesita postgresql://
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "")
ADMIN_API_KEY  = os.environ.get("ADMIN_API_KEY", "")

db = SQLAlchemy(app)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class Persona(db.Model):
    __tablename__ = "directorio"

    id           = db.Column(db.String(50),  primary_key=True)
    nombre       = db.Column(db.String(100), nullable=False)
    departamento = db.Column(db.String(100), nullable=False)
    telefono     = db.Column(db.String(20),  default="")
    email        = db.Column(db.String(100), default="")
    _alias       = db.Column("alias", db.Text, default="[]")
    activo       = db.Column(db.Boolean, default=True)

    @property
    def alias(self):
        return json.loads(self._alias or "[]")

    @alias.setter
    def alias(self, value):
        self._alias = json.dumps(value or [])

    def to_dict(self):
        return {
            "id":           self.id,
            "nombre":       self.nombre,
            "departamento": self.departamento,
            "telefono":     self.telefono,
            "email":        self.email,
            "alias":        self.alias,
            "activo":       self.activo,
        }


class Recado(db.Model):
    __tablename__ = "recados"

    id                = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nombre_llamante   = db.Column(db.String(100))
    empresa           = db.Column(db.String(100))
    destino_deseado   = db.Column(db.String(100))
    motivo            = db.Column(db.Text)
    telefono_contacto = db.Column(db.String(20))
    urgencia          = db.Column(db.String(20), default="normal")
    email_enviado     = db.Column(db.Boolean, default=False)
    created_at        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id":                self.id,
            "nombre_llamante":   self.nombre_llamante,
            "empresa":           self.empresa,
            "destino_deseado":   self.destino_deseado,
            "motivo":            self.motivo,
            "telefono_contacto": self.telefono_contacto,
            "urgencia":          self.urgencia,
            "email_enviado":     self.email_enviado,
            "created_at":        self.created_at.isoformat() if self.created_at else None,
        }


# ---------------------------------------------------------------------------
# Seed inicial
# ---------------------------------------------------------------------------
_SEED = [
    {"id": "ana_aduanas",      "nombre": "Ana",       "departamento": "Aduanas",     "alias": ["aduanas", "ana de aduanas", "ana aduanas"]},
    {"id": "ana_export",       "nombre": "Ana",       "departamento": "Exportación", "alias": ["exportacion", "exportación", "export", "ana export"]},
    {"id": "carlos_comercial", "nombre": "Carlos",    "departamento": "Comercial",   "alias": ["comercial", "ventas", "carlos"]},
    {"id": "recepcion_general","nombre": "Recepción", "departamento": "General",     "alias": ["general", "información", "recepción", "recepcion"]},
]

def _init_db():
    db.create_all()
    if Persona.query.count() == 0:
        for d in _SEED:
            p = Persona(id=d["id"], nombre=d["nombre"], departamento=d["departamento"])
            p.alias = d["alias"]
            db.session.add(p)
        db.session.commit()
        print("[db] Directorio inicial cargado", flush=True)


# ---------------------------------------------------------------------------
# Auth para endpoints admin
# ---------------------------------------------------------------------------
def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ADMIN_API_KEY or request.headers.get("X-API-Key") != ADMIN_API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _buscar(consulta: str):
    if not consulta:
        return []
    q = consulta.lower().strip()
    return [
        p for p in Persona.query.filter_by(activo=True).all()
        if q in p.nombre.lower()
        or q in p.departamento.lower()
        or any(q in a or a in q for a in p.alias)
    ]


def _extraer_tool_call(data: dict):
    """Soporta todos los formatos de Vapi: message.toolCalls y toolCalls raíz."""
    if not isinstance(data, dict):
        return None, {}
    # Formato 1: { "message": { "toolCalls": [...] } }
    tc_list = data.get("message", {}).get("toolCalls")
    # Formato 2: { "toolCalls": [...] }  (top-level, formato más común)
    if not tc_list:
        tc_list = data.get("toolCalls")
    if tc_list:
        tc   = tc_list[0]
        args = tc["function"].get("arguments", {})
        if isinstance(args, str):
            args = json.loads(args)
        print(f"[tool_call] id={tc['id']} args={args}", flush=True)
        return tc["id"], args
    # Fallback: args planos en el body
    print("[tool_call] formato no reconocido, usando body completo", flush=True)
    return None, (data or {})


def _responder_vapi(tool_call_id, resultado, destination=None):
    if tool_call_id:
        entry = {"toolCallId": tool_call_id, "result": resultado}
        if destination:
            entry["destination"] = destination
        return jsonify({"results": [entry]})
    # Fallback sin toolCallId (no debería llegar aquí con el fix de arriba)
    resp = {"result": resultado}
    if destination:
        resp["destination"] = destination
    return jsonify(resp)


def _enviar_email(asunto: str, cuerpo_html: str, destino: str) -> bool:
    if not GMAIL_USER or not GMAIL_PASSWORD:
        print("[email] Faltan credenciales", flush=True)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"]    = f"Operadora Virtual <{GMAIL_USER}>"
        msg["To"]      = destino
        msg.attach(MIMEText(cuerpo_html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
            s.login(GMAIL_USER, GMAIL_PASSWORD)
            s.sendmail(GMAIL_USER, destino, msg.as_string())
        print(f"[email] OK → {destino}", flush=True)
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
# Tool Vapi: buscar_directorio
# Devuelve teléfono para que Vapi ejecute el warm transfer
# ---------------------------------------------------------------------------
@app.post("/buscar_directorio")
def buscar_directorio():
    data = request.get_json(silent=True) or {}
    print("[buscar_directorio] payload:", data, flush=True)

    try:
        tool_call_id, args = _extraer_tool_call(data)
        consulta = args.get("consulta", "")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        print(f"[buscar_directorio] error parseando: {e}", flush=True)
        return jsonify({"results": []}), 400

    coincidencias = _buscar(consulta)
    print(f"[buscar_directorio] '{consulta}' → {[p.id for p in coincidencias]}", flush=True)

    if not coincidencias:
        resultado = (
            "No he encontrado a nadie con ese nombre o departamento. "
            "Pregunta al llamante si quiere que le pases con recepción general "
            "o prefiere dejar un mensaje."
        )
    elif len(coincidencias) == 1:
        p = coincidencias[0]
        resultado = {
            "match":   "unico",
            "persona": {
                "id":           p.id,
                "nombre":       p.nombre,
                "departamento": p.departamento,
                "telefono":     p.telefono or None,
            },
        }
    else:
        resultado = {
            "match":    "varios",
            "personas": [
                {
                    "id":           p.id,
                    "nombre":       p.nombre,
                    "departamento": p.departamento,
                    "telefono":     p.telefono or None,
                }
                for p in coincidencias
            ],
        }

    return _responder_vapi(tool_call_id, resultado)


# ---------------------------------------------------------------------------
# Tool Vapi: tomar_mensaje
# Guarda el recado en BD y lo envía por email
# ---------------------------------------------------------------------------
@app.post("/tomar_mensaje")
def tomar_mensaje():
    data = request.get_json(silent=True) or {}
    print("[tomar_mensaje] payload:", data, flush=True)

    try:
        tool_call_id, args = _extraer_tool_call(data)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        print(f"[tomar_mensaje] error parseando: {e}", flush=True)
        return jsonify({"results": []}), 400

    nombre   = args.get("nombre_llamante",   "(no facilitado)")
    empresa  = args.get("empresa",           "(no facilitada)")
    destino  = args.get("destino_deseado",   "(no especificado)")
    motivo   = args.get("motivo",            "(sin detalle)")
    telefono = args.get("telefono_contacto", "(no facilitado)")
    urgencia = args.get("urgencia",          "normal")

    recado = Recado(
        nombre_llamante=nombre,
        empresa=empresa,
        destino_deseado=destino,
        motivo=motivo,
        telefono_contacto=telefono,
        urgencia=urgencia,
    )
    db.session.add(recado)

    asunto = f"[Recado] {nombre} pregunta por {destino}"
    if urgencia.lower() == "urgente":
        asunto = "🔴 URGENTE · " + asunto

    cuerpo = f"""
    <h2>Nuevo recado de la operadora</h2>
    <p>
      <b>Quién llama:</b> {nombre}<br>
      <b>Empresa:</b> {empresa}<br>
      <b>Pregunta por:</b> {destino}<br>
      <b>Motivo:</b> {motivo}<br>
      <b>Teléfono de contacto:</b> {telefono}<br>
      <b>Urgencia:</b> {urgencia}
    </p>
    """

    ok = _enviar_email(asunto, cuerpo, GMAIL_USER)
    recado.email_enviado = ok
    db.session.commit()
    print(f"[tomar_mensaje] id={recado.id} email_enviado={ok}", flush=True)

    resultado = (
        "Mensaje registrado y enviado correctamente. "
        "Confirma al llamante que se le devolverá la llamada lo antes posible."
        if ok else
        "Mensaje guardado, pero hubo un problema al enviarlo por email. "
        "Despide al llamante con normalidad; el recado queda registrado en el sistema."
    )
    return _responder_vapi(tool_call_id, resultado)


# ---------------------------------------------------------------------------
# Tool Vapi: transferir_llamada
# Busca el teléfono en la BD y devuelve la instrucción de transferencia a Vapi.
# Así el número vive solo en la BD — no hay que tocarlo en Vapi.
# ---------------------------------------------------------------------------
@app.post("/transferir")
def transferir():
    data = request.get_json(silent=True) or {}
    print("[transferir] payload:", data, flush=True)

    try:
        tool_call_id, args = _extraer_tool_call(data)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        print(f"[transferir] error parseando: {e}", flush=True)
        return jsonify({"results": []}), 400

    persona_id      = args.get("persona_id", "")
    nombre_llamante = args.get("nombre_llamante", "el llamante")
    empresa         = args.get("empresa", "")
    motivo          = args.get("motivo", "una consulta")

    persona = Persona.query.get(persona_id)

    if not persona or not persona.telefono:
        resultado = (
            "No se pudo transferir: persona no encontrada o sin teléfono configurado. "
            "Ofrece al llamante dejar un mensaje."
        )
        return _responder_vapi(tool_call_id, resultado)

    empresa_txt    = f" de {empresa}" if empresa else ""
    msg_susurro    = (
        f"Hola {persona.nombre}, te paso con {nombre_llamante}{empresa_txt}, "
        f"llama por {motivo}."
    )

    print(f"[transferir] → {persona.nombre} ({persona.telefono})", flush=True)

    destino = {
        "type": "number",
        "number": persona.telefono,
        "transferPlan": {
            "mode": "blind-transfer",
        },
    }

    return _responder_vapi(
        tool_call_id,
        f"Transfiriendo con {persona.nombre} ({persona.departamento}).",
        destination=destino,
    )


# ---------------------------------------------------------------------------
# Admin: CRUD directorio
# ---------------------------------------------------------------------------
@app.get("/admin/directorio")
@require_api_key
def admin_list():
    return jsonify([p.to_dict() for p in Persona.query.order_by(Persona.departamento).all()])


@app.post("/admin/directorio")
@require_api_key
def admin_create():
    body = request.get_json(silent=True) or {}
    if not body.get("id") or not body.get("nombre") or not body.get("departamento"):
        return jsonify({"error": "id, nombre y departamento son obligatorios"}), 400
    if Persona.query.get(body["id"]):
        return jsonify({"error": "Ya existe una persona con ese id"}), 409
    p = Persona(
        id=body["id"],
        nombre=body["nombre"],
        departamento=body["departamento"],
        telefono=body.get("telefono", ""),
        email=body.get("email", ""),
        activo=body.get("activo", True),
    )
    p.alias = body.get("alias", [])
    db.session.add(p)
    db.session.commit()
    return jsonify(p.to_dict()), 201


@app.put("/admin/directorio/<string:persona_id>")
@require_api_key
def admin_update(persona_id):
    p = Persona.query.get_or_404(persona_id)
    body = request.get_json(silent=True) or {}
    for field in ("nombre", "departamento", "telefono", "email", "activo"):
        if field in body:
            setattr(p, field, body[field])
    if "alias" in body:
        p.alias = body["alias"]
    db.session.commit()
    return jsonify(p.to_dict())


@app.delete("/admin/directorio/<string:persona_id>")
@require_api_key
def admin_delete(persona_id):
    p = Persona.query.get_or_404(persona_id)
    db.session.delete(p)
    db.session.commit()
    return jsonify({"deleted": persona_id})


# ---------------------------------------------------------------------------
# Admin: recados
# ---------------------------------------------------------------------------
@app.get("/admin/recados")
@require_api_key
def admin_recados():
    recados = Recado.query.order_by(Recado.created_at.desc()).limit(200).all()
    return jsonify([r.to_dict() for r in recados])


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
with app.app_context():
    _init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
