# Central

Central es un catálogo de productos reales con importación autorizada por URL,
análisis de oportunidades, publicación web y herramientas de monetización.

## Arquitectura

- Frontend estático: `product-analyzer/frontend`, desplegado en Vercel.
- API, worker y bot: `product-analyzer/app`, desplegados en Railway.
- Persistencia: PostgreSQL.
- Fuentes de producto admitidas: Amazon y AliExpress.
- Notificaciones e interacción: Telegram.
- Pagos: Wompi.

## Importación de productos

Amazon y AliExpress se importan exclusivamente mediante enlaces proporcionados
por el usuario o por los CLI autorizados. No se buscan productos ni se extraen
datos automáticamente de páginas de marketplaces. Los CLI pueden enviar título,
precio, moneda e imagen junto con el enlace.

Los enlaces afiliados se generan cuando están configuradas las credenciales de
la plataforma. Si no existen, el producto conserva su URL original.

## Telegram

El bot ofrece `/top`, `/buscar`, `/modelar`, `/estado` y `/agregar URL`.
La importación por URL admite Amazon y AliExpress. Configura
`TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en Railway para habilitarlo.

## Requisitos operativos obligatorios

- Telegram es solo la interfaz privada de entrada, consulta y notificación; no ejecuta ni controla el ciclo de análisis.
- El trabajador de Railway procesa los productos ya guardados aunque Telegram esté detenido o no pueda enviar mensajes.
- Los fallos de notificación nunca deben interrumpir la importación, el enriquecimiento ni la puntuación.
- Los errores 401, 403, permisos de API, redirecciones o sesión se registran con su causa y permanecen en reintento; no se presentan como bloqueo definitivo de la tienda.
- Los reintentos usan espera progresiva limitada y los productos pendientes se procesan por antigüedad para evitar que un grupo monopolice el lote.
- El análisis no debe depender de exclusiones de publicación de la tienda. Una exclusión de escaparate no elimina ni bloquea el análisis del catálogo.
- El worker debe recuperar automáticamente su ciclo después de errores transitorios de base de datos o de una ejecución individual.

## Desarrollo local

Desde `product-analyzer`:

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

Frontend: <http://localhost:3000> · API: <http://localhost:8000> ·
Adminer: <http://localhost:8080> · PostgreSQL: `localhost:5435`.

Las credenciales deben configurarse como variables de entorno y no guardarse en
Git.
