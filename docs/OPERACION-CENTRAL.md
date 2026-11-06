# Operación y recuperación de Central

## URLs de producción

| Uso | URL |
|---|---|
| Sitio principal | https://central-7ykr.vercel.app/ |
| Tienda | https://central-7ykr.vercel.app/tienda |
| Showcase 3D de audífonos | https://central-7ykr.vercel.app/headphones/ |
| Salud API + PostgreSQL | https://gracious-renewal-production-76df.up.railway.app/api/v1/health |
| Catálogo activo (primeros 200) | https://gracious-renewal-production-76df.up.railway.app/api/v1/products?limit=200&offset=0 |
| Publicaciones visibles | https://gracious-renewal-production-76df.up.railway.app/api/v1/publications?limit=5000 |
| Secciones de tienda | https://gracious-renewal-production-76df.up.railway.app/api/v1/storefront/sections |

## Infraestructura

- GitHub: `JHbotero-deve/Plataforma-Administrativa`, rama `main`.
- Frontend estático: Vercel, proyecto `central-7ykr`, raíz `product-analyzer/frontend`.
- API: Railway, proyecto `happy-recreation`, servicio `gracious-renewal`, raíz `product-analyzer/app`.
- Worker: Railway, servicio `central-worker`, arranque `python -u main.py`.
- Bot: Railway, servicio `central-telegram`.
- Persistencia: PostgreSQL de Railway. No editar la base de datos manualmente como primer recurso.

## Flujo de catálogo esperado

1. El usuario o un ayudante entrega una URL corta o larga de Amazon/AliExpress.
2. `url_import.py` valida el enlace y conserva la URL original; no consulta APIs de búsqueda de marketplaces.
3. `product_sources.py` sigue redirecciones HTTP una por una, valida los dominios permitidos y lee HTML público, Open Graph y JSON-LD.
4. El enriquecedor recupera únicamente campos realmente presentes: título, precio, moneda, imagen, galería, descripción, categoría y vendedor. Los campos ausentes siguen vacíos.
5. PostgreSQL conserva URL original, URL resuelta, estado, versión y campos faltantes.
6. El worker vuelve a intentar registros parciales con espera, analiza los datos y publica en la tienda los productos completos al precio real de la fuente. No inventa descuentos ni costes.
7. Los registros con más de 48 horas no se borran ni se desactivan automáticamente: se marcan como `stale`, la tarjeta avisa que el precio debe verificarse y el worker vuelve a consultar su URL. Solo una página que confirme título, precio, moneda e imagen reactiva un registro que ya estaba inactivo. Si el comercio bloquea la lectura, se conserva el registro y se reintenta más tarde.
7. La tienda consulta publicaciones de Central; los productos incompletos no deben mostrarse como fichas de compra.

Las APIs internas de Central siguen siendo necesarias para el frontend. Se retiraron las APIs externas de búsqueda, detalle, afiliación y OAuth de Amazon/AliExpress. El guard de red solo permite GET HTTPS a páginas de producto y enlaces cortos oficiales; bloquea búsqueda, APIs, autenticación y escrituras. Telegram y Wompi son integraciones independientes.

## Estados de importación

- `complete`: se recuperaron los datos mínimos y la ficha es utilizable.
- `partial`: la página respondió, pero faltan campos o el marketplace limitó el acceso.
- `paused`: estado histórico de versiones anteriores; el worker debe volver a procesarlo con la versión vigente.
- `blocked`: dominio no permitido, redirección insegura, login/CAPTCHA o política de acceso. No se intenta evadir el bloqueo.

Datos mínimos para mostrar una tarjeta: título real, precio positivo, imagen HTTP/HTTPS y URL de producto. La categoría mejora la navegación y se deriva solo cuando hay evidencia suficiente. Los ratings, reseñas y ventas no se rellenan por defecto. Solo se usan en el modelo si `source_metadata.field_sources` indica `public_product_page` o `user`; las métricas heredadas de integraciones antiguas se muestran como N/D y no inflan el score. Los datos manuales se identifican como aportados por el usuario. Los precios antiguos de registros marcados `metadata_source=url_only` se sustituyen únicamente cuando la página pública entrega un precio verificable; los valores suministrados por el usuario se conservan.

## Diagnóstico rápido

1. Abrir `/api/v1/health`. Se espera HTTP 200 y `status=ok`, `database=ok`.
2. Consultar `/api/v1/products?limit=200&offset=0` y repetir con `offset=200`, `400`, etc. El endpoint entrega productos activos, no garantiza que todos sean publicables. Para recuperar registros antiguos, revisar `catalog_expires_at`, `is_active`, `source_metadata.catalog_status` y los logs de revalidación.
3. Consultar `/api/v1/publications?limit=5000`. Este endpoint entrega tarjetas publicadas; no es el contador del catálogo.
4. Si hay productos pero no publicaciones, revisar `source_metadata.missing_fields`, `enrichment_status`, imagen, precio, título y URL. Revisar los logs del worker.
5. Si publicaciones devuelve productos pero la tienda está vacía, revisar en el navegador Network/Console, la ruta `/api/v1/publications`, el deployment de Vercel y el caché del navegador. Confirmar que `image_url` y `image_gallery` contienen URLs HTTPS accesibles y que la interfaz no esté descartando dominios de CDN de marketplace.
6. Si el health falla, revisar primero logs de `gracious-renewal` y conexión a PostgreSQL. No cambiar variables secretas sin evidencia.
7. Si el worker no enriquece, revisar logs de `central-worker` y confirmar que el deployment contiene la versión actual de `product_sources.py` y `main.py`.
8. Si un enlace corto no se resuelve, revisar el destino de cada redirección y el estado parcial. No desactivar la lista de dominios permitidos ni permitir hosts arbitrarios.
9. En Telegram, `/estado` distingue total registrado, activos, inactivos, vencidos y registros con URL. `/buscar término` necesita un término; `/buscar` sin argumento solo devuelve el uso. `/modelar` primero informa que inició, revalida hasta 20 URLs pendientes y luego reporta los productos puntuados y los errores.

## Comprobaciones antes de publicar un cambio

- Comparar el SHA de GitHub `main` con el SHA de Vercel y los servicios Railway.
- Ejecutar Central CI y CodeQL; no aceptar fallos ni checks omitidos.
- Probar URLs cortas y largas de ambos marketplaces con respuestas HTTP/JSON-LD simuladas.
- Probar redirecciones fuera de dominios permitidos, ciclos, login, HTML sin metadatos y respuestas de error.
- Probar que los campos aportados manualmente no sean sobrescritos por metadatos de menor calidad.
- Confirmar que una ficha sin precio o imagen no se publique y que una ficha válida sin descuento sí pueda aparecer al precio de origen.
- Verificar en producción health, catálogo, publicaciones, tienda y showcase 3D.

## Límites de seguridad

No subir claves, cookies, tokens ni credenciales al repositorio. No imprimir secretos en logs. No evadir CAPTCHA, login, paywalls o restricciones de los marketplaces. No permitir redirecciones a IP privadas, localhost, servicios de metadatos cloud o dominios arbitrarios. Los datos de una página se consideran no confiables y se validan antes de persistirlos.

## Referencias de código

- Importación y URL original: `product-analyzer/app/url_import.py`.
- Extracción pública y reintentos: `product-analyzer/app/product_sources.py`.
- Worker y publicación automática: `product-analyzer/app/main.py`, `product-analyzer/app/pricing.py`.
- API y paginación: `product-analyzer/app/api.py`.
- Tienda: `product-analyzer/frontend/tienda.html`.
- Arquitectura URL-first: `docs/ARQUITECTURA-URL-FIRST.md`.
