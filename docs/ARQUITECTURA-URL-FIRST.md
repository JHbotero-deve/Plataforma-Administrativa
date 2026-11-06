# Arquitectura URL-first y ayudantes de Central

## Objetivo

Central debe obtener los productos a partir de URLs de páginas públicas. El usuario, el administrador o un ayudante entrega el enlace; los ayudantes de Central procesan esa página, guardan la información disponible y entregan el producto al motor de análisis.

La búsqueda de productos y el enriquecimiento del catálogo no deben depender de APIs de Amazon, AliExpress ni de otros marketplaces. Las credenciales de APIs de proveedores no pueden ser un requisito para importar, analizar o publicar un producto.

## Qué significa trabajar con URLs

Una URL de producto es la fuente primaria del registro. El sistema debe:

- aceptar URLs HTTP o HTTPS de dominios autorizados;
- resolver enlaces cortos y redirecciones de forma segura;
- guardar la URL original y la URL final;
- leer únicamente el contenido público permitido de la página;
- extraer metadatos HTML, Open Graph y JSON-LD;
- normalizar precio, moneda, imágenes, título, descripción, categoría, vendedor y valoraciones cuando existan;
- guardar la procedencia de cada dato y la fecha de lectura;
- distinguir entre dato confirmado, dato ausente y dato que no se pudo leer.

No se debe asumir que todos los marketplaces exponen todos los campos. Si la página no ofrece un precio, imagen o descripción legible, el registro queda parcial y se muestra esa condición.

## Responsabilidad de los ayudantes

Los ayudantes son los responsables de ejecutar el flujo de URL. No son un sustituto de la fuente: la fuente es la página enlazada.

Flujo obligatorio:

1. **Recepción:** recibir la URL y, opcionalmente, una categoría o nota del usuario.
2. **Validación:** comprobar protocolo, dominio permitido, ausencia de credenciales incrustadas y protección contra SSRF.
3. **Resolución:** seguir redirecciones con límite de saltos, validar cada destino y conservar el enlace original.
4. **Lectura:** solicitar la página pública con límites de tiempo y tamaño.
5. **Extracción:** leer metadatos públicos y datos estructurados; no inventar valores ni completar con resultados de búsquedas ajenas.
6. **Normalización:** convertir precios y monedas de forma controlada, validar imágenes y limpiar textos.
7. **Persistencia:** crear o actualizar el producto sin duplicarlo y guardar la procedencia de los campos.
8. **Análisis:** enviar el producto guardado al motor de puntuación y oportunidades.
9. **Publicación:** permitir su publicación solo cuando cumpla las reglas comerciales y tenga los datos mínimos requeridos.
10. **Resultado:** devolver al usuario el estado: importado, parcial, duplicado o bloqueado, junto con la causa concreta.

## Restricciones de seguridad y calidad

- No evadir CAPTCHA, login, paywall, controles de acceso ni restricciones del marketplace.
- No ejecutar JavaScript arbitrario descargado de la página.
- No seguir redirecciones hacia localhost, IP privadas, rangos reservados, servicios de metadatos cloud ni dominios fuera de la lista permitida.
- Limitar tamaño de respuesta, tiempo de conexión, número de redirecciones y cantidad de imágenes.
- No guardar secretos, cookies de sesión ni credenciales de usuarios.
- No fabricar precio, inventario, ventas, reseñas, descuentos ni características.
- Marcar explícitamente los campos que no se pudieron obtener.
- Reintentar solo errores transitorios con espera progresiva; no repetir indefinidamente errores permanentes.

## Qué queda fuera del flujo de catálogo

No usar APIs de proveedores para:

- descubrir productos o ejecutar búsquedas automáticas en marketplaces;
- obtener fichas de productos como sustituto de procesar la URL;
- enriquecer productos que ya fueron recibidos por URL;
- bloquear la importación cuando una API de proveedor no tiene credenciales o rechaza la autenticación.

Las APIs internas de Central y los servicios de otras funciones (por ejemplo, Telegram para notificaciones y Wompi para pagos) son independientes de esta regla. La prohibición se refiere a las APIs de marketplace para adquirir datos de catálogo.

## Experiencias de usuario

### Tienda pública

- Diseño independiente del administrador.
- Catálogo visual con categorías, búsqueda y fichas de producto.
- Imágenes, precios y enlaces sustentados en los datos guardados.
- Experiencia 3D o inmersiva donde mejore la exploración, sin sacrificar velocidad ni accesibilidad.
- Enlaces de compra que conduzcan a la URL válida del producto o al enlace comercial permitido.

### Administración

- Dashboard con menú lateral persistente.
- Secciones diferenciadas para resumen, catálogo, ayudantes, análisis, publicaciones, pedidos, monetización y configuración.
- Estado visible de cada importación: recibida, procesando, completada, parcial o fallida.
- Detalle del error y de la fuente de cada dato.
- Acciones para reintentar o corregir una URL sin ocultar el motivo del fallo.

Las referencias visuales aprobadas son la dirección de diseño. No deben presentarse como funcionalidades terminadas hasta que existan en el frontend y hayan sido verificadas.

## Estado detectado en el repositorio

Estado de la rama de recuperación URL-first:

- `product-analyzer/app/url_import.py` valida el enlace y conserva la URL original; no realiza búsquedas ni llamadas a APIs de marketplaces.
- `product-analyzer/app/product_sources.py` sigue redirecciones con validación de cada host y extrae metadatos públicos HTML, Open Graph y JSON-LD. Los campos manuales tienen prioridad.
- `product-analyzer/app/marketplace_ingestion.py` y `marketplace_search.py` se conservaron como stubs de compatibilidad, sin llamadas externas.
- Se retiraron los módulos de búsqueda/enriquecimiento por API `amazon_creators.py`, `promotion_bots.py` y `affiliate_links.py`.
- Telegram `/buscar` consulta únicamente el catálogo local; para incorporar un artículo se envía su URL o se usa `/agregar`.
- El endpoint administrativo heredado `/admin/inject` devuelve HTTP 410 y no ejecuta búsquedas externas.
- El worker procesa URLs parciales, puntúa fichas utilizables y publica productos completos al precio real de la fuente, sin fabricar descuentos ni costes.

La lectura de páginas públicas puede fallar por login, CAPTCHA, límites o falta de metadatos. En esos casos el producto conserva estado parcial y el error queda registrado; no se intenta eludir el bloqueo.

## Criterios para dar la migración por terminada

- [x] El worker no ejecuta búsquedas de catálogo mediante APIs de marketplaces.
- [x] La importación por URL es el único flujo de adquisición de productos.
- [x] El enriquecimiento se basa en la página pública enlazada y en datos suministrados explícitamente por el usuario.
- [x] No se requieren claves de API de Amazon o AliExpress para importar, analizar o mostrar productos.
- [x] Los errores de acceso, CAPTCHA, login y falta de metadatos se registran como estados parciales o fallidos con causa.
- [x] Se conservan URL original, URL resuelta y procedencia de datos.
- [ ] Las pruebas cubren URL válida, enlaces cortos, redirecciones, dominios prohibidos, SSRF, metadatos ausentes y respuestas bloqueadas.
- [ ] La tienda pública y el administrador existen como experiencias separadas; el administrador tiene menú lateral y la tienda implementa el diseño visual aprobado.
- [ ] Los despliegues se validan en preview antes de publicar en producción.

## Regla de producto

**Central trabaja con URLs. Los ayudantes procesan esas URLs. El catálogo se analiza con los datos obtenidos de las páginas enlazadas. Las APIs de marketplace no son el motor de búsqueda ni la fuente de enriquecimiento del catálogo.**
