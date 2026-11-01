"""Búsqueda oficial de promociones y revisión antes de avisar por Telegram."""
import hashlib
import math
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

from affiliate_links import aliexpress_api_request
from analysis import score_product
from db import get_connection, upsert_product
from notifications import send_telegram_alert


SEARCH_BUCKETS = (
    ("ropa", "ropa"),
    ("calzado", "zapatos"),
    ("accesorios", "accesorios"),
)
PRODUCT_FIELDS = ",".join(
    (
        "product_id",
        "product_title",
        "product_detail_url",
        "promotion_link",
        "product_main_image_url",
        "product_small_image_urls",
        "target_sale_price",
        "target_sale_price_currency",
        "original_price",
        "original_price_currency",
        "discount",
        "evaluate_rate",
        "lastest_volume",
        "commission_rate",
        "hot_product_commission_rate",
        "relevant_market_commission_rate",
        "promo_code_info",
    )
)


def _env(name):
    return os.getenv(name, "").strip()


def _number(value):
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentage(value):
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    has_percent = raw.endswith("%")
    number = _number(raw.rstrip("%"))
    if number is None or number < 0:
        return None
    return number * 100 if not has_percent and 0 < number <= 1 else number


def _official_url(value, *, image=False):
    try:
        parsed = urlsplit(str(value or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return None
    allowed_suffixes = (
        (".alicdn.com", ".aliexpress-media.com")
        if image
        else (".aliexpress.com", ".aliexpress.us", ".aliexpress.ru", ".alii.pub")
    )
    allowed_hosts = (
        {"alicdn.com", "aliexpress-media.com"}
        if image
        else {"aliexpress.com", "aliexpress.us", "aliexpress.ru", "alii.pub"}
    )
    return parsed.geturl() if host in allowed_hosts or host.endswith(allowed_suffixes) else None


def _product_results(payload):
    root = payload.get("aliexpress_affiliate_product_query_response", payload)
    response = root.get("resp_result", root) if isinstance(root, dict) else {}
    if not isinstance(response, dict):
        raise RuntimeError("AliExpress devolvió una respuesta de búsqueda inválida.")
    code = response.get("resp_code")
    if code not in (None, "", 0, "0", 200, "200"):
        raise RuntimeError(
            "AliExpress rechazó la búsqueda de promociones "
            f"({code}: {response.get('resp_msg') or 'sin descripción'})."
        )
    result = response.get("result") or {}
    products = result.get("products") or []
    if isinstance(products, dict):
        products = products.get("product") or products.get("products") or []
    if isinstance(products, dict):
        products = [products]
    if not isinstance(products, list):
        raise RuntimeError("AliExpress devolvió una lista de productos inválida.")
    return products


def _normalize_product(raw, category):
    if not isinstance(raw, dict):
        return None
    product_id = str(raw.get("product_id") or "").strip()
    title = str(raw.get("product_title") or "").strip()
    product_url = _official_url(raw.get("product_detail_url"))
    promotion_url = _official_url(raw.get("promotion_link"))
    sale_price = _number(raw.get("target_sale_price") or raw.get("sale_price"))
    original_price = _number(
        raw.get("target_original_price") or raw.get("original_price")
    )
    discount = _percentage(raw.get("discount"))
    volume = _number(raw.get("lastest_volume"))
    quality = _percentage(raw.get("evaluate_rate"))
    commission = _percentage(
        raw.get("relevant_market_commission_rate")
        or raw.get("hot_product_commission_rate")
        or raw.get("commission_rate")
    )
    image = _official_url(raw.get("product_main_image_url"), image=True)
    currency = str(
        raw.get("target_sale_price_currency")
        or raw.get("sale_price_currency")
        or "USD"
    ).strip().upper()
    if (
        not product_id
        or not title
        or not product_url
        or not promotion_url
        or not image
        or sale_price is None
        or sale_price <= 0
        or discount is None
        or volume is None
        or quality is None
        or commission is None
    ):
        return None

    promo = raw.get("promo_code_info") or {}
    promo_code = (
        str(promo.get("promo_code") or "").strip()
        if isinstance(promo, dict)
        else ""
    )
    promo_url = (
        _official_url(promo.get("code_promotionurl"))
        if isinstance(promo, dict)
        else None
    )
    affiliate_url = promo_url or promotion_url
    gallery = raw.get("product_small_image_urls") or []
    if isinstance(gallery, dict):
        gallery = gallery.get("string") or gallery.get("url") or []
    if isinstance(gallery, str):
        gallery = [gallery]
    if not isinstance(gallery, list):
        gallery = []
    return {
        "platform": "aliexpress",
        "external_id": product_id,
        "title": title[:500],
        "image_url": image,
        "gallery_urls": [
            safe_url
            for url in gallery
            if (safe_url := _official_url(url, image=True))
        ][:8],
        "product_url": product_url,
        "affiliate_url": affiliate_url,
        "price": sale_price,
        "currency": currency[:10],
        "rating": None,
        "reviews_count": 0,
        "sales_estimate": int(volume),
        "category": category,
        "discount": round(discount, 2),
        "original_price": original_price,
        "quality": round(quality, 2),
        "commission_rate": round(commission, 2),
        "promo_code": promo_code[:60],
        "promotion_score": _promotion_score(discount, volume, quality, commission),
        "source_metadata": {
            "import_method": "official_aliexpress_affiliate_api",
            "search_query": category,
            "discount_pct": round(discount, 2),
            "original_price": original_price,
            "evaluation_rate_pct": round(quality, 2),
            "last_30_days_volume": int(volume),
            "commission_rate_pct": round(commission, 2),
            "promo_code": promo_code[:60] or None,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _promotion_score(discount, volume, quality, commission):
    discount_score = min(100, discount * 2)
    volume_score = min(100, volume / 5)
    commission_score = min(100, commission * 10)
    return round(
        discount_score * 0.4
        + quality * 0.3
        + volume_score * 0.2
        + commission_score * 0.1,
        2,
    )


def _review_product(product):
    minimum_discount = _number(_env("PROMOTION_MIN_DISCOUNT") or 30)
    minimum_volume = _number(_env("PROMOTION_MIN_30D_VOLUME") or 100)
    minimum_quality = _number(
        _env("PROMOTION_MIN_EVALUATION")
        or _env("PROMOTION_MIN_EVALUATE_RATE")
        or 90
    )
    minimum_commission = _number(_env("PROMOTION_MIN_COMMISSION") or 0)
    minimum_score = _number(_env("PROMOTION_MIN_SCORE") or 55)
    return (
        product["discount"] >= minimum_discount
        and product["sales_estimate"] >= minimum_volume
        and product["quality"] >= minimum_quality
        and product["commission_rate"] > 0
        and product["commission_rate"] >= minimum_commission
        and product["promotion_score"] >= minimum_score
    )


def _alert_fingerprint(product):
    values = "|".join(
        (
            product["external_id"],
            str(product["price"]),
            str(product["discount"]),
            str(product["commission_rate"]),
            product["affiliate_url"],
            product["promo_code"],
        )
    )
    return hashlib.sha256(values.encode("utf-8")).hexdigest()


def _was_alerted(conn, product_id, fingerprint):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fingerprint FROM promotion_alerts WHERE product_id=%s",
            (product_id,),
        )
        row = cur.fetchone()
    return bool(row and row["fingerprint"] == fingerprint)


def _record_alert(conn, product_id, fingerprint):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO promotion_alerts(product_id,fingerprint,last_sent_at)
               VALUES(%s,%s,NOW())
               ON CONFLICT(product_id) DO UPDATE SET
                 fingerprint=EXCLUDED.fingerprint,last_sent_at=NOW()""",
            (product_id, fingerprint),
        )
    conn.commit()


def _category_average(conn, category, currency, price):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT AVG(p.current_price) AS average
               FROM products p
               JOIN platforms pl ON pl.id=p.platform_id
               JOIN categories c ON c.id=p.category_id
               WHERE pl.name='aliexpress' AND c.name=%s
                 AND p.currency=%s AND p.current_price IS NOT NULL
                 AND p.is_active=TRUE""",
            (category, currency),
        )
        row = cur.fetchone()
    return float((row or {}).get("average") or price)


def _search_bucket(category, keyword):
    payload = aliexpress_api_request(
        "aliexpress.affiliate.product.query",
        {
            "keywords": keyword,
            "page_no": "1",
            "page_size": "50",
            "sort": "LAST_VOLUME_DESC",
            "target_currency": "USD",
            "target_language": "ES",
            "ship_to_country": "CO",
            "tracking_id": _env("ALIEXPRESS_TRACKING_ID")
            or _env("ALIEXPRESS_AFFILIATE_TRACKING_ID"),
            "fields": PRODUCT_FIELDS,
        },
    )
    return [
        normalized
        for raw in _product_results(payload)
        if (normalized := _normalize_product(raw, category)) is not None
        and _review_product(normalized)
    ]


def run_promotion_cycle(max_alerts=5):
    tracking_id = _env("ALIEXPRESS_TRACKING_ID") or _env(
        "ALIEXPRESS_AFFILIATE_TRACKING_ID"
    )
    if (
        not _env("ALIEXPRESS_APP_KEY")
        or not _env("ALIEXPRESS_APP_SECRET")
        or not tracking_id
    ):
        raise RuntimeError(
            "Configura ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET y "
            "ALIEXPRESS_TRACKING_ID para buscar promociones."
        )

    candidates = {}
    queried = 0
    for category, keyword in SEARCH_BUCKETS:
        products = _search_bucket(category, keyword)
        queried += len(products)
        for product in products:
            current = candidates.get(product["external_id"])
            if not current or product["promotion_score"] > current["promotion_score"]:
                candidates[product["external_id"]] = product

    ranked = sorted(
        candidates.values(),
        key=lambda product: product["promotion_score"],
        reverse=True,
    )
    conn = get_connection()
    approved = 0
    alerts = 0
    try:
        for product in ranked:
            if alerts >= max_alerts:
                break
            product_id = upsert_product(
                conn, "aliexpress", product["category"], product
            )
            approved += 1
            average = _category_average(
                conn, product["category"], product["currency"], product["price"]
            )
            score_product(conn, product_id, average)
            fingerprint = _alert_fingerprint(product)
            if _was_alerted(conn, product_id, fingerprint):
                continue
            sent = send_telegram_alert(
                {
                    "title": product["title"],
                    "price": product["price"],
                    "score": product["promotion_score"],
                    "platform": "AliExpress",
                    "category": product["category"],
                    "discount": product["discount"],
                    "commission_rate": product["commission_rate"],
                    "sales_estimate": product["sales_estimate"],
                    "promo_code": product["promo_code"],
                    "url": product["affiliate_url"],
                    "affiliate_link_generated": True,
                }
            )
            if sent:
                _record_alert(conn, product_id, fingerprint)
                alerts += 1
            else:
                print(
                    "[promotions] Telegram no confirmó el aviso; "
                    f"se reintentará en el siguiente ciclo (producto {product_id})."
                )
    finally:
        conn.close()
    return {"queried": queried, "approved": approved, "alerts": alerts}
