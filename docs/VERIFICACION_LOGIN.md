# Inicio de sesión con verificación por correo

Al iniciar una sesión nueva, el usuario introduce sus credenciales y después un código de seis dígitos enviado al correo de su cuenta. Crear una cuenta también requiere confirmar el código antes de entrar. Mientras conserve una sesión válida en ese dispositivo, volver a abrir la aplicación no pide contraseña ni código.

## Sesión única y persistente

- El servidor crea la sesión únicamente después de verificar el código y revoca las sesiones anteriores de esa cuenta. Solicitar un código, equivocarse o cancelar no cierra la sesión del otro dispositivo.
- Las sesiones nuevas no tienen vencimiento automático en la base de datos. Cerrar sesión o completar un nuevo inicio de sesión las revoca. Recuperar la contraseña también revoca las sesiones.
- En web se utiliza una cookie `HttpOnly`, `SameSite=Lax` y `Secure` en producción. Su duración es de 400 días y se renueva con las solicitudes autenticadas; el navegador puede borrarla o limitar su duración. Borrar las cookies exige iniciar sesión otra vez.
- Android/iOS guardan el token en Expo SecureStore y lo restauran al abrir la aplicación. No se guardan contraseñas, códigos ni desafíos en almacenamiento persistente.
- El servidor rechaza inmediatamente las peticiones de una sesión revocada. La pantalla comprueba la sesión al volver al primer plano y cada minuto mientras la aplicación permanece activa. Sin conexión no puede detectar un cambio en otro dispositivo hasta reconectarse.
- Después de cerrar sesión, cambiar de navegador o perder los datos guardados, el siguiente acceso solicita contraseña y un código nuevo. Las sesiones anteriores a este cambio conservan su fecha de vencimiento original hasta iniciar sesión nuevamente.

## Código y reenvío

El código vence en diez minutos y permite cinco intentos. El reenvío tiene una espera de un minuto y límites adicionales por cuenta y dirección IP. Los códigos se guardan como hashes y son de un solo uso; un cambio de correo o contraseña invalida los desafíos previos.

El servicio utiliza la configuración privada de Resend que ya usa la recuperación de contraseña: `RESEND_API_KEY`, `RESEND_SENDER_NAME` y `RESEND_SENDER_EMAIL` en `backend/servidor/.env`, o en el entorno del servicio desplegado. No se agregan secretos a Expo. Si no es posible enviar el correo, el inicio de sesión informa el error y no abre una sesión.

## Actualización de la base

Desde la raíz del proyecto:

```powershell
.\backend\.venv\Scripts\python.exe backend/servidor/manage.py migrate movil --database=movil
```

Esta actualización agrega los desafíos de inicio de sesión y permite que las sesiones nuevas no tengan fecha de vencimiento. No borra usuarios ni modifica sus contraseñas. El contenedor aplica las migraciones móviles al arrancar.

## Comprobación

1. Inicia sesión con una cuenta de prueba y comprueba que solicita el código antes de abrir el sistema.
2. Introduce el código recibido, cierra la aplicación y vuelve a abrirla: debe conservar la sesión.
3. Inicia sesión en otro navegador o dispositivo. La sesión anterior debe seguir funcionando hasta confirmar el código nuevo; después, sus solicitudes deben recibir `401` y la app volver al login.
4. Cierra sesión y vuelve a abrir la aplicación: debe pedir credenciales y código.
5. Comprueba un código incorrecto, un reenvío y un código utilizado anteriormente.

Las pruebas automatizadas utilizan SQLite en memoria y sustituyen el envío de correo por un servicio simulado. No envían correos reales.
