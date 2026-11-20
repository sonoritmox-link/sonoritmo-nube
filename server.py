import json
import logging
import os
import secrets
import socket
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(
    filename=os.path.join(BASE_DIR, "arcotel_audit.log"),
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

app = FastAPI(title="SONORITMO Link - Gestión por Puntos")

ESTACIONES: Dict[str, dict] = {
    "CANELA-BOLIVAR": {
        "clave_maestra": "ClaveSegura2026*",
        "puntos": [
            {"id": "PT-01", "nombre": "Planta Principal", "clave": "RX-CANELA-01", "activo": True},
        ],
    }
}

salas: Dict[str, dict] = {}
puntos_tx: Dict[str, Optional[WebSocket]] = {}
puntos_rx: Dict[str, List[WebSocket]] = defaultdict(list)


class NuevoPuntoPayload(BaseModel):
    sala: str
    nombre_punto: str


def get_local_ipv4() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def ensure_firewall_port(port: int = 9000):
    if sys.platform != "win32":
        return
    rule_name = f"SONORITMO LINK {port}"
    try:
        check = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if rule_name not in (check.stdout or ""):
            res = subprocess.run(
                [
                    "netsh", "advfirewall", "firewall", "add", "rule",
                    f"name={rule_name}", "dir=in", "action=allow",
                    "protocol=TCP", f"localport={port}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                logging.info(f"FIREWALL HABILITADO | Puerto TCP {port}")
            else:
                logging.warning(f"FIREWALL SIN PERMISOS | Puerto TCP {port}: {(res.stderr or res.stdout).strip()}")
    except Exception as exc:
        logging.warning(f"FIREWALL ERROR | Puerto TCP {port}: {exc}")


@app.on_event("startup")
async def on_startup():
    ensure_firewall_port(9000)
    ip = get_local_ipv4()
    logging.info(f"SERVIDOR INICIADO | IP Local: {ip} | Puerto: 9000")


@app.get("/api/host-info")
async def host_info():
    ip = get_local_ipv4()
    return JSONResponse({"ip": ip, "port": 9000, "host": f"{ip}:9000"})


def leer_archivo(nombre: str) -> str:
    rutas_candidatas = (
        os.path.join(BASE_DIR, nombre),
        os.path.join(BASE_DIR, "template", nombre),
        os.path.join(BASE_DIR, "templates", nombre),
        nombre,
        f"template/{nombre}",
    )
    for ruta in rutas_candidatas:
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            continue
    return f"<h3>No se encontró {nombre}</h3>"


@app.get("/", response_class=HTMLResponse)
async def inicio():
    return """
    <html><body style="font-family:system-ui;background:#0f172a;color:#f8fafc;padding:30px;">
      <h2>SONORITMO Link — Modelos de Operación</h2>
      <ul>
        <li><a href="/admin" style="color:#38bdf8;">Panel de Puntos y Claves (Añadir Punto / Cobro por Punto)</a></li>
        <li><a href="/tx" style="color:#38bdf8;">Carátula Emisor (Entrada / Transformación / Emisión)</a></li>
        <li><a href="/rx" style="color:#4ade80;">Carátula Receptor (Llegada / Transformación / Salida)</a></li>
      </ul>
    </body></html>
    """


@app.get("/admin", response_class=HTMLResponse)
async def panel_admin():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
      <meta charset="UTF-8" />
      <title>SONORITMO Link — Puntos y Claves</title>
      <style>
        body { font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; max-width: 780px; margin: 24px auto; padding: 16px; }
        .card { background: #1e293b; padding: 20px; border-radius: 12px; margin-bottom: 18px; }
        h2, h3 { margin-top: 0; color: #38bdf8; }
        .row { display: flex; gap: 10px; flex-wrap: wrap; }
        input, select, button { padding: 10px 12px; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: #fff; font-size: 14px; }
        button { background: #0284c7; border: none; font-weight: bold; cursor: pointer; }
        button.verde { background: #16a34a; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
        th, td { border-bottom: 1px solid #334155; padding: 10px 8px; text-align: left; }
        .badge { padding: 3px 8px; border-radius: 999px; font-size: 11px; background: #0f172a; color: #4ade80; }
      </style>
    </head>
    <body>
      <div class="card">
        <h2>SONORITMO Link — Control de Puntos y Claves</h2>
        <p style="color:#94a3b8;font-size:13px;">Crea puntos para cada emisora cliente, genera automáticamente una clave única por punto y visualiza el total de puntos facturables.</p>
        <div class="row">
          <input id="sala" value="CANELA-BOLIVAR" placeholder="Código Emisora / Sala" />
          <input id="nombrePunto" placeholder="Ej: Repetidora Cerro / Móvil 1" />
          <button class="verde" onclick="anadirPunto()">+ Añadir punto</button>
          <button onclick="cargarPuntos()">Actualizar lista</button>
        </div>
        <div id="resumen" style="margin-top:12px;font-size:13px;color:#facc15;"></div>
      </div>

      <div class="card">
        <h3>Puntos Registrados y Claves Generadas</h3>
        <table>
          <thead>
            <tr>
              <th>Emisora</th>
              <th>ID Punto</th>
              <th>Nombre del Punto</th>
              <th>Clave Generada</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody id="tablaPuntos"></tbody>
        </table>
      </div>

      <script>
        async function anadirPunto() {
          const sala = document.getElementById("sala").value.trim().toUpperCase();
          const nombre_punto = document.getElementById("nombrePunto").value.trim();
          if (!sala || !nombre_punto) return alert("Ingresa emisora y nombre del punto.");
          const res = await fetch("/api/puntos/anadir", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sala, nombre_punto })
          });
          if (res.ok) {
            document.getElementById("nombrePunto").value = "";
            cargarPuntos();
          }
        }

        async function cargarPuntos() {
          const res = await fetch("/api/puntos");
          const data = await res.json();
          const tbody = document.getElementById("tablaPuntos");
          tbody.innerHTML = "";
          let totalPuntos = 0;

          for (const [sala, info] of Object.entries(data.estaciones)) {
            for (const p of info.puntos) {
              if (p.activo) totalPuntos++;
              tbody.innerHTML += `
                <tr>
                  <td><strong>${sala}</strong></td>
                  <td>${p.id}</td>
                  <td>${p.nombre}</td>
                  <td><code>${p.clave}</code></td>
                  <td><span class="badge">${p.activo ? "ACTIVO" : "INACTIVO"}</span></td>
                </tr>
              `;
            }
          }
          document.getElementById("resumen").innerText =
            `Total de puntos activos registrados: ${totalPuntos} (listo para calcular cobro por punto).`;
        }
        cargarPuntos();
      </script>
    </body>
    </html>
    """


@app.get("/api/puntos")
async def listar_puntos():
    return JSONResponse({"estaciones": ESTACIONES})


@app.post("/api/puntos/anadir")
async def api_anadir_punto(payload: NuevoPuntoPayload):
    sala = payload.sala.strip().upper()
    if sala not in ESTACIONES:
        ESTACIONES[sala] = {
            "clave_maestra": secrets.token_urlsafe(8),
            "puntos": [],
        }
    indice = len(ESTACIONES[sala]["puntos"]) + 1
    nuevo_punto = {
        "id": f"PT-{indice:02d}",
        "nombre": payload.nombre_punto.strip(),
        "clave": f"KEY-{secrets.token_hex(3).upper()}",
        "activo": True,
    }
    ESTACIONES[sala]["puntos"].append(nuevo_punto)
    logging.info(f"PUNTO AGREGADO | Sala: {sala} | ID: {nuevo_punto['id']} | Nombre: {nuevo_punto['nombre']}")
    return JSONResponse({"ok": True, "punto": nuevo_punto})


@app.get("/tx", response_class=HTMLResponse)
async def get_tx():
    return leer_archivo("tx.html")


@app.get("/rx", response_class=HTMLResponse)
async def get_rx():
    return leer_archivo("rx.html")


async def _enviar_mensaje(ws: WebSocket, mensaje: dict | bytes | str):
    if isinstance(mensaje, bytes):
        await ws.send_bytes(mensaje)
    elif isinstance(mensaje, str):
        await ws.send_text(mensaje)
    else:
        await ws.send_text(json.dumps(mensaje))


@app.websocket("/ws/tx/{punto}")
async def ws_tx_directo(websocket: WebSocket, punto: str):
    await websocket.accept()
    punto_key = punto.strip()
    puntos_tx[punto_key] = websocket
    print(f"[WS TX] Conectado en punto: {punto_key}")
    logging.info(f"TX CONECTADO | Punto: {punto_key}")

    try:
        for rx_ws in list(puntos_rx[punto_key]):
            try:
                await _enviar_mensaje(rx_ws, {"type": "ready", "punto": punto_key})
            except Exception:
                pass
        if puntos_rx[punto_key]:
            try:
                await _enviar_mensaje(websocket, {"type": "ready", "punto": punto_key})
            except Exception:
                pass

        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                raise WebSocketDisconnect()

            payload = msg.get("bytes") if msg.get("bytes") is not None else msg.get("text")
            if payload is None:
                continue

            desconectados: List[WebSocket] = []
            for rx_ws in list(puntos_rx[punto_key]):
                try:
                    await _enviar_mensaje(rx_ws, payload)
                except Exception:
                    desconectados.append(rx_ws)

            for rx_ws in desconectados:
                if rx_ws in puntos_rx[punto_key]:
                    puntos_rx[punto_key].remove(rx_ws)
    except (WebSocketDisconnect, Exception) as exc:
        logging.warning(f"TX CERRADO | Punto: {punto_key} | Motivo: {type(exc).__name__}")
    finally:
        if puntos_tx.get(punto_key) is websocket:
            puntos_tx[punto_key] = None
        print(f"[WS TX] Desconectado en punto: {punto_key}")
        for rx_ws in list(puntos_rx[punto_key]):
            try:
                await _enviar_mensaje(rx_ws, {"type": "peer_disconnected", "punto": punto_key})
            except Exception:
                pass


@app.websocket("/ws/rx/{punto}")
async def ws_rx_directo(websocket: WebSocket, punto: str):
    await websocket.accept()
    punto_key = punto.strip()
    puntos_rx[punto_key].append(websocket)
    print(f"[WS RX] Conectado en punto: {punto_key}")
    logging.info(f"RX CONECTADO | Punto: {punto_key} | Total RX: {len(puntos_rx[punto_key])}")

    try:
        tx_ws = puntos_tx.get(punto_key)
        if tx_ws is not None:
            try:
                await _enviar_mensaje(tx_ws, {"type": "ready", "punto": punto_key})
            except Exception:
                pass
            try:
                await _enviar_mensaje(websocket, {"type": "ready", "punto": punto_key})
            except Exception:
                pass

        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                raise WebSocketDisconnect()

            payload = msg.get("bytes") if msg.get("bytes") is not None else msg.get("text")
            tx_ws = puntos_tx.get(punto_key)
            if payload is not None and tx_ws is not None:
                try:
                    await _enviar_mensaje(tx_ws, payload)
                except Exception:
                    pass
    except (WebSocketDisconnect, Exception) as exc:
        logging.warning(f"RX CERRADO | Punto: {punto_key} | Motivo: {type(exc).__name__}")
    finally:
        if websocket in puntos_rx[punto_key]:
            puntos_rx[punto_key].remove(websocket)
        print(f"[WS RX] Desconectado en punto: {punto_key}")


def clave_valida(sala: str, rol: str, clave: str) -> bool:
    info = ESTACIONES.get(sala)
    if not info:
        return False
    if rol == "tx":
        return clave == info["clave_maestra"] or any(p["activo"] and p["clave"] == clave for p in info["puntos"])
    return any(p["activo"] and p["clave"] == clave for p in info["puntos"])


@app.websocket("/ws/{rol}/{sala}/{clave}")
async def websocket_endpoint(websocket: WebSocket, rol: str, sala: str, clave: str):
    await websocket.accept()
    sala = sala.upper()

    if not clave_valida(sala, rol, clave):
        logging.warning(f"ACCESO RECHAZADO | Sala: {sala} | Rol: {rol}")
        await websocket.send_text(json.dumps({"type": "error", "msg": "Sala o clave de punto no válida"}))
        await websocket.close()
        return

    if sala not in salas:
        salas[sala] = {"tx": None, "rx": []}

    if rol == "tx":
        salas[sala]["tx"] = websocket
        logging.info(f"EMISOR CONECTADO | Sala: {sala}")
        for receptor in list(salas[sala]["rx"]):
            await receptor.send_text(json.dumps({"type": "ready"}))
        if salas[sala]["rx"]:
            await websocket.send_text(json.dumps({"type": "ready"}))
    elif rol == "rx":
        salas[sala]["rx"].append(websocket)
        logging.info(f"RECEPTOR CONECTADO | Sala: {sala} | Total RX: {len(salas[sala]['rx'])}")
        if salas[sala]["tx"]:
            await salas[sala]["tx"].send_text(json.dumps({"type": "ready"}))
            await websocket.send_text(json.dumps({"type": "ready"}))

    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                raise WebSocketDisconnect()

            payload = msg.get("bytes") if msg.get("bytes") is not None else msg.get("text")
            if payload is None:
                continue

            if rol == "tx":
                for receptor in list(salas[sala]["rx"]):
                    await _enviar_mensaje(receptor, payload)
            elif rol == "rx" and salas[sala]["tx"]:
                await _enviar_mensaje(salas[sala]["tx"], payload)
    except WebSocketDisconnect:
        if rol == "tx":
            salas[sala]["tx"] = None
            logging.warning(f"EMISOR DESCONECTADO | Sala: {sala}")
            for receptor in list(salas[sala]["rx"]):
                await receptor.send_text(json.dumps({"type": "peer_disconnected"}))
        elif rol == "rx":
            if websocket in salas[sala]["rx"]:
                salas[sala]["rx"].remove(websocket)
            logging.warning(f"RECEPTOR DESCONECTADO | Sala: {sala} | Restantes RX: {len(salas[sala]['rx'])}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)