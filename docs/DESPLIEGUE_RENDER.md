# Expo web y Django en un solo Web Service de Render

El `Dockerfile` de la raíz compila la web de Expo SDK 58 y prepara una imagen con Node.js y Python. El navegador usa el mismo origen para las páginas y `/api/`; no hace falta publicar un segundo servicio para Django ni configurar otra URL de API para la web.

```text
HTTPS de Render -> Node / Express (PORT)
                    |-- páginas -> Expo Router
                    |-- /api/... -> Django / Gunicorn (127.0.0.1:8000)
                    |-- /static/... -> archivos estáticos de Django
                    `-- /healthz -> comprobación de Django
                                         |
                                         `-> base móvil en Neon
```

La base de datos sigue fuera del contenedor, en Neon. El `backend/Dockerfile` anterior corresponde al despliegue separado del backend; para esta modalidad se usa **el `Dockerfile` de la raíz**.

## Publicar desde el panel de Render

1. Sube al repositorio los cambios de este despliegue, incluidos `Dockerfile`, `.dockerignore`, `server.cjs`, `scripts/arrancar-contenedor.py` y los cambios del cliente y de Django. Incluye `package.json` y `package-lock.json` actualizados.
2. En Render selecciona **New -> Web Service**, conecta el repositorio y elige la rama.
3. Selecciona **Language: Docker** y configura lo siguiente:

   | Campo | Valor |
   | --- | --- |
   | Root Directory | Vacío si `Dockerfile` y `package.json` están en la raíz del repositorio. Si están en una subcarpeta, selecciona esa carpeta. |
   | Dockerfile Path | `./Dockerfile` |
   | Docker Build Context | `.` |
   | Docker Command | Vacío; se utiliza el comando de la imagen. |
   | Health Check Path | `/healthz` |

   Render construye el Dockerfile; los comandos `npm run build:web` y de arranque ya están dentro de la imagen. No configures `npm run start:web` como Docker Command: eso omitiría el arranque de Django.
4. En **Environment** agrega estas dos variables privadas:

   | Variable | Valor |
   | --- | --- |
   | `SECRET_KEY` | Una clave aleatoria privada de al menos 50 caracteres. Consérvala entre despliegues. |
   | `NEON_MOBILE_DATABASE_URL` | La conexión PostgreSQL de la base independiente de esta aplicación, con sus credenciales y SSL. |

   No agregues estas variables con prefijo `EXPO_PUBLIC_`. La imagen no copia los archivos `.env`, los entornos virtuales ni las bases SQLite locales.

5. Pulsa **Deploy Web Service**. En el arranque verás la recolección de estáticos, las migraciones de la aplicación móvil y luego los dos servidores.
6. Abre la URL HTTPS de Render y verifica `/healthz`, `/login`, el registro, el inicio de sesión y la carga de clientes/proyectos. Actualiza una ruta interna para comprobar también la navegación directa.

El hostname exacto del servicio se obtiene de `RENDER_EXTERNAL_HOSTNAME` y se añade a `ALLOWED_HOSTS`; su origen `https://HOSTNAME` también se añade a `CSRF_TRUSTED_ORIGINS`. La imagen ya configura `DEBUG=False`, `ENABLE_INTERNAL_PROXY=True` y `TRUST_PROXY_HTTPS=True`. No es necesario definir `EXPO_PUBLIC_API_URL_WEB`: la compilación Docker activa el modo de API del mismo origen, que usa `/api/sistema/app/` en el dominio de la web.

Para agregar un dominio personalizado, configúralo en **Settings -> Custom Domains**, añádelo a `ALLOWED_HOSTS` como hostname, sin `https://` ni rutas, y añade su origen completo a `CSRF_TRUSTED_ORIGINS` (por ejemplo, `https://app.example.com`). Esta variable admite varios orígenes separados por comas. Si mantienes otros clientes web en dominios diferentes, conserva sus orígenes en `CORS_ALLOWED_ORIGINS`; por compatibilidad, también se incluyen entre los orígenes CSRF. El cliente web de este contenedor no necesita CORS para acceder a su propia API. Las protecciones CSRF siguen activas.

## Login bloqueado con 403 por el origen

Si el log indica `Forbidden (Origin checking failed - https://TU_SERVICIO.onrender.com does not match any trusted origins.)`, Django rechazó el origen antes de comprobar las credenciales. La respuesta es HTML, por lo que la aplicación puede mostrar «El servidor no devolvió JSON». Un `401` de `/api/sistema/app/yo/` al abrir el login sin sesión es esperable y no explica ese `403`.

Publica esta versión y vuelve a desplegar: el origen HTTPS del servicio se incorpora automáticamente desde `RENDER_EXTERNAL_HOSTNAME`, sin autorizar otros servicios de Render.

Si necesitas corregir un despliegue anterior antes de actualizar el código, añade el origen exacto de tu servicio a `CORS_ALLOWED_ORIGINS` en **Render -> Environment**, conservando los orígenes existentes separados por comas. Por ejemplo:

```dotenv
CORS_ALLOWED_ORIGINS=https://aplicacionreactnative.onrender.com
```

Guarda la variable y vuelve a desplegar. La versión anterior obtiene sus orígenes CSRF de esa variable y todavía no lee `CSRF_TRUSTED_ORIGINS` del entorno. Después, recarga `/login` para obtener una cookie y un token CSRF nuevos e intenta iniciar sesión. El token CSRF sigue siendo obligatorio en las escrituras web.

## Publicar con Blueprint

También puedes seleccionar **New -> Blueprint**, conectar el repositorio y utilizar el `render.yaml` de la raíz. Define un único Web Service con Docker, genera `SECRET_KEY` y solicita la conexión `NEON_MOBILE_DATABASE_URL`, `RESEND_API_KEY` y `RESEND_SENDER_EMAIL` al crear el servicio. Revisa el nombre del servicio antes de aplicarlo si ya tienes otros recursos en Render.

Para activar la recuperación de contraseña en un servicio existente, agrega también `RESEND_API_KEY`, `RESEND_SENDER_EMAIL` y `RESEND_SENDER_NAME` en **Environment**. Consulta la [guía de recuperación](RECUPERACION_CONTRASENA.md) para el remitente, la vigencia de los códigos y la programación de reintentos de avisos pendientes. El primer envío del aviso se intenta automáticamente al guardar la contraseña.

El Blueprint usa el plan `free` para una demostración. Para uso continuo, elige el plan que necesites; los servicios gratuitos se suspenden cuando están inactivos y tienen límites de recursos. La suspensión y los reinicios no borran los datos de Neon.

## Qué ocurre al iniciar o reiniciar

El supervisor ejecuta estos pasos, en orden:

1. Valida que existan `SECRET_KEY` y `NEON_MOBILE_DATABASE_URL`.
2. Ejecuta `collectstatic --noinput` para los archivos estáticos de Django.
3. Ejecuta `migrate movil --database=movil --noinput`. Solo se aplican migraciones pendientes de la conexión móvil, sin cargar datos de demostración ni migrar la base del sistema web original.
4. Inicia Gunicorn en `127.0.0.1:8000` y Node en el puerto `PORT` de Render.

Si falla la preparación o se detiene cualquiera de los servidores, termina el contenedor. Al recibir una señal de apagado, detiene ambos procesos y los workers de Gunicorn.

`/healthz` comprueba que la entrada pública puede llegar a Django; no consulta Neon. Un resultado HTTP 200 en esa ruta no sustituye una prueba de registro o inicio de sesión con la base. Si la migración falla, revisa la conexión y los permisos de la base en los logs sin compartir sus credenciales.

Los datos de cuentas, organizaciones y negocio se conservan en Neon. La base SQLite predeterminada y el panel administrativo de Django no forman parte del almacenamiento persistente de esta aplicación.

## Probar el contenedor localmente

Necesitas Docker Desktop con el motor Linux en ejecución. Desde la carpeta que contiene el Dockerfile:

```powershell
docker build -t gestion-proyectos .
```

Crea un archivo privado `.env.render.local` con las variables `SECRET_KEY`, `NEON_MOBILE_DATABASE_URL` y `ALLOWED_HOSTS=localhost,127.0.0.1`. Ese nombre ya queda excluido de Git y Docker. Usa una base de pruebas si quieres evitar aplicar migraciones a tu base de producción. No cambies el `.env` que utilizas con Expo Go.

```powershell
docker run --rm --name gestion-proyectos -p 3000:10000 --env-file .env.render.local gestion-proyectos
```

Puedes abrir `http://localhost:3000/login` y `http://localhost:3000/healthz`. El despliegue de producción utiliza cookies `Secure`: para validar la sesión completa, usa HTTPS (como la URL de Render). Una prueba local por HTTP no representa el comportamiento de autenticación en producción.

Para detener el contenedor desde otra terminal:

```powershell
docker stop gestion-proyectos
```

Las compilaciones Android/iOS siguen necesitando su URL HTTPS de API en las variables de la plataforma; el modo de mismo origen se aplica únicamente a la versión web compilada en Docker.

Referencias: [Docker en Render](https://render.com/docs/docker), [Web Services y puertos](https://render.com/docs/web-services#port-binding), [Blueprints](https://render.com/docs/blueprint-spec), [publicación web de Expo](https://docs.expo.dev/guides/publishing-websites/).
