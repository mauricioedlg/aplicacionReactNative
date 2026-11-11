# Recuperación de contraseña con Resend

El botón **¿Olvidaste tu contraseña?**, debajo de **Entrar**, permite escribir el correo registrado, ingresar un código de seis dígitos y crear y confirmar una contraseña nueva. Al guardar, la app vuelve al inicio de sesión y envía un aviso de cambio al correo de la cuenta.

## Configuración pendiente

En desarrollo, configura estos valores en **`backend/servidor/.env`**, conservando las demás variables existentes. En Render usa **Environment** del servicio Django o del contenedor unificado:

```dotenv
RESEND_API_KEY=re_TU_API_KEY
RESEND_SENDER_NAME=Proyectos MEG
RESEND_SENDER_EMAIL=acceso@tu-dominio.com

# Opcionales; los valores predeterminados son 600 segundos (10 minutos).
MOBILE_PASSWORD_RESET_CODE_TTL_SECONDS=600
MOBILE_PASSWORD_RESET_TOKEN_TTL_SECONDS=600
```

`RESEND_SENDER_EMAIL` debe pertenecer a un [dominio verificado en Resend](https://resend.com/docs/dashboard/domains/introduction). La clave necesita permiso de envío para ese dominio. El remitente de prueba `onboarding@resend.dev` tiene restricciones de destinatarios; configura tu dominio para enviar a los usuarios de la app. Los valores de vigencia se limitan a entre 60 y 1800 segundos.

Estas variables son privadas del servidor. No agregues la API key a `.env` de Expo ni a variables `EXPO_PUBLIC_*`. No se necesita ninguna dependencia nueva, webhook, URL de redirección ni clave de Resend en Android/iOS/web.

Desde la raíz, aplica la migración pendiente y reinicia Django:

```powershell
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py migrate movil --database=movil
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py runserver 0.0.0.0:8000
```

El contenedor unificado de Render aplica las migraciones móviles automáticamente al arrancar. En un servicio existente agrega las variables anteriores y vuelve a desplegar. El código no modifica tus archivos `.env` privados ni ejecuta migraciones sobre Neon durante las pruebas.

## Comportamiento

- El código vence en diez minutos por defecto y permite cinco intentos. Solo puede verificarse una vez. El permiso para cambiar la contraseña vence también en diez minutos y solo se utiliza una vez.
- Puedes pedir otro código después de 60 segundos. Un reenvío aceptado por Resend sustituye el código anterior. Hay límites persistentes por correo e IP para evitar abuso.
- La solicitud muestra el mismo mensaje para correos registrados y desconocidos. Solo se envían códigos a cuentas activas. Si falta la configuración de Resend, el servidor devuelve un error de disponibilidad para cualquier correo.
- Si el proveedor falla al enviar el código, se conserva el mensaje genérico para no revelar si la cuenta existe; revisa los logs del servidor y la actividad de Resend, y vuelve a solicitarlo después del plazo de reenvío.
- La contraseña debe tener de 10 a 256 caracteres y cumplir las mismas reglas del registro: no ser común ni contener solo números. La confirmación se comprueba también en el servidor.
- Al cambiarla se revocan todas las sesiones previas y se eliminan los bloqueos por contraseña incorrecta. El usuario debe iniciar sesión con la nueva contraseña.
- Los códigos y permisos se almacenan únicamente como hashes; el permiso de cambio vive en memoria dentro de la app. Nunca aparece en enlaces ni en almacenamiento persistente. Si cierras o recargas la app en ese paso, inicia otra recuperación.

## Avisos de cambio y reintentos

Después de guardar la contraseña, Django intenta enviar el correo de confirmación mediante la [API HTTP de Resend](https://resend.com/docs/api-reference/emails/send-email). El correo no contiene la contraseña ni el código. Si Resend falla temporalmente, el cambio de contraseña se conserva y el aviso queda pendiente en la base; la app informa de ese estado al volver al login.

Para procesar los avisos pendientes:

```powershell
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py reenviar_notificaciones_seguridad --limite 100
```

En producción programa ese comando periódicamente, por ejemplo cada minuto, en un proceso con las mismas variables privadas y acceso a la base móvil. En Linux/contenedor usa `python backend/servidor/manage.py reenviar_notificaciones_seguridad --limite 100` desde la raíz. La app intenta el primer envío automáticamente; los reintentos después de una caída del proveedor requieren programar este comando.

## Comprobación después de configurar la clave

1. Registra o utiliza una cuenta de prueba con un correo que controles.
2. Desde el login toca **¿Olvidaste tu contraseña?**, escribe ese correo y solicita el código. Comprueba también spam.
3. Prueba un código incorrecto y luego el recibido; solo el correcto debe abrir **Nueva contraseña**.
4. Escribe contraseñas diferentes: la app debe pedir que coincidan. Guarda una contraseña válida y confirma el regreso al login y el correo de aviso.
5. Comprueba que la contraseña anterior ya no permite entrar, que las sesiones anteriores quedaron revocadas y que la contraseña nueva sí funciona.
6. Solicita un reenvío y comprueba que el código anterior ya no funciona. Verifica también el vencimiento y que un permiso utilizado no pueda repetirse.

Las pruebas automatizadas sustituyen Resend por dobles de prueba y utilizan bases SQLite en memoria. El envío real queda pendiente de tu API key y del remitente configurado.
