"""
Resuelve el paso de autorización OAuth de TikTok Shop para obtener el
ACCESS_TOKEN y el SHOP_ID que van en el .env.

Uso:
  1. Correr: python get_tiktok_token.py --step auth
     Te imprime una URL. Abrila en el navegador, autorizá la app contra tu
     tienda, y TikTok te va a redirigir a tu "redirect_uri" con un
     parámetro ?code=XXXX en la URL.

  2. Correr: python get_tiktok_token.py --step token --code XXXX
     Cambia ese código por el access_token y te muestra el shop_id.
     Copiá ambos valores al archivo .env.

Requiere tener ya App Key y App Secret (TIKTOK_APP_KEY / TIKTOK_APP_SECRET
en el .env), generados en partner.tiktokshop.com.
"""

import os
import time
import argparse
import requests

from tiktok_shop import _sign_request

AUTH_BASE = "https://services.tiktokshop.com/open/authorize"
TOKEN_URL = "https://auth.tiktok-shops.com/api/v2/token/get"
SHOPS_HOST = "https://open-api.tiktokglobalshop.com"
SHOPS_PATH = "/authorization/202309/shops"


def print_auth_url(redirect_uri: str):
    app_key = os.getenv("TIKTOK_APP_KEY")
    if not app_key:
        raise RuntimeError("Falta TIKTOK_APP_KEY en el .env")

    url = f"{AUTH_BASE}?app_key={app_key}&redirect_uri={redirect_uri}&state=setup"
    print("\nAbrí esta URL en el navegador, iniciá sesión con tu cuenta de TikTok "
          "Shop y autorizá la app:\n")
    print(url)
    print("\nTikTok te va a redirigir a tu redirect_uri con '?code=XXXX' al final. "
          "Copiá ese código y corré este script de nuevo con --step token --code XXXX\n")


def exchange_code_for_token(code: str):
    app_key = os.getenv("TIKTOK_APP_KEY")
    app_secret = os.getenv("TIKTOK_APP_SECRET")
    if not app_key or not app_secret:
        raise RuntimeError("Faltan TIKTOK_APP_KEY / TIKTOK_APP_SECRET en el .env")

    resp = requests.get(TOKEN_URL, params={
        "app_key": app_key,
        "app_secret": app_secret,
        "auth_code": code,
        "grant_type": "authorized_code",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    access_token = data.get("data", {}).get("access_token")
    if not access_token:
        print("Respuesta inesperada de TikTok:", data)
        return

    print(f"\nACCESS_TOKEN obtenido:\n{access_token}\n")

    # Con el token ya se puede consultar a qué tienda(s) quedó autorizado.
    # Este endpoint también requiere ir firmado (app_key + timestamp + sign),
    # no alcanza con mandar el access_token en el header.
    timestamp = str(int(time.time()))
    params = {"app_key": app_key, "timestamp": timestamp}
    params["sign"] = _sign_request(app_secret, SHOPS_PATH, params)
    params["access_token"] = access_token

    shops_resp = requests.get(
        SHOPS_HOST + SHOPS_PATH,
        params=params,
        headers={"x-tts-access-token": access_token},
        timeout=15,
    )
    shops_data = shops_resp.json()
    shops = shops_data.get("data", {}).get("shops", [])

    if shops:
        print("Tiendas autorizadas:")
        for s in shops:
            print(f"  - shop_id: {s.get('id')}  |  shop_cipher: {s.get('cipher')}  |  nombre: {s.get('name')}")
        print("\nCopiá el ACCESS_TOKEN y el shop_cipher correspondiente a tu .env "
              "(TIKTOK_ACCESS_TOKEN y TIKTOK_SHOP_CIPHER). El shop_id sirve solo "
              "de referencia: la API 202309 identifica la tienda por shop_cipher.")
    else:
        print("No se encontraron tiendas asociadas a este token:", shops_data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["auth", "token"], required=True)
    parser.add_argument("--redirect_uri", default="https://localhost/callback",
                         help="Debe coincidir con el configurado en tu app de TikTok Partner")
    parser.add_argument("--code", help="Código recibido en la redirección tras autorizar")
    args = parser.parse_args()

    if args.step == "auth":
        print_auth_url(args.redirect_uri)
    else:
        if not args.code:
            raise SystemExit("Falta --code. Usá el valor que viene en la URL de redirección.")
        exchange_code_for_token(args.code)

