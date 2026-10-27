# Central

## Arquitectura
- Frontend estático en Vercel desde `product-analyzer/frontend`.
- API y worker en Railway desde `product-analyzer/app`.
- PostgreSQL como persistencia.
- Amazon y AliExpress: importación únicamente por URL.
- La conversión de AliExpress usa la API oficial de enlaces afiliados cuando existen App Key, App Secret y Tracking ID.
- Telegram para consulta y alertas.
- Wompi preparado para pagos.

## Flujo
URL enviada por Telegram/CLI -> cola PostgreSQL -> importador URL -> enlace afiliado -> scoring -> Telegram y catálogo.

## Tarjetas
Cada producto se representa en una tarjeta única. La zona multimedia admite imágenes reales y, cuando `model_url` apunta a un modelo GLB/GLTF accesible, activa el visor 3D dentro de la misma tarjeta. Sin modelo válido, no se simula 3D.
Los productos activos caducan a las 48 horas; cuando vuelven a detectarse, su vigencia se renueva 48 horas.

## Regla de datos
No existen productos demo, mocks ni valores de respaldo. Si la fuente real falla, el frontend informa la indisponibilidad y no inventa catálogo.

## Producción
Las credenciales reales viven en las variables de entorno de Railway/Vercel y no se almacenan en Git.

## Amazon por URL
Amazon se importa por URL sin descargar páginas ni usar scraping. Central conserva el enlace original y agrega el tag afiliado si `AMAZON_PARTNER_TAG` está configurado. Si no hay título, precio o imagen disponibles, se pueden enviar manualmente; no se inventan datos. AliExpress usa `aliexpress.affiliate.link.generate` con `ALIEXPRESS_APP_KEY`, `ALIEXPRESS_APP_SECRET` y `ALIEXPRESS_TRACKING_ID`.

## Telegram
El bot mantiene /top, /buscar, /modelar y /estado. También acepta enlaces directos de Amazon y AliExpress, los pone en cola, los procesa y devuelve el producto al chat. El servicio de Telegram en Railway requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID; sin esas variables queda deshabilitado de forma segura.
