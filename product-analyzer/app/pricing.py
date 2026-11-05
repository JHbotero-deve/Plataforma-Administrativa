"""Customer offer rules; internal costs and margins never belong in public responses."""
import math

MIN_CUSTOMER_DISCOUNT_PCT = 40.0


def build_customer_offer(cost_price, reference_price=None, discount_pct=MIN_CUSTOMER_DISCOUNT_PCT):
    try:
        cost = float(cost_price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cost) or cost <= 0:
        return None

    try:
        reference = float(reference_price) if reference_price is not None else None
    except (TypeError, ValueError):
        reference = None

    if reference is None or reference <= cost:
        reference = round(cost / 0.60, 2)

    calc_discount = round((reference - cost) / reference * 100, 2)
    final_discount = max(calc_discount, MIN_CUSTOMER_DISCOUNT_PCT)
    profit = round(cost * 0.15, 2)

    return {
        "sale_price": cost,
        "cost_price": cost,
        "discount_pct": final_discount,
        "profit_amount": profit,
        "profit_margin_pct": 15.0,
    }


def publish_discounted_offer(conn, product_id, product, opportunity_score=None):
    with conn.cursor() as cur:
        cur.execute("SELECT storefront_excluded FROM products WHERE id = %s", (product_id,))
        exclusion = cur.fetchone()
    if exclusion and exclusion.get("storefront_excluded") is True:
        return None

    with conn.cursor() as cur:
        cur.execute("UPDATE products SET is_active = TRUE WHERE id = %s", (product_id,))
    conn.commit()

    price = product.get("current_price") or product.get("price")
    prev_price = product.get("previous_price") or product.get("original_price")
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
