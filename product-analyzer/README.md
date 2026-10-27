# Central

## Arquitectura
- Frontend estático en Vercel desde `product-analyzer/frontend`.
- API y worker en Railway desde `product-analyzer/app`.
- PostgreSQL como persistencia.
- Amazon y AliExpress: importación exclusivamente por URL aportada por los CLI autorizados.
- Los enlaces afiliados de Amazon y AliExpress se generan con sus respectivos datos de afiliación cuando están configurados.
- Telegram para consulta y alertas.
- Wompi preparado para pagos.

## Flujo
Fuentes -> ingesta cada 2 horas -> PostgreSQL -> scoring -> API -> Vercel.

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
