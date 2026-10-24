"""Pruebas de reglas de descuento del catálogo Central."""


def discount_percentage(current_price, previous_price):
    """Devuelve el descuento porcentual solo si ambos precios son válidos."""
    try:
        current = float(current_price)
        previous = float(previous_price)
    except (TypeError, ValueError):
        return None
    if current <= 0 or previous <= current:
        return None
    return round((previous - current) / previous * 100, 2)
