# Central

Central es una plataforma de catálogo, análisis de oportunidades y comercio digital. Su principio de ingestión es **URL-first**: el usuario o un ayudante proporciona el enlace del producto y Central trabaja a partir de esa página.

## Principio de funcionamiento del catálogo

- La entrada de productos se realiza mediante URLs públicas de páginas de producto.
- Los ayudantes de Central reciben, validan, resuelven y procesan esas URLs.
- La extracción de información se realiza desde el contenido público de la página: metadatos HTML, Open Graph y datos estructurados como JSON-LD.
- Se conserva la URL original y se registra la URL final resuelta.
- El catálogo se guarda en PostgreSQL y pasa al flujo de análisis, puntuación y publicación.
- Si la página no expone información, requiere autenticación, presenta CAPTCHA o bloquea la lectura, el ayudante registra el motivo y deja los campos faltantes pendientes. Nunca inventa precios, imágenes, ventas ni características.

**No se deben utilizar las APIs de catálogo, búsqueda o afiliación de Amazon, AliExpress u otros marketplaces para descubrir o enriquecer productos.** Las URLs son la fuente de entrada y los ayudantes son los encargados de procesarlas. Las credenciales de esas APIs no deben ser un requisito para importar, analizar o mostrar un producto.

Esta regla aplica a la adquisición de datos del catálogo. No elimina servicios técnicos propios ni integraciones independientes necesarias para otras funciones, como la API interna de Central, notificaciones de Telegram o pagos de Wompi.

## Ayudantes y flujo esperado

1. Recibir una URL desde el panel, Telegram, una herramienta autorizada o un ayudante.
2. Validar el esquema HTTP/HTTPS, el dominio permitido y la seguridad de la redirección.
3. Resolver enlaces cortos y conservar tanto el enlace original como el destino final.
4. Leer el contenido público de la página y extraer título, descripción, precio, moneda, imágenes, categoría, vendedor y valoraciones cuando estén disponibles.
5. Guardar el resultado y la evidencia de origen; marcar como parcial cualquier dato que la página no permita obtener.
6. Enviar el producto al análisis y, si cumple las reglas, habilitar su publicación en la tienda.

Los ayudantes no deben evadir inicios de sesión, CAPTCHA, controles de acceso ni restricciones del sitio. Tampoco deben presentar datos estimados como si fueran datos confirmados por la página.

## Experiencia de producto

Central debe tener dos experiencias claramente separadas:

- **Tienda pública:** escaparate visual de productos, navegación por categorías, fichas atractivas y una experiencia inmersiva con elementos 3D cuando aporten valor. El diseño debe priorizar la presentación y compra de productos.
- **Administración:** dashboard independiente con menú lateral, navegación clara por catálogo, ayudantes, análisis, publicaciones, pedidos, configuración y estado de los procesos.

La tienda y el administrador comparten el catálogo y los servicios de Central, pero no deben ser la misma interfaz ni limitarse a cambiar colores o estilos. Las referencias visuales aprobadas orientan el diseño; no significan que la tienda 3D o el nuevo administrador ya estén implementados.

## Arquitectura técnica

- Frontend: `product-analyzer/frontend`, desplegado en Vercel.
- Backend y API interna: `product-analyzer/app`, desplegados en Railway.
- Worker: procesos de importación, enriquecimiento permitido, análisis y recuperación.
- Persistencia: PostgreSQL.
- Interfaz de ayudantes y notificaciones: Telegram y las herramientas internas disponibles.
- Pagos: Wompi.

## Estado actual y trabajo pendiente

La arquitectura URL-first es el requisito funcional de producto. **El código actual todavía no cumple por completo esa regla.** Se han identificado estos puntos que deben corregirse antes de considerar terminada la migración:

- `product-analyzer/app/marketplace_ingestion.py` ejecuta búsquedas de candidatos mediante `amazon_creators.search_products` y `promotion_bots._search_bucket`.
- `product-analyzer/app/product_sources.py` contiene lógica de enriquecimiento que reconoce fuentes de metadatos de APIs de marketplace además de páginas públicas.
- `product-analyzer/app/url_import.py` ya permite recibir URLs, resolver enlaces y crear registros de producto; debe ser el punto de entrada principal, no un flujo secundario.
- Deben retirarse del ciclo de catálogo las búsquedas y dependencias de credenciales de APIs de proveedores, sustituyéndolas por procesamiento de URLs mediante los ayudantes.
- Deben existir pruebas para URL válida, redirecciones, dominio no permitido, página sin metadatos, página con bloqueo y producto con datos parciales.

No se debe declarar completada la migración ni activar un despliegue hasta que el código, las pruebas y los procesos de producción reflejen esta arquitectura.

## Telegram

Telegram es una interfaz de entrada, consulta y notificación. No debe ser el único proceso que mantiene el análisis ni la publicación. La importación por URL debe poder ejecutarse desde las interfaces autorizadas y continuar en el worker aunque Telegram no esté disponible.

## Desarrollo local

Desde `product-analyzer`:

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

Frontend: http://localhost:3000 · API interna: http://localhost:8000 · Adminer: http://localhost:8080 · PostgreSQL: localhost:5435.

Las credenciales deben configurarse como variables de entorno y no guardarse en Git.
