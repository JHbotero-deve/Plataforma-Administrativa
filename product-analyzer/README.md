# Central

## Arquitectura
- Frontend estático en Vercel desde `product-analyzer/frontend`.
- API y worker en Railway desde `product-analyzer/app`.
- PostgreSQL como persistencia.
- AliExpress descubre automáticamente ofertas con su API oficial de afiliados; Amazon requiere acceso a su API de catálogo para búsquedas automáticas.
- Los enlaces afiliados de Amazon y AliExpress se generan con sus respectivos datos de afiliación cuando están configurados.
- Telegram para consulta y alertas.
- Wompi preparado para pagos.

## Flujo
AliExpress Affiliate API -> descubrimiento y revisión cada 2 horas -> PostgreSQL -> scoring -> API -> Vercel; las mejores ofertas nuevas se notifican por Telegram.

## Tarjetas
Cada producto se representa en una tarjeta única. La zona multimedia admite imágenes reales y, cuando `model_url` apunta a un modelo GLB/GLTF accesible, activa el visor 3D dentro de la misma tarjeta. Sin modelo válido, no se simula 3D.
Los productos activos caducan a las 48 horas; cuando vuelven a detectarse, su vigencia se renueva 48 horas.

## Regla de datos
No existen productos demo, mocks ni valores de respaldo. Si la fuente real falla, el frontend informa la indisponibilidad y no inventa catálogo.

## Producción
Las credenciales reales viven en las variables de entorno de Railway/Vercel y no se almacenan en Git.

## Amazon por URL
Amazon se importa por URL sin descargar ni raspar sus páginas. Central agrega `AMAZON_PARTNER_TAG` y, si existe, `AMAZON_TRACKING_ID`. Los CLI autorizados pueden enviar título, precio, moneda e imagen junto con la URL. AliExpress genera el enlace afiliado mediante `aliexpress.affiliate.link.generate` usando App Key, App Secret y Tracking ID.

## Telegram
El bot mantiene /top, /buscar, /modelar y /estado. También acepta URLs de Amazon y AliExpress. El endpoint autenticado `/api/v1/products/import-cli` guarda el producto y envía el enlace afiliado a Telegram. El servicio de Telegram en Railway requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID; sin esas variables queda deshabilitado de forma segura.

El worker busca ropa, calzado y accesorios en AliExpress mediante `aliexpress.affiliate.product.query` en `https://api-sg.aliexpress.com/sync`, con firma IOP HMAC-SHA256. Revisa descuento, ventas recientes, evaluación, comisión y score, y envía hasta cinco alertas nuevas por ciclo. Requiere `ALIEXPRESS_APP_KEY`, `ALIEXPRESS_APP_SECRET` y `ALIEXPRESS_TRACKING_ID`; el token OAuth no es necesario para esta consulta de afiliados. Los umbrales se ajustan con `PROMOTION_MIN_DISCOUNT`, `PROMOTION_MIN_30D_VOLUME`, `PROMOTION_MIN_EVALUATION`, `PROMOTION_MIN_COMMISSION` y `PROMOTION_MIN_SCORE`.

## CLI autorizados

Usa `python scripts/importar-url-central.py --url "URL_DEL_PRODUCTO" --category accesorios` y añade `--title`, `--price`, `--currency` o `--image` si el CLI origen ya conoce esos datos. Configura `CENTRAL_API_URL` y `CENTRAL_INGEST_API_KEY`; la clave se envía en el encabezado y no debe guardarse en el código del bot.
