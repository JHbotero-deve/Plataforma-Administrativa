# Arquitectura URL-first y ayudantes de Central

## Objetivo

Central debe obtener los productos a partir de URLs de páginas públicas. El usuario, el administrador o un ayudante entrega el enlace; los ayudantes de Central procesan esa página, guardan la información disponible y entregan el producto al motor de análisis.

La URL pública es siempre el punto de entrada y la fuente primaria. El sistema debe intentar primero leer la página enlazada; no debe exigir una API para importar o procesar una URL. Si la página no entrega todos los campos, puede usar como respaldo una API oficial de detalle del mismo producto cuando esa integración exista y esté configurada. Hoy está implementado ese respaldo para el detalle de AliExpress. La API complementa los campos faltantes: no reemplaza la URL, no es obligatoria y no debe bloquear el flujo si no está disponible.

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
5. **Extracción primaria:** leer metadatos HTML, Open Graph y JSON-LD de la URL pública; no inventar valores ni completar con resultados de búsquedas ajenas.
6. **Respaldo opcional:** si faltan campos esenciales, consultar la API oficial de detalle del mismo producto solo cuando el proveedor tenga esa integración implementada, las credenciales estén configuradas y se pueda identificar el producto con seguridad. Registrar la procedencia API por campo. Si no hay API, falla o no devuelve datos suficientes, continuar con los datos de la URL y dejar los campos ausentes como pendientes.
7. **Normalización:** convertir precios y monedas de forma controlada, validar imágenes y limpiar textos.
8. **Persistencia:** crear o actualizar el producto sin duplicarlo y guardar la procedencia de cada campo.
9. **Análisis:** puntuar únicamente cuando los campos mínimos estén completos y verificados, ya provengan de la página pública, de una API oficial de detalle admitida o de datos aportados por el usuario.
10. **Publicación:** permitirla solo cuando se cumplan las reglas de calidad y comerciales.
11. **Resultado:** devolver el estado importado, parcial, duplicado o bloqueado, junto con los campos faltantes y la causa concreta.

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

Las APIs oficiales de marketplaces se permiten únicamente como fuente opcional de candidatos para descubrimiento:

- Amazon Creators API y AliExpress Affiliate API pueden buscar por término cuando sus credenciales oficiales están configuradas.
- Cada candidato debe aportar una URL de producto válida; la ficha pública de esa URL se vuelve a leer antes de guardar, puntuar o publicar.
- Una API oficial de detalle puede complementar los campos que la URL no exponga, siempre que corresponda al mismo producto y devuelva datos válidos. Cada campo conserva la procedencia `aliexpress_affiliate_api` u otra procedencia específica admitida; solo esos campos efectivamente devueltos se consideran verificados.
- Si la API no existe para el proveedor, no está configurada o falla, la importación URL-first continúa sin ella; los campos no recuperados permanecen vacíos y el producto queda parcial.
- Una API sin credenciales, con error 401/403, límite o caída solo desactiva su propia búsqueda; la otra fuente, Telegram, la importación URL-first y el worker siguen funcionando.
- No se eluden CAPTCHA, login, controles de acceso ni restricciones de los marketplaces.
- Los enlaces afiliados se gestionan aparte de la lectura pública. No se presenta una URL normal como afiliada.

La importación de una URL sigue siendo independiente: recibe, resuelve, lee y conserva la página pública sin requerir claves de afiliación. Las APIs internas de Central y servicios como Telegram y Wompi siguen siendo independientes.

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
- `product-analyzer/app/product_sources.py` sigue redirecciones con validación de cada host y extrae metadatos públicos HTML, Open Graph y JSON-LD. Los campos manuales tienen prioridad y el detalle oficial de AliExpress funciona como respaldo para campos que falten en la página pública.
- `product-analyzer/app/marketplace_ingestion.py` ejecuta discovery oficial opcional por Amazon y AliExpress, vuelve a verificar cada URL pública y solo persiste/puntúa fichas completas y confirmadas.
- `amazon_creators.py`, `promotion_bots.py` y `affiliate_links.py` contienen los clientes oficiales; sus errores se aíslan por marketplace y no bloquean la ruta URL-first.
- El worker ejecuta la recuperación URL-first cada 15 minutos y dispara discovery en un hilo independiente cada 2 horas, evitando que una búsqueda externa retrase la lectura de URLs.
- Telegram `/buscar <término>` consulta primero el catálogo local y después las fuentes oficiales disponibles; `/buscar <URL>`, `/producto <URL>` y `/agregar <URL>` importan directamente el enlace.
- Los candidatos sin verificación pública se muestran como pendientes, sin precio inventado, puntuación ni publicación.
- El endpoint administrativo heredado `/admin/inject` devuelve HTTP 410 y no ejecuta búsquedas externas.
- El worker procesa URLs parciales, puntúa fichas utilizables y publica productos completos al precio real de la fuente, sin fabricar descuentos ni costes.

La lectura de páginas públicas puede fallar por login, CAPTCHA, límites o falta de metadatos. En esos casos el producto conserva estado parcial y el error queda registrado; no se intenta eludir el bloqueo.

## Criterios para dar la migración por terminada

- [x] El worker no ejecuta búsquedas de catálogo mediante APIs de marketplaces.
- [x] La importación por URL es el flujo primario; el discovery oficial es opcional y separado.
- [x] El enriquecimiento se basa en la página pública enlazada y en datos suministrados explícitamente por el usuario.
- [x] La importación por URL no exige claves de APIs externas; si la página aporta los datos mínimos, puede continuar sin ellas.
- [x] El respaldo de API de detalle es opcional, específico por proveedor y no sustituye la URL como entrada principal.
- [x] Los errores de acceso, CAPTCHA, login y falta de metadatos se registran como estados parciales o fallidos con causa.
- [x] Se conservan URL original, URL resuelta y procedencia de datos.
- [ ] Las pruebas cubren URL válida, enlaces cortos, redirecciones, dominios prohibidos, SSRF, metadatos ausentes, respuestas bloqueadas y errores aislados de APIs oficiales.
- [ ] La tienda pública y el administrador existen como experiencias separadas; el administrador tiene menú lateral y la tienda implementa el diseño visual aprobado.
- [ ] Los despliegues se validan en preview antes de publicar en producción.

## Regla de producto

**Central trabaja URL-first: la URL es la entrada y la página pública es la primera fuente. Una API oficial de detalle puede completar datos faltantes como respaldo opcional; nunca es un requisito. El análisis solo usa campos completos, válidos y con procedencia verificable.**
