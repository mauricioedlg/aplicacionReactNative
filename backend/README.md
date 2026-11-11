# API de la aplicación móvil

La aplicación Django `movil` proporciona cuentas y organizaciones para empresas de software. Sus datos se almacenan exclusivamente en una base PostgreSQL/Neon independiente mediante `NEON_MOBILE_DATABASE_URL`. Las rutas y tablas del sistema web original permanecen en la aplicación `server`, con su conexión opcional `NEON_DATABASE_URL`.

## Configuración

Desde la raíz del repositorio, con Python 3.13:

```powershell
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

Crea `backend/servidor/.env` a partir del ejemplo solo si no existe. Configura:

```dotenv
NEON_MOBILE_DATABASE_URL=postgresql://ROL_MOVIL:CLAVE@HOST/aplicacion_movil?sslmode=require
SECRET_KEY=UNA_CLAVE_ALEATORIA_DE_AL_MENOS_50_CARACTERES
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,IP_LAN_DE_LA_PC
CORS_ALLOWED_ORIGINS=http://localhost:8081
SECURITY_RATE_LIMIT_ENABLED=True
```

La nueva base debe existir antes de migrar. Sigue [BASE_MOVIL.md](BASE_MOVIL.md) para revisar el esquema y crearla en Neon. La configuración impide reutilizar la base del negocio; no hay fallback a SQLite. En caso de faltar la conexión, los endpoints devuelven 503 con un mensaje de configuración.

```powershell
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py migrate movil --database=movil
.\backend\.venv\Scripts\python.exe scripts/verificar-base-movil.py
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py runserver 0.0.0.0:8000
```

SQLite se mantiene como base de las aplicaciones de Django existentes. El router envía todos los modelos móviles al alias `movil` y bloquea las migraciones de otras aplicaciones en esa conexión. No migres el esquema móvil contra `neon`.

## Contrato HTTP

Todas las rutas siguientes parten de `/api/sistema/app/`. Envía `Content-Type: application/json` en las escrituras. Los errores llevan `ok: false` y `mensaje`.

| Método y ruta | Comportamiento |
| --- | --- |
| `GET csrf/` | Token CSRF para navegador. |
| `POST registro/` o `movil/registro/` | Nombre completo, correo, teléfono, usuario y contraseña; crea cuenta y solicita verificación por correo, sin abrir sesión. |
| `POST login/` o `movil/login/` | `{usuario, contrasena}`; usuario admite nombre, correo o teléfono. Devuelve `requiere_verificacion`, `verificacion_id`, `correo_destino`, `expira_en` y `reenviar_en`. |
| `POST verificacion-login/verificar/` o `movil/verificacion-login/verificar/` | `{verificacion_id, codigo}`; confirma el correo, abre la sesión y revoca las anteriores. |
| `POST verificacion-login/reenviar/` o `movil/verificacion-login/reenviar/` | `{verificacion_id}`; envía un código nuevo tras el plazo de espera. |
| `POST recuperacion/solicitar/` o `movil/recuperacion/solicitar/` | `{correo}`; solicita un código y devuelve `recuperacion_id`, `expira_en` y `reenviar_en`. |
| `POST recuperacion/verificar/` o `movil/recuperacion/verificar/` | `{recuperacion_id, codigo}`; verifica el código y entrega `token_recuperacion` temporal. |
| `POST recuperacion/restablecer/` o `movil/recuperacion/restablecer/` | `{token_recuperacion, contrasena, confirmacion}`; cambia la contraseña, revoca sesiones y envía el aviso; `correo_enviado` indica si el proveedor lo aceptó. |
| `GET yo/` | Usuario, organización, rol y estado de acceso actuales. |
| `GET/PATCH cuenta/` | Datos personales y contraseña; cambios sensibles exigen `contrasena_actual`. |
| `POST salir/` | Revoca la sesión actual. |
| `GET organizaciones/?buscar=...` | Busca organizaciones con dos o más caracteres. |
| `POST organizaciones/` | `{nombre, descripcion, invitaciones:[{usuario,rol}]}`; crea organización y membresía admin. |
| `POST organizaciones/solicitar/` | `{organizacion_id}`; solicita acceso como colaborador. |
| `GET/PATCH organizacion/` | Consulta o modifica nombre y descripción; admin. |
| `GET organizacion/miembros/?buscar=...` | Miembros aceptados; admin. |
| `DELETE organizacion/miembros/ID/` | Retira el acceso de otro miembro; admin. |
| `GET organizacion/solicitudes/` | Solicitudes pendientes; admin. |
| `POST organizacion/solicitudes/ID/` | `{aceptar,rol}`; decide solicitud y asigna rol. |
| `GET/POST clientes/` | Búsqueda, filtro `venta=todos/si/no`, paginación y alta; CRM/admin. |
| `GET/PATCH clientes/UUID/` | Consulta o edición de cliente y venta; CRM/admin. |
| `GET dashboard-crm/` | Ventas, conversión, tendencia y ventas recientes; CRM/admin. |
| `GET gestion-ventas/` | Ventas con información de cobro; Finanzas/admin. |
| `POST gestion-ventas/UUID/liquidacion/` | Total, anticipo, fechas y liquidación; Finanzas/admin. |
| `GET proyectos/pendientes/` y `proyectos/gestion/` | Ventas sin/con requerimientos; Desarrollador/admin. |
| `GET/POST proyectos/directo/UUID/requerimientos/` | Consulta o captura inicial de hasta 100 requerimientos. |
| `GET proyectos/directo/UUID/kanban/` | Tablero del proyecto. |
| `POST proyectos/directo/UUID/requerimientos/UUID/estado/` | `por_hacer`, `en_progreso` o `completado`. |
| `GET finanzas/dashboard/?periodo=AAAA-MM` | Cobros y gastos del mes, comparativo y tendencia. |
| `GET/POST finanzas/gastos-operativos/` | Catálogo de gastos mensuales. |
| `POST/PATCH/DELETE finanzas/gastos-operativos/UUID/` | Actualiza o desactiva gasto conservando historial. |
| `GET finanzas/reportes/?periodo=AAAA-MM&tipo=...` | `estado-resultados`, `balance-general` o `flujo-efectivo`. |

El cliente contiene `nombre`, `correo`, `telefono`, `comentarios`, `hubo_venta`, `monto_venta` y `descripcion_servicio`. Una venta requiere monto positivo y descripción. Los cobros usan `importe_total`, `importe_anticipo`, `fecha_anticipo`, `proyecto_liquidado` y `fecha_liquidacion`. Las fechas no pueden estar en el futuro; los importes admiten hasta dos decimales. Una venta con cobros o requerimientos no puede volver a prospecto.

## Sesiones y acceso

Login y registro solicitan primero un código por correo. Solo `movil/verificacion-login/verificar/` entrega el token para SecureStore; la verificación web establece la cookie HttpOnly `movil_sesion`. Las demás rutas nativas reciben `Authorization: Bearer TOKEN` y las escrituras web requieren CSRF. Nunca guardes tokens en localStorage. Las sesiones nuevas duran hasta su revocación y la base conserva únicamente su hash. La cookie web dura 400 días y se renueva con el uso, sujeta a los límites del navegador. Confirmar otro inicio de sesión revoca las sesiones anteriores.

La pertenencia y el rol se consultan en cada petición. Las búsquedas y modificaciones se limitan a la organización de la sesión. Rechazar o retirar una membresía no elimina la cuenta personal. Los cambios administrativos se serializan por organización y revalidan al administrador antes de guardar.

El registro, login, verificación y recuperación tienen límites persistentes en la base móvil, además de bloqueo por fallos de contraseña. El inicio de sesión utiliza contraseña y código por correo. Los códigos de login y recuperación están separados y no son intercambiables. La [guía de verificación](../docs/VERIFICACION_LOGIN.md) describe la persistencia, el reenvío y el cambio de dispositivo; las rutas del sistema antiguo mantienen sus reglas anteriores.

Configura `RESEND_API_KEY`, `RESEND_SENDER_EMAIL` y `RESEND_SENDER_NAME` solo en el servidor. Los códigos y permisos de cambio vencen y son de un solo uso. Al restablecer la contraseña se revocan todas las sesiones. La [guía de recuperación](../docs/RECUPERACION_CONTRASENA.md) detalla variables, migración, comprobación y el comando de reintento de avisos pendientes.

## Pruebas y producción

```powershell
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py test server movil --settings=servidor.mobile_test_settings --noinput
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py makemigrations movil --check --dry-run --settings=servidor.mobile_test_settings
```

Estas pruebas no leen `.env` ni abren conexiones a Neon. Para producción configura `DEBUG=False`, HTTPS, los hosts y orígenes reales y una `SECRET_KEY` privada. El Dockerfile utiliza Gunicorn. Ejecuta la migración móvil como paso de instalación y configura la conexión nueva en las variables privadas del servicio. Solo activa `TRUST_PROXY_HTTPS` si el proxy controla y reemplaza esa cabecera.
