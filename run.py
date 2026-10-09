"""Spustí Lumen. Použití:  python run.py"""
import os
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    if host in ("127.0.0.1", "localhost"):
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run("backend.main:app", host=host, port=port)
