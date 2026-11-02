"""Customer offer rules; internal costs and margins never belong in public responses."""
import math

MIN_CUSTOMER_DISCOUNT_PCT = 40.0


def build_customer_offer(cost_price, reference_price, discount_pct=MIN_CUSTOMER_DISCOUNT_PCT):
    try:
        cost = float(cost_price)
        reference = float(reference_price)
        discount = float(discount_pct)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (cost, reference, discount)):
        return None
    if cost <= 0 or reference <= 0 or not 0 < discount < 100 or reference <= cost:
        return None
    sale_price = round(reference * (1 - discount / 100), 2)
    if sale_price <= cost:
        return None
    profit = round(sale_price - cost, 2)
    return {
        "sale_price": sale_price,
        "cost_price": round(cost, 2),
        "discount_pct": round((reference - sale_price) / reference * 100, 2),
        "profit_amount": profit,
        "profit_margin_pct": round(profit / sale_price * 100, 2),
    }


def publish_discounted_offer(conn, product_id, product, opportunity_score=None):
    offer = build_customer_offer(product.get("current_price"), product.get("previous_price"))
    if not offer or not product.get("title") or not product.get("image_url"):
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
                price_display, product["image_url"], product.get("product_url"),
                offer["sale_price"], offer["cost_price"], offer["profit_amount"],
                offer["profit_margin_pct"], opportunity_score or 0,
                "Oferta Central · descuento para clientes", "#b6f23a",
            ),
        )
    conn.commit()
    return offer
