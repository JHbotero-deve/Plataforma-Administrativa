# Central

Central es un radar de oportunidades de productos reales. El sistema ingiere fuentes comerciales, persiste los productos en PostgreSQL, calcula una señal de oportunidad y expone el catálogo mediante una interfaz web.

## Arquitectura

- Vercel: frontend estático.
- Railway: API, worker y bot de Telegram.
- PostgreSQL: persistencia del catálogo, histórico y monetización.
- Mercado Libre Colombia: ingesta automática.
- Amazon: importación por URL y lote inicial configurado.
- TikTok Shop Creator: integración preparada para productos del Showcase.
- Telegram: alertas y bot interactivo.
- Wompi: checkout y recepción de eventos preparados.

## Ciclo del catálogo

El worker ejecuta una ingesta al iniciar y después cada 2 horas. Los productos activos caducan a las 48 horas. Cuando un producto vuelve a detectarse, su registro se actualiza y su vigencia se renueva otras 48 horas.

## Frontend

El frontend se sirve desde `product-analyzer/frontend` mediante Vercel. Consume la API versionada a través de `/api`, que Vercel redirige al servicio Railway.

Funciones disponibles:

- oportunidades ordenadas por score;
- catálogo de productos reales;
- importación de Amazon y Mercado Libre mediante URL;
- imágenes reales cuando la fuente las proporciona;
- visualización 3D procedimental como apoyo visual;
- estado de fuentes y vigencia del catálogo.

No existe fallback de productos ficticios en producción.

## API

- `GET /api/v1/health`
- `GET /api/v1/pipeline/summary`
- `GET /api/v1/products`
- `POST /api/v1/products/import-url`
- `GET /api/v1/products/{id}`
- `PATCH /api/v1/products/{id}/model`
- `GET /api/v1/comparison`
- `GET /api/v1/opportunities/top`
- `GET /api/v1/monetize/plans`
- `POST /api/v1/wompi/checkout/subscription`
- `POST /api/v1/wompi/events`

## Desarrollo local

Desde `product-analyzer`:

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

Servicios locales:

- Frontend: http://localhost:3000
- API: http://localhost:8000
- Adminer: http://localhost:8080
- PostgreSQL: localhost:5435

Si Docker Desktop no está ejecutando el motor Linux, `docker compose` no podrá iniciar los servicios.

## Seguridad

Las credenciales se cargan mediante variables de entorno y no se guardan en Git. No colocar tokens, secretos de Wompi, claves privadas de plataformas ni credenciales de base de datos en archivos versionados.
