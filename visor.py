import sys
import threading
import time
import urllib.request
import webview
import uvicorn
from server import app

SERVIDOR_URL = "http://127.0.0.1:8000"


def iniciar_servidor_local():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


def esperar_servidor(url, intentos=20):
    for _ in range(intentos):
        try:
            urllib.request.urlopen(url + "/tx", timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


if __name__ == "__main__":
    # Levanta el motor en segundo plano automáticamente si no está corriendo
    hilo_server = threading.Thread(target=iniciar_servidor_local, daemon=True)
    hilo_server.start()
    esperar_servidor(SERVIDOR_URL)

    modo = "tx"
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("tx", "rx"):
        modo = sys.argv[1].lower()
    else:
        eleccion = input("Selecciona modo [1 = Estudio TX | 2 = Planta RX]: ").strip()
        modo = "rx" if eleccion == "2" else "tx"

    titulo = (
        "SONORITMO Link — Emisor Estudio (TX)"
        if modo == "tx"
        else "SONORITMO Link — Receptor Planta (RX)"
    )

    # Crea la ventana nativa de escritorio
    webview.create_window(
        title=titulo,
        url=f"{SERVIDOR_URL}/{modo}",
        width=580,
        height=640,
        resizable=True,
    )
    webview.start(private_mode=False)