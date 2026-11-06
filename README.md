# Central

Central es una plataforma full stack de catálogo, análisis de oportunidades y comercio digital. El frontend público y el panel administrativo comparten la API y PostgreSQL, pero mantienen experiencias separadas.

## Guía visual aprobada: Figma, tienda y tarjetas 3D

Esta sección conserva en el repositorio la especificación visual revisada para no perderla entre commits. Es una guía de implementación; no reemplaza el archivo fuente de Figma ni afirma que el conector haya sincronizado sus frames.

- Hero principal de producto a gran escala, con un producto protagonista y navegación visual clara.
- Tarjetas de producto con perspectiva 3D, profundidad, inclinación por puntero, respuesta de luz y reflejos; la interacción debe respetar pantallas táctiles y `prefers-reduced-motion`.
- Familias visuales de referencia: audífonos, zapatos, camisetas y portátiles, con variantes de color y fondos adecuados a cada producto.
- Recorrido de tienda: hero, navegación y categorías, catálogo por secciones, ficha de producto, galería, carrito y proceso de compra para productos propios.
- El escaparate de audífonos tiene su ruta independiente en `/headphones/`. La tienda está en `/tienda` y el panel administrativo en `/`.
- Un visor 3D real solo se habilita cuando el producto tiene una URL válida a un modelo GLB/GLTF. Sin modelo se muestra la imagen real; nunca se simula un modelo 3D ni se inventa una foto.
- Las tarjetas solo muestran información procedente del catálogo. No se deben generar precios, imágenes, reseñas, ventas, marcas ni atributos ficticios para completar un diseño.

## Funciones

- Importación de productos por URL y por CLI autenticado, con conservación del enlace original y de los metadatos suministrados.
- Catálogo en PostgreSQL, clasificación, puntuación de oportunidades, vencimiento y recuperación de registros.
- Panel administrativo para catálogo, publicaciones, ayudantes, pedidos, métricas y configuración.
- Tienda pública con búsqueda, filtros por categoría, productos destacados, fichas, tarjetas con interacción 3D y enlaces de compra externos.
- Carrito y checkout Wompi únicamente para productos propios de Central.
- Bot de Telegram para recibir enlaces, consultar catálogo y enviar avisos.
- Worker independiente para tareas del catálogo; la API y el worker no dependen de que Telegram esté conectado.

## Regla de integridad de datos

Una URL por sí sola no demuestra título, precio, imagen, calificación, reseñas ni ventas. Si el origen no proporciona datos y no se han suministrado mediante el panel o un CLI, esos campos quedan vacíos y el producto no se publica. Un título interno como `Producto Amazon ...` identifica un registro incompleto, no un nombre comercial.

Las solicitudes automáticas a APIs, páginas, enlaces cortos y CDN de marketplaces están bloqueadas por la protección de red actual. No se deben evadir CAPTCHA, login ni controles de acceso. La importación manual puede conservar URL, título, categoría, precio e imágenes que aporte el usuario o una fuente legítima autorizada. Los datos desconocidos permanecen desconocidos.

Para ser elegible para análisis y publicación, una ficha necesita al menos título real, categoría, precio válido e imagen real. La publicación comercial también aplica las reglas de descuento y margen configuradas en Central. Las imágenes deben ser URLs HTTP/HTTPS válidas y accesibles o archivos alojados por Central.

## Arquitectura de producción

| Componente | Plataforma | Configuración de producción |
|---|---|---|
| Frontend estático, panel y tienda | Vercel | Proyecto `central-7ykr`; código en `product-analyzer/frontend` |
| API FastAPI | Railway | Proyecto `happy-recreation`, servicio `gracious-renewal`, raíz `/product-analyzer/app`, Dockerfile `Dockerfile`, puerto 8000 |
| Worker del catálogo | Railway | Servicio `central-worker`, raíz `product-analyzer/app`, comando `python -u main.py` |
| Bot de Telegram | Railway | Servicio `central-telegram`, raíz `product-analyzer/app`, arranque `python -u -c "from telegram_alert import run_bot; run_bot()"` |
| Base de datos | Railway | PostgreSQL; conexión por `DATABASE_URL` |
| Integraciones adicionales | Railway | Wompi, correo, TikTok y otros servicios según variables configuradas |

### Frontend: Vercel

El repositorio conectado es `JHbotero-deve/Plataforma-Administrativa` y la rama de producción es `main`. La raíz del proyecto Vercel debe ser `product-analyzer/frontend`; el frontend es estático y sus rutas se definen en `product-analyzer/frontend/vercel.json`.

El archivo `vercel.json` reescribe `/api/v1/*` hacia `https://gracious-renewal-production-76df.up.railway.app/api/v1/*` y `/api/*` hacia la API versionada. También conserva las rutas `/tienda`, `/catalogo`, `/tarjetas`, `/producto/:slug` y `/headphones/`. Los HTML principales usan políticas de revalidación para evitar servir páginas obsoletas.

Después de cada push, verificar que el deployment READY tenga el mismo SHA de GitHub `main` y que `central-7ykr.vercel.app` esté asignado a ese deployment. Un deployment READY con solo el alias técnico `central-7ykr-git-main-*` no demuestra que el dominio principal se haya actualizado.

### Backend: Railway

Los servicios de API, worker y Telegram apuntan al mismo repositorio y rama `main`. La API se construye desde `product-analyzer/app/Dockerfile`, arranca Uvicorn en el puerto indicado por `PORT` y expone el health check `/api/v1/health`. El worker arranca `main.py`; Telegram arranca `run_bot()`.

Los servicios deben permanecer en el proyecto `happy-recreation`, entorno `production`. La API debe usar el dominio `gracious-renewal-production-76df.up.railway.app`, que es el destino del proxy de Vercel. El worker procesa tareas y el bot es un proceso independiente. PostgreSQL es la fuente de persistencia del catálogo.

Variables críticas por servicio:

- API: `DATABASE_URL`, `CENTRAL_AUTH_SECRET`, credenciales del administrador, `CENTRAL_INGEST_API_KEY`, `PUBLIC_STORE_URL` y variables de integraciones que estén habilitadas.
- Worker: `DATABASE_URL` y las variables de tareas/integraciones realmente activas.
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CENTRAL_API_URL` y `CENTRAL_INGEST_API_KEY`.
- Vercel: al ser estático y usar rewrite al backend, no necesita copiar secretos de PostgreSQL ni claves privadas del backend.

Los valores secretos permanecen en las variables de entorno de Railway/Vercel. Nunca se suben al repositorio ni se imprimen en logs.

## Flujo de despliegue por push

1. Hacer cambios en el repositorio y ejecutar las pruebas aplicables.
2. Confirmar que el commit llega a `main` y guardar su SHA.
3. GitHub Actions ejecuta `Central CI` y `CodeQL Advanced` para ese SHA.
4. Vercel construye el frontend desde `product-analyzer/frontend` y crea un deployment de producción.
5. Railway actualiza API, worker y Telegram desde `main`, cada uno con su raíz y comando correspondientes.
6. Verificar que Vercel y los tres servicios de Railway declaren exactamente el SHA del commit de `main`.
7. Verificar HTTP 200 en `/tienda`, `/headphones/` y `/api/v1/health`; el health debe responder `status: ok` y `database: ok`.
8. Consultar `/api/v1/products`, `/api/v1/publications` y `/api/v1/storefront/sections`. Una página 200 no equivale a un catálogo publicado: las tarjetas requieren publicaciones con datos e imágenes reales.
9. Verificar que el alias `central-7ykr.vercel.app` apunte al nuevo deployment. Si el deployment quedó solo con alias de rama, asignar el dominio principal al deployment correcto desde Vercel.

## GitHub Actions y validación

El workflow `Central CI` valida sintaxis JavaScript del panel y la tienda, compila el showcase Headphones, comprueba sus archivos estáticos, valida las rutas de Vercel, compila Python, ejecuta las pruebas y construye la imagen Docker del backend. `CodeQL Advanced` realiza el análisis de seguridad.

No se debe considerar terminado un cambio cuando una etapa falla o queda omitida. El estado de Railway `SUCCESS` y de Vercel `READY` se revisa por separado; también se compara el SHA desplegado. La rama `main` debe protegerse en GitHub para exigir los checks aprobados antes de aceptar cambios.

## Importación de un producto con datos reales

Desde el panel o un CLI autorizado se pueden aportar título, categoría, precio, moneda, imagen principal, galería, descripción y métricas solo cuando la fuente las confirma. Para importación CLI se usan `scripts/importar-url-central.py` y `scripts/importar-lote-central.py`; primero validar lotes con `--dry-run`. El endpoint autenticado es `/api/v1/products/import-cli` y su variante batch.

Si solo se entrega una URL, Central guarda el registro como incompleto y lo deja fuera de las publicaciones. El sistema no debe rellenarlo con una imagen por defecto, precio estimado, calificación ficticia ni un título promocional genérico.

## Desarrollo local

Desde la carpeta `product-analyzer`, copiar `.env.example` a `.env` y ejecutar `docker compose up -d --build`. Las direcciones locales y servicios definidos por Compose se documentan en `product-analyzer/README.md`. No guardar `.env` en Git.

## Documentos técnicos

- `docs/ARQUITECTURA-URL-FIRST.md`: principios y flujo de importación.
- `product-analyzer/README.md`: funcionamiento de API, worker, tienda, Telegram, base de datos y despliegue.