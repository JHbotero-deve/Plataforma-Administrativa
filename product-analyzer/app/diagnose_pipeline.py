import os
from dotenv import load_dotenv
import requests
from db import get_connection

load_dotenv()

def check_env():
    print("--- 🔍 Verificando Variables de Entorno ---")
    required = [
        'DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD',
        'AMAZON_CREDENTIAL_ID', 'AMAZON_CREDENTIAL_SECRET', 'AMAZON_PARTNER_TAG',
        'TIKTOK_APP_KEY', 'TIKTOK_APP_SECRET', 'TIKTOK_ACCESS_TOKEN', 'TIKTOK_SHOP_CIPHER',
        'BOT_TOKEN', 'CHAT_ID'
    ]
    all_ok = True
    for var in required:
        val = os.getenv(var)
        if val and val != 'tu_api_key_aqui' and 'tu_' not in val:
            print(f"✅ {var}: Configurado")
        else:
            print(f"❌ {var}: FALTANTE o VALOR POR DEFECTO")
            all_ok = False
    return all_ok

def check_db():
    print("\n--- 🗄️ Verificando Conexión a Base de Datos ---")
    try:
        conn = get_connection()
        if conn:
            print("✅ Base de Datos: Conectada exitosamente")
            conn.close()
            return True
    except Exception as e:
        print(f"❌ Base de Datos: Error de conexión -> {e}")
    return False

def check_connectivity():
    print("\n--- 🌐 Verificando Conectividad de APIs ---")
    tests = {
        "Mercado Libre": "https://api.mercadolibre.com/sites",
        "Telegram": "https://api.telegram.org",
        "Amazon/General": "https://www.amazon.com"
    }
    for name, url in tests.items():
        try:
            r = requests.get(url, timeout=5)
            if r.ok:
                print(f"✅ {name}: Alcanzable")
            else:
                print(f"⚠️ {name}: Responde pero con error {r.status_code}")
        except Exception as e:
            print(f"❌ {name}: No alcanzable -> {e}")

if __name__ == '__main__':
    print("🚀 INICIANDO DIAGNÓSTICO DEL PIPELINE DE DATOS\n")
    env_ok = check_env()
    db_ok = check_db()
    check_connectivity()
    
    print("\n-------------------------------------------")
    if env_ok and db_ok:
        print("🎉 RESULTADO: El pipeline parece estar listo para operar.")
    else:
        print("⚠️ RESULTADO: Hay fallas críticas. Por favor, revisa el archivo .env")
    print("-------------------------------------------")

