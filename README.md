# Operadora virtual — backend

Backend mínimo en Flask que expone las *tools* que Vapi llamará durante una llamada.

Por ahora solo tiene `/buscar_directorio`. Las otras (`tomar_mensaje`) se añaden encima de la misma app. La transferencia se hace con la tool nativa de Vapi (`transferCall`), no aquí.

---

## 1. Probarlo en local primero (opcional, 5 min)

```bash
cd operadora-backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Abre `http://localhost:5000/` en el navegador. Tienes que ver `{"status":"ok","service":"operadora-backend"}`. Si lo ves, funciona.

(Cuando estés en local y quieras que Vapi lo alcance, puedes usar `ngrok http 5000` y darle a Vapi la URL pública. Pero para 24/7 saltamos directos a Railway.)

---

## 2. Subirlo a GitHub

1. Crea un repo nuevo en https://github.com/new (privado vale).
2. En la carpeta del proyecto:
   ```bash
   git init
   git add .
   git commit -m "operadora backend inicial"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/operadora-backend.git
   git push -u origin main
   ```

---

## 3. Desplegar en Railway (10 min)

1. Entra en https://railway.app y registra cuenta con tu GitHub.
2. **New Project → Deploy from GitHub repo →** selecciona `operadora-backend`.
3. Railway detecta que es Python, instala `requirements.txt` y arranca con el `Procfile`. No hace falta tocar nada.
4. En la pestaña **Settings → Networking →** pulsa **Generate Domain**. Te dará una URL tipo `https://operadora-backend-production.up.railway.app`.
5. Abre esa URL en el navegador. Tienes que ver el `{"status":"ok",...}`. Si lo ves, ya estás **24/7 en producción**.

> Plan gratuito: Railway te da ~$5 de crédito al mes. Para esta app y pilotos, sobra. Cuando consumas, pagas por uso (céntimos).

---

## 4. Conectar la tool en Vapi

1. En Vapi, menú lateral → **Tools → Create Tool → Custom Function**.
2. Rellena:
   - **Name:** `buscar_directorio`
   - **Description:** "Busca personas o departamentos del directorio de la empresa a partir de lo que pide quien llama. Devuelve coincidencias para que el agente sepa a quién transferir o si tiene que desambiguar."
   - **Server URL:** `https://TU-URL.up.railway.app/buscar_directorio`
   - **Parameters:**
     - `consulta` (string, required) — "Lo que pide la persona, por ejemplo 'Ana de aduanas' o 'el de exportación'."
3. Guarda.
4. Ve a tu **Assistant → Tools** y **añade** la tool que acabas de crear. Sin este paso, el agente no la verá.

---

## 5. Probar la llamada

1. En el Assistant pulsa **Talk to Assistant**.
2. Di: *"Quiero hablar con Ana, la de aduanas."*
3. Mira tres sitios a la vez:
   - **Conversación en Vapi:** ¿el agente sigue el flujo y pide de parte de quién?
   - **Logs de Vapi** (menú lateral → Logs): verás el `tool call` con sus argumentos y la respuesta.
   - **Logs de Railway** (en el proyecto → pestaña *Deployments* → *View Logs*): verás los `print` con la consulta que llegó.

Si las tres cosas pasan, has cerrado el ciclo completo. **Eso es lo que querías**.

---

## 6. Iterar

A partir de aquí, cada cambio en `app.py`:

```bash
git add .
git commit -m "..."
git push
```

Railway redespliega automáticamente. URL no cambia.

---

## 7. Siguientes pasos sugeridos

1. Añadir endpoint `/tomar_mensaje` (formato igual, pero en vez de buscar, envía el recado por email o webhook a Teams).
2. Sacar el directorio del código y meterlo en una BD (Supabase / Neon, gratis).
3. Multi-tenant: que la app sepa de qué empresa es la llamada (Vapi puede pasar metadatos del asistente / número).
4. Monitor con UptimeRobot (gratis) pegándole al `/` cada 5 min.
5. Cuando todo esté estable: panel web para que el cliente edite su propio directorio.
