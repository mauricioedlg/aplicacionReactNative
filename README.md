# Gestión de proyectos para equipos de software

Aplicación móvil Expo SDK 58 / React Native con backend Django. Cada empresa administra sus clientes, proyectos y finanzas dentro de su organización. La aplicación tiene cuentas propias y una base Neon independiente de la del sistema web original.

## Despliegue web en Render

La web de Expo y la API Django pueden publicarse en un solo Web Service mediante el `Dockerfile` de la raíz. Sigue [la guía de despliegue en Render](docs/DESPLIEGUE_RENDER.md) para configurar los secretos, publicar el contenedor y comprobar el inicio de sesión.

## Funciones

| Área | Comportamiento |
| --- | --- |
| Cuenta | Registro, acceso con usuario/correo/teléfono y contraseña, edición de datos y cambio de contraseña. |
| Organización | Crear empresa e invitar usuarios registrados, buscar empresa y solicitar acceso, estados pendiente/rechazado/eliminado. |
| Administración | Aprobar/rechazar solicitudes, asignar rol, editar organización, buscar y retirar miembros. |
| CRM | Dashboard de ventas y conversión; registro/edición de clientes y prospectos; búsqueda por nombre, correo o teléfono y filtro de venta. |
| Proyectos | Ventas pendientes de requerimientos, captura de alcance y tablero por hacer/en progreso/completado. |
| Finanzas | Ventas cobradas completas en su fecha de venta, dashboard mensual, gastos con historial, resultados, balance y flujo de efectivo. |

| Rol | Acceso |
| --- | --- |
| Administrador | Todos los módulos, solicitudes y configuración de organización. |
| Desarrollador | Proyectos y configuración de su cuenta. |
| CRM | Clientes, ventas, dashboard CRM y configuración de su cuenta. |
| Finanzas | Indicadores, gastos, reportes y configuración de su cuenta. |

Las cuentas pendientes, rechazadas o eliminadas conservan acceso a sus datos personales. Los permisos y la organización se verifican en el servidor en cada solicitud. La navegación se actualiza cuando cambia el acceso. El CRM móvil presenta únicamente el flujo comercial del diagrama; las rutas antiguas del backend web se conservan separadas.

## Preparación local

Desde la raíz del proyecto:

```powershell
npm.cmd install
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

Si faltan los archivos de configuración, copia `.env.example` a `.env` y `backend/servidor/.env.example` a `backend/servidor/.env`. Conserva cualquier configuración privada existente.

Configura `NEON_MOBILE_DATABASE_URL` en el archivo del servidor con una base Neon nueva y `SECRET_KEY` con una clave aleatoria de al menos 50 caracteres. Instala el esquema siguiendo [backend/BASE_MOVIL.md](backend/BASE_MOVIL.md). La app no utiliza las cuentas ni los datos del sistema web y no necesita Resend para iniciar sesión.

En este workspace la base `aplicacion_movil`, su rol dedicado y las nueve tablas ya están creados y la conexión privada está configurada. Reinicia Django para cargarla; puedes comenzar creando tu cuenta desde la aplicación.

```powershell
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py migrate movil --database=movil
.\backend\.venv\Scripts\python.exe scripts/verificar-base-movil.py
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py runserver 0.0.0.0:8000
```

En otra terminal:

```powershell
npx.cmd expo start --clear
```

Si los cambios aparecen en web pero Android muestra una versión anterior, detén las sesiones anteriores de Expo con `Ctrl+C`, conserva Django encendido y cierra Expo Go completamente. Desde esta raíz, vuelve a abrir el proyecto en el emulador con:

```powershell
npm.cmd run android:limpio
```

Este comando abre Expo Go y reconstruye la caché de Metro. Comprueba que la terminal muestre esta carpeta en `Starting project at` y que aparezca `Android Bundled` al cargar la app. En un teléfono físico usa `npx.cmd expo start --go --clear` y escanea el QR recién generado, en lugar de abrir una entrada anterior de proyectos recientes. La web y Expo Go cargan el código por separado; una recarga de la web no recarga Android.

Después crea tu cuenta desde la aplicación y elige crear una organización o solicitar acceso a una existente. No se incluyen credenciales ni datos de demostración en la base.

## Conexión desde cada dispositivo

`.env` contiene únicamente el origen HTTP del backend, sin `/api` al final:

```dotenv
EXPO_PUBLIC_API_URL=http://IP_LAN_DE_LA_PC:8000
EXPO_PUBLIC_API_URL_WEB=http://localhost:8000
EXPO_PUBLIC_API_URL_ANDROID=http://10.0.2.2:8000
```

Para un teléfono físico, elimina `EXPO_PUBLIC_API_URL_ANDROID` y usa la IP LAN de la computadora en `EXPO_PUBLIC_API_URL`. Ambos dispositivos deben estar en la misma red. Si cambias de red, `npm.cmd run configurar:local` actualiza las direcciones locales conservando las variables privadas y orígenes existentes; no crea ni migra la base. `npm.cmd run diagnosticar:android` verifica la conexión desde Android mediante adb.

La variante web se abre en `http://localhost:8081`. Ese origen debe figurar en `CORS_ALLOWED_ORIGINS` del servidor. Los endpoints móviles están bajo `/api/sistema/app/`; la dirección de un backend anterior sin estas rutas no sirve para esta versión.

Si aparece «El servidor tardó demasiado», la petición de acceso llegó al límite de 60 segundos sin completarse. Metro (puerto 8081) y Django (puerto 8000) deben estar ejecutándose a la vez. Mantén Django abierto en una terminal y Expo en otra, con los comandos de preparación anteriores.

Para localizar el problema, ejecuta `npm.cmd run diagnosticar:android`: primero comprueba Django desde la PC, después el permiso de red local de Expo Go y finalmente la conexión desde el dispositivo por adb. Para una compilación propia, usa `npm.cmd run diagnosticar:android -- --package identificador.de.la.app`; si hay varios dispositivos agrega `--serial SERIAL`. Si Django no responde, inicia `runserver 0.0.0.0:8000`; si responde solo desde la PC, comprueba la dirección y la conexión del emulador. `10.0.2.2` permite al emulador acceder a la PC; un teléfono físico necesita la IP LAN. Recarga completamente la app después de cambiar `.env`.

En Android 17 (API 37), el acceso a la red local puede estar bloqueado para la app aunque las consultas de adb respondan: `nc` se ejecuta con el usuario shell, que tiene otros permisos. La app comprueba y solicita `ACCESS_LOCAL_NETWORK` antes de conectar con el backend LAN en desarrollo. Acepta el diálogo de Android. Si el permiso se denegó o el diálogo ya no aparece, abre **Ajustes > Aplicaciones > Expo Go > Permisos > Dispositivos cercanos / Red local**, permite el acceso y vuelve a abrir el proyecto. El diagnóstico detecta este caso; conceder el permiso no requiere cambiar de IP ni aumentar el timeout. Expo Go y las compilaciones debug de React Native ya declaran el permiso. Consulta la [documentación de Android sobre acceso a la red local](https://developer.android.com/privacy-and-security/local-network-permission).

Si el servidor responde pero el acceso sigue fallando, ejecuta `.\backend\.venv\Scripts\python.exe scripts/verificar-base-movil.py` para verificar Neon y sus tablas en solo lectura. Las comprobaciones HTTP sin sesión no validan la conexión con la base. La conexión móvil a PostgreSQL tiene un límite de 8 segundos por destino para evitar esperas indefinidas al conectar; ese valor no limita la duración total del login.

Android/iOS guardan el token en SecureStore y envían Bearer. El navegador utiliza cookie HttpOnly y CSRF. Las sesiones móviles usan una clave de almacenamiento y cookie propias. Las compilaciones de producción requieren una URL HTTPS.

## Validación

```powershell
npx.cmd expo lint
npx.cmd tsc --noEmit
npm.cmd test
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py test server movil --settings=servidor.mobile_test_settings --noinput
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py makemigrations movil --check --dry-run --settings=servidor.mobile_test_settings
```

Las pruebas Django usan bases SQLite en memoria y configuración aislada que no lee `.env`. Cubren aislamiento entre organizaciones, roles, registro, sesiones, aprobación y eliminación de miembros, ventas cobradas por el 100 % de su importe una sola vez, proyectos, reportes, validación monetaria y gastos históricos. La migración a pago completo se prueba preservando importes y fechas conocidas. No consultan ni modifican Neon. Las pruebas Node cubren el transporte HTTP, la configuración local y los cálculos anteriores de cotizaciones conservados en el repositorio.

Los reportes conservan el alcance original: muestran cobros y costos registrados, y señalan los importes o datos contables que faltan. No infieren saldos bancarios, impuestos ni patrimonio.

## Estructura

- `src/app/`: rutas y protección con Expo Router.
- `src/features/account/`: cuentas, organizaciones, accesos y miembros.
- `src/features/crm/clientes.tsx`: clientes y dashboard comercial.
- `src/features/projects/` y `src/features/finance/`: gestión operativa.
- `src/services/api.ts`: API móvil independiente, CSRF y SecureStore.
- `backend/servidor/movil/`: modelos, migración y API por organización.
- [backend/README.md](backend/README.md): configuración y contrato HTTP.
- [backend/BASE_MOVIL.md](backend/BASE_MOVIL.md): esquema e instalación en Neon.

Referencias: [Expo SDK 58](https://docs.expo.dev/versions/v58.0.0/), [rutas protegidas](https://docs.expo.dev/router/advanced/protected/), [SecureStore](https://docs.expo.dev/versions/v58.0.0/sdk/securestore/).
