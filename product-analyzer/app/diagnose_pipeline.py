import os

import requests
from dotenv import load_dotenv

from db import get_connection

load_dotenv()

REQUIRED_ENV = [
    "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD",
    "AMAZON_CREDENTIAL_ID", "AMAZON_CREDENTIAL_SECRET", "AMAZON_PARTNER_TAG",
    "TIKTOK_APP_KEY", "TIKTOK_APP_SECRET", "TIKTOK_ACCESS_TOKEN", "TIKTOK_SHOP_CIPHER",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
]


def check_env():
    print("--- Verificando variables de entorno ---")
    all_ok = True
    for name in REQUIRED_ENV:
        value = os.getenv(name, "").strip()
        configured = bool(value) and not value.startswith("tu_")
        print(f"{name}: {'configurado' if configured else 'faltante'}")
        all_ok = all_ok and configured
    return all_ok


def check_db():
    print("\n--- Verificando conexión a base de datos ---")
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        print("Base de datos: conectada")
        return True
    except Exception as exc:
        print(f"Base de datos: error de conexión -> {exc}")
        return False


def check_connectivity():
    print("\n--- Verificando conectividad externa ---")
    tests = {
        "Mercado Libre": "https://api.mercadolibre.com/sites",
        "Telegram": "https://api.telegram.org",
        "Amazon": "https://www.amazon.com",
    }
    results = {}
    for name, url in tests.items():
        try:
            response = requests.get(url, timeout=5)
            results[name] = response.ok
            print(f"{name}: {'alcanzable' if response.ok else f'HTTP {response.status_code}'}")
        except requests.RequestException as exc:
            results[name] = False
            print(f"{name}: no alcanzable -> {exc}")
    return all(results.values())


if __name__ == "__main__":
    env_ok = check_env()
    db_ok = check_db()
    connectivity_ok = check_connectivity()
    print("\nResultado:", "LISTO" if env_ok and db_ok and connectivity_ok else "REVISAR CONFIGURACIÓN")
