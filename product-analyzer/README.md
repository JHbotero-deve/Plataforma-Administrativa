# Central — Product Analyzer

Esta carpeta contiene la API, el worker y la aplicación frontend de Central.

## Componentes

- `app/`: API FastAPI, lógica de catálogo, autenticación, publicaciones, pedidos, pagos y worker.
- `frontend/`: panel administrativo, tienda, fichas, estilos, service worker y showcase de audífonos.
- `headphones/`: aplicación fuente del showcase 3D en React/Vite. Su salida compilada se publica en `frontend/headphones/`.
- `tests/`: pruebas unitarias y de regresión.
- `../docs/OPERACION-CENTRAL.md`: URLs de producción, endpoints, diagnóstico y recuperación.

## Experiencia de producto

La guía visual aprobada contempla un hero de producto grande, tienda por categorías y tarjetas con perspectiva 3D, profundidad e interacción de luz. Las referencias contemplan audífonos, zapatos, camisetas y portátiles, con variaciones de color y fondos. La tienda incluye catálogo, detalle, galería, carrito y checkout para productos propios.

La implementación existente de `frontend/tienda.html` incorpora tarjetas con interacción de perspectiva; los productos pueden activar `model-viewer` cuando `model_url` apunta a un modelo GLB/GLTF válido. Sin modelo se conserva la imagen real. El showcase de audífonos se sirve en `/headphones/`. No se simulan modelos ni se rellenan fichas con datos inventados.

Este README conserva las decisiones de diseño como especificación persistente. No sustituye el archivo fuente de Figma ni significa que sus frames se hayan sincronizado automáticamente.

## Flujo de datos

1. Panel administrativo, Telegram o CLI envían una URL y los metadatos disponibles.
2. La API valida URL, categoría, moneda, precio e imágenes HTTP/HTTPS.
3. El catálogo se almacena en PostgreSQL y conserva la fuente y los datos suministrados.
4. La capa de análisis puntúa solo productos con título, precio, moneda, imagen y categoría completos y verificados, independientemente de que cada campo proceda de la página pública, del respaldo oficial admitido o de datos manuales.
5. La publicación comercial exige los datos reales y las reglas de precio, descuento y margen.
6. La tienda consulta publicaciones y secciones; no muestra registros incompletos como si fueran productos listos para comprar.

## Integridad del catálogo

Los campos que no se recibieron se mantienen vacíos. Un registro URL-only puede tener un identificador técnico y un título interno de referencia, pero no se presenta como nombre comercial ni se publica. No se permiten valores de respaldo para precio, imagen, reseñas, calificación o ventas.

La URL es la entrada principal y la página pública se consulta primero. El enriquecedor extrae metadatos HTML/Open Graph/JSON-LD y resuelve enlaces cortos validando cada redirección. Si faltan campos esenciales, puede usar como respaldo una API oficial de detalle del mismo producto cuando exista una integración implementada y esté configurada; actualmente existe ese respaldo para AliExpress. La API es opcional: no se exige para importar, no sustituye la URL y su ausencia o fallo no detiene el proceso. Si ninguna fuente entrega los datos requeridos, el producto queda parcial y se informan los campos faltantes. No se evaden login, CAPTCHA ni controles de acceso.

## Despliegue frontend — Vercel

- Proyecto: `central-7ykr`.
- Repositorio: `JHbotero-deve/Plataforma-Administrativa`.
- Rama de producción: `main`.
- Root Directory del proyecto: `product-analyzer/frontend`.
- Configuración de rutas y rewrites: `frontend/vercel.json`.
- Proxy API: `https://gracious-renewal-production-76df.up.railway.app/api/v1/*`.
- Rutas relevantes: `/`, `/tienda`, `/producto/:slug` y `/headphones/`.

El HTML estático no necesita secretos de base de datos. Después de cada push verificar que el SHA del deployment coincida con `main` y que el dominio `central-7ykr.vercel.app` esté asignado al deployment nuevo, no a una versión anterior.

## Despliegue backend — Railway

Proyecto: `happy-recreation`, entorno `production`.

| Servicio | Carpeta raíz | Build | Start | Health |
|---|---|---|---|---|
| `gracious-renewal` (API) | `/product-analyzer/app` | Dockerfile `Dockerfile` | Uvicorn, puerto `PORT` | `/api/v1/health` |
| `central-worker` | `product-analyzer/app` | Railpack | `python -u main.py` | Proceso continuo |
| `central-telegram` | `product-analyzer/app` | Railpack | `python -u -c "from telegram_alert import run_bot; run_bot()"` | Proceso continuo |

PostgreSQL es la base primaria. La API recibe tráfico desde Vercel; el worker procesa tareas del catálogo; Telegram funciona como interfaz de consulta y notificación, no como dependencia de la API.

Variables de entorno críticas: `DATABASE_URL` en API y worker; `CENTRAL_AUTH_SECRET` y credenciales admin en API; `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CENTRAL_API_URL` y `CENTRAL_INGEST_API_KEY` en Telegram. Los secretos se configuran directamente en Railway.

## Validación de producción

- GitHub `main`, Vercel y los servicios Railway deben apuntar al mismo SHA.
- Vercel debe estar `READY`; los servicios de Railway deben estar `SUCCESS` y en estado `online`.
- `GET /api/v1/health` debe devolver `status: ok` y `database: ok`.
- `/tienda` y `/headphones/` deben responder HTTP 200.
- Revisar cantidades de `/api/v1/products`, `/api/v1/publications` y `/api/v1/storefront/sections`.
- Una tienda con cero publicaciones no muestra tarjetas; un catálogo con imagen nula no puede mostrar foto. No usar datos ficticios para ocultar esos vacíos.
- Revisar logs de API, worker y Telegram después del despliegue.

## Desarrollo local

Desde esta carpeta, el Compose del proyecto levanta los servicios definidos en `docker-compose.yml`. Configura `.env` a partir de `.env.example`; no incluyas claves reales en Git.

## Pruebas

Desde la raíz del repositorio, el workflow `Central CI` compila el frontend, construye el showcase Headphones, compila Python, ejecuta las pruebas unitarias de `tests/` y construye la imagen Docker del backend. Ejecuta los mismos comandos del workflow antes de un cambio de arquitectura.