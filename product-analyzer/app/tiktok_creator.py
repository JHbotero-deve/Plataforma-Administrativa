import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import requests

from db import get_connection, upsert_product

API_BASE = "https://open-api.tiktokglobalshop.com"
SHOWCASE_PATH = "/affiliate_creator/202405/showcases/products"
PROFILE_PATH = "/affiliate_creator/202405/profiles"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable de entorno {name}")
    return value


def _sign(app_secret: str, path: str, params: dict, body: str = "") -> str:
    sign_params = {
        key: value
        for key, value in params.items()
        if key not in {"sign", "access_token", "x-tts-access-token"}
        and not isinstance(value, (list, dict))
    }
    canonical = "".join(f"{key}{sign_params[key]}" for key in sorted(sign_params))
    payload = f"{path}{canonical}{body}"
    wrapped = f"{app_secret}{payload}{app_secret}"
    return hmac.new(app_secret.encode(), wrapped.encode(), hashlib.sha256).hexdigest()


def _creator_params(extra: dict | None = None) -> dict:
    params = {
        "app_key": _required("TIKTOK_APP_KEY"),
        "timestamp": str(int(time.time())),
    }
    if extra:
        params.update({key: value for key, value in extra.items() if value is not None})
    params["sign"] = _sign(_required("TIKTOK_APP_SECRET"), SHOWCASE_PATH if "page_size" in params else PROFILE_PATH, params)
    return params


def _creator_get(path: str, params: dict) -> dict:
    token = _required("TIKTOK_CREATOR_ACCESS_TOKEN")
    signed = dict(params)
    signed["sign"] = _sign(_required("TIKTOK_APP_SECRET"), path, signed)
    response = requests.get(
        f"{API_BASE}{path}",
        params=signed,
        headers={
            "x-tts-access-token": token,
            "content-type": "application/json",
        },
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"TikTok Creator API HTTP {response.status_code}: {response.text[:800]}"
        )
    data = response.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(
            f"TikTok Creator API {data.get('code')}: {data.get('message', 'error')}"
        )
    return data


def get_creator_profile() -> dict:
    params = {
        "app_key": _required("TIKTOK_APP_KEY"),
        "timestamp": str(int(time.time())),
    }
    data = _creator_get(PROFILE_PATH, params)
    return data.get("data") or {}


def _money(value):
    if isinstance(value, dict):
        amount = value.get("amount") or value.get("minimum_amount") or value.get("sale_price")
        currency = value.get("currency") or value.get("currency_code") or os.getenv("TIKTOK_CURRENCY", "USD")
    else:
        amount = value
        currency = os.getenv("TIKTOK_CURRENCY", "USD")
    try:
        return float(amount), currency
    except (TypeError, ValueError):
        return None, currency


def _first_image(item: dict):
    addition = item.get("addition") or {}
    images = (
        addition.get("customized_main_images")
        or item.get("images")
        or item.get("main_images")
        or []
    )
    if images:
        first = images[0]
        return first.get("url") if isinstance(first, dict) else first
    return None


def _normalize(item: dict) -> dict | None:
    product_id = str(item.get("id") or item.get("product_id") or "").strip()
    title = str(item.get("title") or item.get("name") or "").strip()
    if not product_id or not title:
        return None

    price_data = item.get("price") or {}
    sale_price = price_data.get("sale_price") if isinstance(price_data, dict) else None
    original_price = price_data.get("original_price") if isinstance(price_data, dict) else price_data
    amount, currency = _money(sale_price or original_price or item.get("current_price"))

    commission = (
        item.get("commission_rate")
        or item.get("commissionRate")
        or (item.get("commission") or {}).get("rate")
        or (item.get("affiliate") or {}).get("commission_rate")
    )
    try:
        commission = float(commission) if commission is not None else None
    except (TypeError, ValueError):
        commission = None

    affiliate_url = (
        item.get("affiliate_url")
        or item.get("affiliate_link")
        or item.get("promotion_link")
        or item.get("product_promotion_link")
    )
    product_url = (
        item.get("product_url")
        or item.get("detail_url")
        or item.get("product_detail_url")
        or item.get("url")
        or affiliate_url
    )

    estimated_commission = None
    if amount is not None and commission is not None:
        estimated_commission = amount * commission / 100

    return {
        "external_id": product_id,
        "title": title,
        "price": amount,
        "currency": currency,
        "image_url": _first_image(item),
        "product_url": product_url,
        "rating": None,
        "reviews_count": 0,
        "sales_estimate": None,
        "affiliate_url": affiliate_url,
        "commission_rate": commission,
        "estimated_commission": estimated_commission,
        "source_metadata": {
            "tiktok_creator": True,
            "showcase": True,
            "raw": item,
        },
    }


def get_showcase_products(limit: int = 2000) -> list[dict]:
    products = []
    page_token = None
    target = max(1, min(limit, 2000))

    while len(products) < target:
        params = {
            "app_key": _required("TIKTOK_APP_KEY"),
            "timestamp": str(int(time.time())),
            "page_size": min(20, target - len(products)),
            "origin": "SHOWCASE",
        }
        if page_token:
            params["page_token"] = page_token

        data = _creator_get(SHOWCASE_PATH, params)
        payload = data.get("data") or {}
        for item in payload.get("products") or []:
            normalized = _normalize(item)
            if normalized:
                products.append(normalized)
                if len(products) >= target:
                    break

        page_token = payload.get("next_page_token")
        if not page_token or not (payload.get("products") or []):
            break

    print(f"[TikTok Shop Creator] Showcase sincronizado: {len(products)} productos.")
    return products


def sync_showcase(category: str = "accesorios", limit: int = 2000) -> dict:
    products = get_showcase_products(limit)
    conn = get_connection()
    synced = 0
    try:
        for product in products:
            product_id = upsert_product(conn, "tiktok", category, product)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tiktok_creator_products
                    (product_id, tiktok_product_id, showcase_status, affiliate_url,
                     commission_rate, estimated_commission, raw_data, synced_at, updated_at)
                    VALUES (%s,%s,'showcase',%s,%s,%s,%s,NOW(),NOW())
                    ON CONFLICT (product_id) DO UPDATE SET
                        tiktok_product_id=EXCLUDED.tiktok_product_id,
                        showcase_status=EXCLUDED.showcase_status,
                        affiliate_url=COALESCE(EXCLUDED.affiliate_url, tiktok_creator_products.affiliate_url),
                        commission_rate=COALESCE(EXCLUDED.commission_rate, tiktok_creator_products.commission_rate),
                        estimated_commission=COALESCE(EXCLUDED.estimated_commission, tiktok_creator_products.estimated_commission),
                        raw_data=EXCLUDED.raw_data,
                        synced_at=NOW(),
                        updated_at=NOW()
                    """,
                    (
                        product_id,
                        product["external_id"],
                        product.get("affiliate_url"),
                        product.get("commission_rate"),
                        product.get("estimated_commission"),
                        json.dumps(product.get("source_metadata", {}).get("raw", {})),
                    ),
                )
            synced += 1

        profile = get_creator_profile()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tiktok_creator_state
                (id, open_id, granted_scopes, user_type, last_sync_at, last_error, updated_at)
                VALUES (1,%s,%s,%s,NOW(),NULL,NOW())
                ON CONFLICT (id) DO UPDATE SET
                    open_id=EXCLUDED.open_id,
                    granted_scopes=EXCLUDED.granted_scopes,
                    user_type=EXCLUDED.user_type,
                    last_sync_at=NOW(),
                    last_error=NULL,
                    updated_at=NOW()
                """,
                (
                    profile.get("open_id") or profile.get("creator_user_id"),
                    json.dumps(profile.get("granted_scopes") or []),
                    profile.get("user_type"),
                ),
            )
        conn.commit()
        return {"synced": synced, "profile": profile}
    except Exception as exc:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tiktok_creator_state (id, last_error, updated_at)
                VALUES (1,%s,NOW())
                ON CONFLICT (id) DO UPDATE SET last_error=EXCLUDED.last_error, updated_at=NOW()
                """,
                (str(exc)[:1000],),
            )
        conn.commit()
        raise
    finally:
        conn.close()


def creator_configured() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in ("TIKTOK_APP_KEY", "TIKTOK_APP_SECRET", "TIKTOK_CREATOR_ACCESS_TOKEN")
    )


__all__ = [
    "creator_configured",
    "get_creator_profile",
    "get_showcase_products",
    "sync_showcase",
]
