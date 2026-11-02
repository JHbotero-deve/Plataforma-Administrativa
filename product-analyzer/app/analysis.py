def compute_price_score(price: float, category_avg_price: float, previous_price=None) -> float:
    market_score = 50.0
    if category_avg_price and category_avg_price > 0:
        ratio = price / category_avg_price
        market_score = max(0, min(100, (2 - ratio) * 50))
    if previous_price is not None and previous_price > price > 0:
        verified_discount = min(1.0, (previous_price - price) / previous_price / 0.5)
        market_score += (100 - market_score) * verified_discount * 0.4
    return round(max(0, min(100, market_score)), 2)


def compute_demand_score(rating, reviews_count, sales_estimate) -> float:
    rating = float(rating or 0)
    reviews_count = float(reviews_count or 0)
    sales_estimate = float(sales_estimate or 0)
    if rating <= 0 and reviews_count <= 0 and sales_estimate <= 0:
        return 50.0

    rating_component = (rating / 5) * 40          # hasta 40 puntos
    reviews_component = min(reviews_count / 5, 30)  # hasta 30 puntos
    sales_component = min(sales_estimate / 10, 30)  # hasta 30 puntos

    return round(rating_component + reviews_component + sales_component, 2)


def compute_trend_score(price_history: list) -> float:
    """price_history: lista de precios ordenada de más viejo a más nuevo."""
    if len(price_history) < 2:
        return 50.0  # sin histórico suficiente, score neutral

    first, last = float(price_history[0]), float(price_history[-1])
    if first == 0:
        return 50.0

    variation = (first - last) / first  # positivo si el precio bajó
    score = 50 + (variation * 100)
    return round(max(0, min(100, score)), 2)


def compute_opportunity_score(price_score, demand_score, trend_score, weights=(0.4, 0.4, 0.2)) -> float:
    w_price, w_demand, w_trend = weights
    return round(
        price_score * w_price + demand_score * w_demand + trend_score * w_trend, 2
    )


def score_product(conn, product_id: int, category_avg_price: float):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current_price, previous_price, rating, reviews_count, sales_estimate "
            "FROM products WHERE id = %s", (product_id,)
        )
        p = cur.fetchone()

        cur.execute(
            "SELECT price FROM price_history WHERE product_id = %s "
            "ORDER BY recorded_at ASC", (product_id,)
        )
        history = [row["price"] for row in cur.fetchall()]

    price_score = compute_price_score(float(p["current_price"]), category_avg_price, p["previous_price"])
    demand_score = compute_demand_score(p["rating"], p["reviews_count"], p["sales_estimate"])
    trend_score = compute_trend_score(history)
    opportunity_score = compute_opportunity_score(price_score, demand_score, trend_score)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO product_scores (product_id, price_score, demand_score,
                trend_score, opportunity_score, calculated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (product_id) DO UPDATE SET
                price_score = EXCLUDED.price_score,
                demand_score = EXCLUDED.demand_score,
                trend_score = EXCLUDED.trend_score,
                opportunity_score = EXCLUDED.opportunity_score,
                calculated_at = NOW();
            """,
            (product_id, price_score, demand_score, trend_score, opportunity_score),
        )
    conn.commit()
    return opportunity_score

