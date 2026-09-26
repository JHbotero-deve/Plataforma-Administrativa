# Central

## Arquitectura

- Frontend estático en Vercel desde `product-analyzer/frontend`.
- API y worker en Railway desde `product-analyzer/app`.
- PostgreSQL como persistencia.
- Mercado Libre Colombia como fuente automática.
- Telegram para consulta y alertas.
- Wompi preparado para pagos.

## Flujo

Mercado Libre -> ingesta cada 2 horas -> PostgreSQL -> scoring -> API -> Vercel.

Los productos activos caducan a las 48 horas y la detección posterior renueva su vigencia.

## Datos

No hay productos de demostración ni valores de respaldo ficticios. Si Mercado Libre no devuelve productos reales, el catálogo no incorpora registros nuevos.
