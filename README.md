# Central

Radar de productos enfocado en Mercado Libre Colombia.

## Arquitectura

- Vercel: frontend estático.
- Railway: API y worker.
- PostgreSQL: persistencia.
- Mercado Libre: fuente de productos.
- Telegram: bot y alertas.
- Wompi: integración de pagos preparada.

## Operación

El worker ejecuta una ingesta al iniciar y después cada 2 horas. Los productos activos caducan a las 48 horas y la detección posterior renueva su vigencia.

El frontend consume la API mediante `/api`.

## Endpoints

- `GET /api/v1/health`
- `GET /api/v1/pipeline/summary`
- `GET /api/v1/products`
- `GET /api/v1/comparison`
- `GET /api/v1/opportunities/top`

## Seguridad

Todas las credenciales se cargan mediante variables de entorno. No se guardan secretos en Git.
