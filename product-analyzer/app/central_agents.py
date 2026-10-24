"""Central agents: auditable product search, catalog QA, and truthful ad copy.

Run from product-analyzer/app:
  python central_agents.py search "audifonos bluetooth"
  python central_agents.py audit
  python central_agents.py ad-copy 123
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from urllib.parse import urlparse


ALLOWED_PLATFORMS = {"amazon", "tiktok", "aliexpress", "personal"}


def build_ad_copy(product: dict) -> dict:
    """Generate a conservative draft from stored facts; never invent discount claims."""
    title = re.sub(r"\s+", " ", str(product.get("title") or "Producto seleccionado")).strip()
    title = title[:180]
    platform = str(product.get("platform") or "tienda").strip()
    price = product.get("current_price", product.get("price"))
    currency = str(product.get("currency") or "COP")
    url = str(product.get("affiliate_url") or product.get("product_url") or "").strip()
    description = re.sub(r"\s+", " ", str(product.get("description") or "")).strip()
    previous = product.get("previous_price")
    discount = None
    try:
        if previous is not None and price is not None and float(previous) > float(price) > 0:
            discount = round((float(previous) - float(price)) / float(previous) * 100)
    except (TypeError, ValueError):
        discount = None

    headline = f"Descubre {title}"
    if discount is not None and discount > 0:
        headline = f"{title}: ahorra hasta {discount}%"
    price_text = ""
    try:
        if price is not None and float(price) > 0:
            price_text = f"Precio registrado: {float(price):,.2f} {currency}."
    except (TypeError, ValueError):
        pass
    facts = [item for item in (description[:240], price_text) if item]
    body = " ".join(facts) or "Consulta la ficha para conocer los detalles disponibles."
    cta = f"Consulta el producto: {url}" if urlparse(url).scheme in {"http", "https"} and urlparse(url).netloc else "Revisa los detalles antes de comprar."
    category = re.sub(r"\s+", " ", str(product.get("category") or "")).strip()
    seo_title = headline[:65]
    meta_description = re.sub(r"\s+", " ", f"{title}. {body}").strip()[:155]
    stop_words = {"para", "desde", "con", "una", "uno", "unos", "unas", "del", "por", "los", "las", "que", "the", "and", "with"}
    words = re.findall(r"[a-záéíóúñ0-9]{3,}", title.lower())
    keywords = list(dict.fromkeys(word for word in words if word not in stop_words))[:8]
    if category and category.lower() not in keywords:
        keywords.append(category.lower())
    social_caption = f"{headline}. {body} {cta}"
    return {
        "headline": headline,
        "body": body,
        "cta": cta,
        "seo_title": seo_title,
        "meta_description": meta_description,
        "keywords": keywords,
        "social_caption": social_caption[:500],
        "platform": platform,
        "discount_verified": discount is not None,
        "disclaimer": "Borrador publicitario. Verifica precio, disponibilidad y condiciones antes de publicar.",
    }


def audit_catalog(conn, limit: int = 1000) -> dict:
    """Audit active catalog records without mutating products or publishing anything."""
    limit = max(1, min(int(limit), 5000))
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id, p.title, p.current_price, p.previous_price, p.currency,
                   p.image_url, p.product_url, p.affiliate_url, p.is_active,
                   p.is_blocked, p.catalog_expires_at, pl.name AS platform,
                   (p.catalog_expires_at IS NOT NULL AND p.catalog_expires_at <= NOW()) AS expired,
                   (pc.id IS NOT NULL AND pc.is_published = TRUE) AS published
            FROM products p
            JOIN platforms pl ON pl.id = p.platform_id
            LEFT JOIN published_cards pc ON pc.product_id = p.id
            WHERE pl.name = ANY(%s)
            ORDER BY p.updated_at DESC NULLS LAST, p.id DESC
            LIMIT %s
            """,
            (sorted(ALLOWED_PLATFORMS), limit),
        )
        rows = cur.fetchall()

    issue_counts = Counter()
    issues = []
    seen_urls = {}
    for row in rows:
        product_id = row.get("id")
        title = str(row.get("title") or "").strip()
        price = row.get("current_price")
        image = str(row.get("image_url") or "").strip()
        product_url = str(row.get("affiliate_url") or row.get("product_url") or "").strip()
        checks = []
        if not title or len(title) < 4:
            checks.append("title_missing_or_short")
        try:
            if price is None or float(price) <= 0:
                checks.append("price_missing_or_invalid")
        except (TypeError, ValueError):
            checks.append("price_missing_or_invalid")
        if not image:
            checks.append("image_missing")
        elif urlparse(image).scheme not in {"http", "https"}:
            checks.append("image_url_invalid")
        parsed = urlparse(product_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            checks.append("product_url_invalid")
        url_key = product_url.split("?", 1)[0].rstrip("/").lower()
        if url_key and url_key in seen_urls:
            checks.append("possible_duplicate_url")
        elif url_key:
            seen_urls[url_key] = product_id
        if row.get("is_blocked"):
            checks.append("product_blocked")
        if row.get("expired"):
            checks.append("catalog_entry_expired")
        if row.get("published") and checks:
            checks.append("published_record_needs_review")
        for issue in checks:
            issue_counts[issue] += 1
        if checks:
            issues.append({"id": product_id, "title": title[:100], "platform": row.get("platform"), "issues": checks})

    return {
        "audited": len(rows),
        "with_issues": len(issues),
        "clean": len(rows) - len(issues),
        "issue_counts": dict(issue_counts),
        "issues": issues[:100],
        "truncated": len(issues) > 100,
        "mutated_data": False,
    }


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Agentes CLI de auditoría y contenido de Central")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Buscar productos reales en fuentes configuradas")
    search.add_argument("term", help="Término de búsqueda")
    search.add_argument("--limit", type=int, default=8)
    audit = sub.add_parser("audit", help="Auditar calidad del catálogo sin modificar datos")
    audit.add_argument("--limit", type=int, default=1000)
    ad = sub.add_parser("ad-copy", help="Generar borrador publicitario desde un producto existente")
    ad.add_argument("product_id", type=int)
    args = parser.parse_args(argv)

    if args.command == "search":
        from product_search import search_external_products
        _print_json({"query": args.term, "source_policy": "configured providers; respect rate limits; no access-control bypass", "products": search_external_products(args.term, args.limit)})
        return 0

    from db import get_connection
    conn = get_connection()
    try:
        if args.command == "audit":
            _print_json(audit_catalog(conn, args.limit))
            return 0
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.title, p.description, p.category, p.current_price, p.previous_price,
                       p.currency, p.product_url, p.affiliate_url, pl.name AS platform
                FROM products p JOIN platforms pl ON pl.id = p.platform_id
                WHERE p.id = %s AND p.is_active = TRUE AND p.is_blocked = FALSE
                """,
                (args.product_id,),
            )
            product = cur.fetchone()
        if not product:
            _print_json({"error": "Producto activo no encontrado", "product_id": args.product_id})
            return 2
        _print_json({"product_id": args.product_id, "ad_copy": build_ad_copy(product), "approved_for_publication": False})
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
