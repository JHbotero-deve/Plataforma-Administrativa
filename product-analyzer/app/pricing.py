"""Customer offer rules; internal costs and margins never belong in public responses."""
import math

MIN_CUSTOMER_DISCOUNT_PCT = 40.0


def build_customer_offer(cost_price, reference_price=None, discount_pct=MIN_CUSTOMER_DISCOUNT_PCT):
    try:
        cost = float(cost_price)
        reference = float(reference_price)
        discount = max(float(discount_pct), MIN_CUSTOMER_DISCOUNT_PCT)
    except (TypeError, ValueError):
        return None

    if not all(math.isfinite(value) for value in (cost, reference, discount)):
        return None
    if cost <= 0 or reference <= 0 or discount >= 100:
        return None

    sale_price = round(reference * (1 - discount / 100), 2)
    profit = round(sale_price - cost, 2)
    if sale_price <= cost or profit <= 0:
        return None

    return {
        "sale_price": sale_price,
        "cost_price": cost,
        "discount_pct": round((reference - sale_price) / reference * 100, 2),
        "profit_amount": profit,
        "profit_margin_pct": round(profit / sale_price * 100, 2),
    }


def publish_discounted_offer(conn, product_id, product, opportunity_score=None):
    with conn.cursor() as cur:
        cur.execute("SELECT storefront_excluded FROM products WHERE id = %s", (product_id,))
        exclusion = cur.fetchone()
    if exclusion and exclusion.get("storefront_excluded") is True:
        return None

    price = product.get("current_price")
    if price is None:
        price = product.get("price")
    prev_price = product.get("previous_price")
    if prev_price is None:
        prev_price = product.get("original_price")
    offer = build_customer_offer(price, prev_price)
    image = product.get("image_url") or product.get("image")
    if not offer or not product.get("title") or not image:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE published_cards SET is_published = FALSE, sale_price = NULL, cost_price = NULL, profit_amount = NULL, profit_margin_pct = NULL, price_display = NULL, updated_at = NOW() WHERE product_id = %s",
                (product_id,),
            )
        conn.commit()
        return None

    with conn.cursor() as cur:
        cur.execute("UPDATE products SET is_active = TRUE WHERE id = %s", (product_id,))

    currency = str(product.get("currency") or "USD").upper()
    price_display = f"{offer['sale_price']:,.2f} {currency}"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO published_cards (
                product_id, title, subtitle, price_display, image_url, product_url,
                sale_price, cost_price, profit_amount, profit_margin_pct,
                opportunity_score, footer, accent, is_published, published_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NOW(),NOW())
            ON CONFLICT (product_id) DO UPDATE SET
                title=EXCLUDED.title, subtitle=EXCLUDED.subtitle,
                price_display=EXCLUDED.price_display, image_url=EXCLUDED.image_url,
                product_url=EXCLUDED.product_url, sale_price=EXCLUDED.sale_price,
                cost_price=EXCLUDED.cost_price, profit_amount=EXCLUDED.profit_amount,
                profit_margin_pct=EXCLUDED.profit_margin_pct,
                opportunity_score=EXCLUDED.opportunity_score,
                footer=EXCLUDED.footer, is_published=TRUE,
                published_at=NOW(), updated_at=NOW()
            """,
            (
                product_id, product["title"], f"{offer['discount_pct']:.0f}% de descuento",
                price_display, image, product.get("product_url") or product.get("affiliate_url"),
                offer["sale_price"], offer["cost_price"], offer["profit_amount"],
                offer["profit_margin_pct"], opportunity_score or 0,
                "Oferta Central · descuento para clientes", "#b6f23a",
            ),
        )
    conn.commit()
    return offer


def publish_catalog_product(conn, product_id, product, opportunity_score=None, commit=True):
    """Publica productos externos a su precio real, sin inventar descuentos ni costes."""
    with conn.cursor() as cur:
        cur.execute("SELECT storefront_excluded FROM products WHERE id = %s", (product_id,))
        exclusion = cur.fetchone()
    if exclusion and exclusion.get("storefront_excluded") is True:
        return None
    title = str(product.get("title") or "").strip()
    image = product.get("image_url") or product.get("image")
    product_url = product.get("product_url") or product.get("affiliate_url")
    try:
        price = float(product.get("current_price") if product.get("current_price") is not None else product.get("price"))
    except (TypeError, ValueError):
        return None
    if not title or title.casefold().startswith("producto ") or not image or not product_url or not math.isfinite(price) or price <= 0:
        return None
    currency = str(product.get("currency") or "USD").upper()
    platform = str(product.get("platform") or "marketplace").replace("_", " ").title()
    with conn.cursor() as cur:
        cur.execute("UPDATE products SET is_active = TRUE WHERE id = %s", (product_id,))
        cur.execute("""
            INSERT INTO published_cards (
                product_id, title, subtitle, price_display, image_url, product_url,
                sale_price, cost_price, profit_amount, profit_margin_pct,
                opportunity_score, footer, accent, is_published, published_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,%s,%s,%s,TRUE,NOW(),NOW())
            ON CONFLICT (product_id) DO UPDATE SET
                title=EXCLUDED.title, subtitle=EXCLUDED.subtitle,
                price_display=EXCLUDED.price_display, image_url=EXCLUDED.image_url,
                product_url=EXCLUDED.product_url, sale_price=EXCLUDED.sale_price,
                cost_price=NULL, profit_amount=NULL, profit_margin_pct=NULL,
                opportunity_score=EXCLUDED.opportunity_score,
                footer=EXCLUDED.footer, is_published=TRUE, updated_at=NOW()
        """, (
            product_id, title, f"Producto de {platform}",
            f"{price:,.2f} {currency}", image, product_url, price,
            opportunity_score or 0, "Precio de referencia del comercio · compra externa", "#b6f23a",
        ))
    if commit:
        conn.commit()
    return {"sale_price": price, "discount_pct": None, "publication_type": "catalog"}
