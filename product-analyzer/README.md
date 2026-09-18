# Backend y dashboard — Radar de Producto

## Componentes

- FastAPI + worker de ingesta en `app/`
- PostgreSQL y migraciones SQL
- Dashboard estático en `frontend/`
- Docker Compose para desarrollo local
- Railway para el backend
- Netlify para el frontend

## Ejecutar localmente

Desde esta carpeta:

```bash
cp .env.example .env
docker compose up --build -d
```

Dashboard: http://localhost:3000  
API: http://localhost:8000  
Health: http://localhost:8000/health  
Pipeline: http://localhost:8000/pipeline/summary

## Producción

### Railway

El despliegue de producción usa el `Dockerfile` de la raíz del repositorio `Central`. El servicio debe apuntar al repositorio, rama `main` y directorio raíz.

Las credenciales reales se cargan como variables de entorno en Railway. Nunca se guardan en GitHub.

### Netlify

Netlify publica exactamente:

```
product-analyzer/frontend
```

En producción el dashboard llama a `/api`. El archivo `frontend/_redirects` hace el proxy hacia Railway, por lo que la URL del backend no está escrita dentro de la aplicación.

El archivo `netlify.toml` evita que el navegador conserve indefinidamente una versión antigua de `index.html` y desactiva el cache del proxy de API.

## Fuentes de productos

### Mercado Libre

Se consulta el buscador público de Mercado Libre Colombia (MCO). Para recursos privados de una cuenta se necesitaría el OAuth correspondiente.

### Amazon

La integración usa Amazon Creators API con Credential ID, Credential Secret, versión regional de credenciales, marketplace y Partner Tag. PA-API 5.0 no se usa.

### TikTok Shop

La integración usa TikTok Shop Open API para el catálogo de la tienda autorizada, con App Key, App Secret, access token y shop cipher. Esta integración no significa acceso al catálogo público completo de terceros.

## Regla de datos reales

El sistema no genera productos ficticios cuando una fuente falla.

Un error de Amazon, TikTok o Mercado Libre queda registrado en el pipeline y esa fuente no aporta productos a la base de datos. El frontend tampoco vuelve a mostrar un catálogo de demostración.

## Diagnóstico rápido

1. `GET /health` debe responder `status=ok` y `database=ok`.
2. `GET /pipeline/summary` muestra cuántos productos activos existen por plataforma y cuándo fue la última actualización.
3. `GET /products?platform=mercadolibre` permite confirmar los datos almacenados.
4. `GET /opportunities/top` permite comprobar que el scoring está funcionando.
5. Si la API tiene productos pero Netlify no los muestra, revisa el deploy de Netlify y el proxy `/api`.

## Seguridad

No subas `.env`, tokens, Credential Secret, App Secret, contraseñas de PostgreSQL ni tokens de Telegram.

## Monetización

La aplicación registra clics mediante `/monetize/click/{product_id}`, pero la confirmación de conversiones y las comisiones reales dependen del programa de afiliados de cada plataforma. El dashboard no debe presentar ingresos ficticios como ingresos reales.
