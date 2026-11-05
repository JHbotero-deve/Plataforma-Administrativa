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

## Importación por URL y descuentos al cliente
Al pegar una URL pública de Amazon o AliExpress, Central intenta extraer título, descripción, precio actual, precio original, imagen principal y galería. El acceso se limita a páginas públicas y dominios permitidos; si la tienda exige sesión, bloquea la consulta o no publica el precio original, el producto queda guardado en el catálogo pero no se publica automáticamente.

La regla comercial es independiente del scoring: el precio de referencia es el precio original del proveedor; el precio al cliente se calcula con 40% de descuento. Solo se publica si el precio de venta supera el costo actual. El margen y el costo se almacenan en `published_cards` y se muestran únicamente en administración; las respuestas públicas entregan el precio final, precio de referencia y descuento, nunca el costo ni el porcentaje de ganancia. Las publicaciones externas se ordenan por margen interno descendente. Amazon agrega `AMAZON_PARTNER_TAG` y, si existe, `AMAZON_TRACKING_ID`. AliExpress genera el enlace afiliado mediante `aliexpress.affiliate.link.generate` usando App Key, App Secret y Tracking ID.

## Secciones y reposición automática de la tienda

La tienda organiza el catálogo en nueve secciones (Tecnología, Hogar, Moda, Accesorios, Deportes, Belleza y cuidado personal, Salud y bienestar, Juguetes y Oficina y estudio) más Ofertas destacadas. Cada sección inicia con un objetivo de 100 productos, configurable desde Administración entre 100 y 500. Moda agrupa también las categorías heredadas ropa y calzado.

El worker consulta productos reales mediante la API oficial de AliExpress, evita duplicados por identificador y publica solo fichas con imagen, precio original verificable, descuento efectivo del 40% y margen positivo. La reposición se ejecuta cada dos horas y consulta hasta cuatro páginas de 50 resultados por sección en cada ciclo. Ajusta STOREFRONT_SEARCH_MAX_PAGES (1–10) para cambiar el ritmo. Requiere ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET y ALIEXPRESS_TRACKING_ID. La reposición automática está habilitada por defecto y puede desactivarse con STOREFRONT_AUTO_REFILL_ENABLED=false. Si faltan credenciales o el origen no entrega datos verificables, no inventa ni publica productos.

## Telegram
El bot mantiene /top, /buscar, /modelar y /estado. También acepta URLs de Amazon y AliExpress. El endpoint autenticado `/api/v1/products/import-cli` guarda el producto y envía el enlace afiliado a Telegram. El servicio de Telegram en Railway requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID; sin esas variables queda deshabilitado de forma segura.

**Modo de protección de marketplaces:** el worker no ejecuta búsquedas ni enriquecimiento automático de Amazon o AliExpress. Un guard global en `requests.Session.send` bloquea las solicitudes HTTP a dominios de ambas tiendas, sus API, enlaces cortos y CDN de imágenes antes de que salgan de la aplicación. Las importaciones manuales conservan URLs y metadatos entregados por el usuario; no consultan las páginas de las tiendas. Los enlaces afiliados existentes y las credenciales se conservan, pero no se generan enlaces nuevos mediante API. Las imágenes alojadas en dominios de marketplaces no se cargan automáticamente en el panel ni en la tienda.

Los módulos de Amazon Creators, AliExpress Affiliate y AE-Dropshipper se conservan para no borrar integraciones ni credenciales. Mientras el guard esté activo, cualquier solicitud HTTP del backend a esos dominios —incluido el intercambio OAuth— se rechaza antes de salir. Telegram, PostgreSQL, TikTok y los demás servicios no pertenecientes a Amazon/AliExpress siguen sus flujos independientes.

## CLI autorizados

Central recibe datos obtenidos legítimamente por tus CLI o exportaciones, sin depender de APIs de marketplace. Configura `CENTRAL_API_URL` y `CENTRAL_INGEST_API_KEY`; la clave se envía por encabezado y nunca debe guardarse en el código.

Para un producto: `python scripts/importar-url-central.py --url "URL_DEL_PRODUCTO" --title "Nombre" --price 19.90 --previous-price 29.90 --currency USD --image "https://..." --category accesorios`. También admite `--external-id`, `--description`, `--rating`, `--reviews-count`, `--sales-estimate` y `--gallery` repetido. No inventes ni completes métricas que el origen no entregue.

Para importar lotes: `python scripts/importar-lote-central.py --file productos.json --dry-run` valida primero el archivo. Después ejecuta el mismo comando sin `--dry-run` para enviar lotes al endpoint autenticado `/api/v1/products/import-cli/batch`. Admite JSON (lista, objeto con `products`/ `items`) y CSV. Campos canónicos: `url`, `external_id`, `category`, `title`, `description`, `price`, `previous_price`, `currency`, `image_url`, `gallery_urls`, `rating`, `reviews_count`, `sales_estimate`. También reconoce nombres comunes como `product_url`, `item_id`, `asin`, `name`, `current_price`, `original_price`, `main_image`, `review_count` y `sales`. Categorías desconocidas quedan sin clasificar; no se fuerza `accesorios`.

El enriquecimiento web solo lee páginas públicas. Si el marketplace exige iniciar sesión, Central registra el bloqueo y aplica espera antes de reintentar; no intenta evadir controles de acceso. Los productos solo se puntúan automáticamente cuando tienen título real, precio, categoría e imagen.
