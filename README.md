# Radar de Producto

Radar de Producto es una plataforma de análisis de oportunidades comerciales que reúne productos de Mercado Libre, Amazon y TikTok Shop, los normaliza en PostgreSQL y calcula un score de oportunidad para facilitar decisiones de promoción y reventa.

## Arquitectura actual

- **Frontend:** HTML/CSS/JavaScript en `product-analyzer/frontend`, publicado en Vercel.
- **Backend:** FastAPI + worker de ingesta en `product-analyzer/app`, publicado como contenedor en Railway.
- **Base de datos:** PostgreSQL.
- **Fuentes:** Mercado Libre (búsqueda pública), Amazon Creators API y TikTok Shop Open API para la tienda autorizada.
- **Monetización:** seguimiento de clics, estructura para dropshipping y suscripciones SaaS.

## Flujo de producción

```
Mercado Libre / Amazon / TikTok Shop
                ↓
          pipeline Python
                ↓
            PostgreSQL
                ↓
        FastAPI /products
        FastAPI /opportunities/top
        FastAPI /comparison
                ↓
             Vercel
```

El frontend **no contiene productos de ejemplo como respaldo**. Cuando una fuente falla, se informa el estado de la API y no se inventan productos.

## Estructura

```
Central/
├── Dockerfile
├── netlify.toml
├── .github/workflows/
└── product-analyzer/
    ├── app/
    │   ├── api.py
    │   ├── amazon_creators.py
    │   ├── db.py
    │   ├── diagnose_pipeline.py
    │   ├── ingest.py
    │   ├── init_db.py
    │   ├── main.py
    │   ├── monetization.py
    │   ├── notifications.py
    │   ├── tiktok_shop.py
    │   └── sql/
    ├── frontend/
    │   ├── index.html
    │   └── _redirects
    ├── docker-compose.yml
    └── .env.example
```

## Desarrollo local

1. Copia `.env.example` como `.env` y completa las credenciales.
2. Ejecuta:

```bash
cd product-analyzer
docker compose up --build -d
```

3. Dashboard: `http://localhost:3000`
4. API: `http://localhost:8000`
5. Salud del backend: `http://localhost:8000/health`
6. Resumen del pipeline: `http://localhost:8000/pipeline/summary`

## Producción

### Railway

El servicio backend usa el `Dockerfile` de la raíz del repositorio. Railway debe apuntar al repositorio `JHbotero-deve/Central`, rama `main` y directorio raíz `/`.

Configura las variables de entorno de producción en Railway. **Nunca subas el archivo `.env` ni credenciales reales al repositorio.**

Variables importantes:

- `DATABASE_URL`
- Amazon: `AMAZON_CREDENTIAL_ID`, `AMAZON_CREDENTIAL_SECRET`, `AMAZON_CREDENTIAL_VERSION`, `AMAZON_PARTNER_TAG`, `AMAZON_MARKETPLACE`
- TikTok Shop: `TIKTOK_APP_KEY`, `TIKTOK_APP_SECRET`, `TIKTOK_ACCESS_TOKEN`, `TIKTOK_SHOP_CIPHER`
- `SCORE_THRESHOLD`
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_POLL_INTERVAL`

### Vercel

Vercel publica:

```
product-analyzer/frontend
```

El frontend usa `/api` en producción. El archivo `product-analyzer/frontend/_redirects` envía esas llamadas al backend de Railway y evita tener la URL de Railway escrita dentro de la interfaz.

Conecta Vercel al repositorio y a la rama `main` para que cada cambio validado se publique automáticamente.

## Diagnóstico

Antes de buscar un problema en la interfaz, comprueba:

```
GET /health
GET /pipeline/summary
GET /products
GET /opportunities/top
```

Si `/health` responde correctamente pero `/pipeline/summary` muestra cero productos, el problema está en la ingesta o en la base de datos. Si hay productos en la API pero no en Vercel, el problema está en el frontend, el proxy o el despliegue.

## Seguridad

Las credenciales de Amazon, TikTok, Mercado Libre, Telegram y la base de datos se manejan únicamente en variables de entorno del backend. No deben aparecer en `index.html`, GitHub ni commits.


## Ciclo de catálogo en producción

- Railway ejecuta una carga inmediatamente al iniciar y después cada **2 horas**.
- Cada producto nuevo recibe una vigencia de **48 horas** desde su primera inserción.
- Al iniciar cada ciclo se desactivan los productos cuyo `catalog_expires_at` ya venció.
- Si un producto reaparece, se actualizan sus datos sin crear duplicados y se conserva la vigencia del lote original.
- Si una fuente no tiene credenciales o falla, no se generan productos ficticios.
