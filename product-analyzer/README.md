# Central

## Arquitectura
- Frontend estático en Vercel desde `product-analyzer/frontend`.
- API y worker en Railway desde `product-analyzer/app`.
- PostgreSQL como persistencia.
- Mercado Libre Colombia como única fuente automática de catálogo.
- Telegram para consulta y alertas.
- Wompi preparado para pagos.

## Flujo
Mercado Libre -> ingesta cada 2 horas -> PostgreSQL -> scoring -> API -> Vercel.
Los productos activos caducan a las 48 horas; cuando vuelven a detectarse, su vigencia se renueva 48 horas.

## Regla de datos
No existen productos demo, mocks ni valores de respaldo. Si la fuente real falla, el frontend informa la indisponibilidad y no inventa catálogo.

## Producción
Las credenciales reales viven en las variables de entorno de Railway/Vercel y no se almacenan en Git.

## Amazon por URL
La ruta comercial de Amazon permanece activa aunque la API de catálogo no esté habilitada. Se puede registrar un producto mediante su URL original; Central intenta obtener título e imagen pública y conserva el enlace para abrirlo y venderlo. El precio es opcional para el alta por URL. La integración de Amazon API es independiente y no bloquea el worker de Mercado Libre.

## Telegram
El bot mantiene /top, /buscar, /modelar y /estado. También acepta /agregar para registrar una URL de Amazon o Mercado Libre. El servicio de Telegram en Railway requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID; sin esas variables queda deshabilitado de forma segura.
