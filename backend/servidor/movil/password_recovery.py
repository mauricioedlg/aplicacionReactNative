"""Recuperación por correo: códigos y credenciales de un solo uso."""
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import timedelta
from html import escape
from uuid import UUID, uuid4

import requests
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.views.decorators.http import require_POST

from server.resend_service import enviar_correo_resend

from .auth import COOKIE, COOKIE_PATH, ErrorAPI, api, comprobar_base, leer_json, limitar, respuesta, token_hash
from .identity import texto, validar_clave
from .models import LimiteAcceso, NotificacionSeguridad, RecuperacionContrasena, Sesion, Usuario

logger = logging.getLogger(__name__)
MENSAJE_SOLICITUD = "Si el correo corresponde a una cuenta activa, recibirás un código para recuperar tu contraseña. Revisa también tu carpeta de spam."
ERROR_CODIGO = "El código no es válido, venció o ya fue utilizado. Solicita uno nuevo si es necesario."
ERROR_TOKEN = "La autorización para cambiar la contraseña venció o ya fue utilizada. Solicita un código nuevo."
ESPERA_REENVIO = 60
MAX_INTENTOS = 5


def _hmac(valor):
    return hmac.new(settings.SECRET_KEY.encode(), valor.encode(), hashlib.sha256).hexdigest()


def _credencial(usuario):
    # Cualquier cambio de correo o contraseña invalida códigos y tokens previos.
    return _hmac(f"movil-recuperacion-cuenta:{usuario.pk}:{usuario.correo}:{usuario.contrasena_hash}")


def _codigo_hash(recuperacion_id, codigo):
    return _hmac(f"movil-recuperacion-codigo:{recuperacion_id}:{codigo}")


def _duracion(nombre):
    return max(60, min(1800, int(getattr(settings, nombre, 600))))


def _comprobar_correo():
    if not getattr(settings, "RESEND_API_KEY", "").strip() or not getattr(settings, "RESEND_SENDER_EMAIL", "").strip():
        raise ErrorAPI("El servicio de recuperación por correo aún no está configurado en el servidor.", 503)


def _limitar_correo(correo):
    """Límites compartidos por correo entre IPs, también para cuentas inexistentes."""
    if not getattr(settings, "SECURITY_RATE_LIMIT_ENABLED", True):
        return
    ahora = int(time.time())
    with transaction.atomic(using="movil"):
        clave = _hmac(f"movil-recuperacion-espera:{correo}")
        LimiteAcceso.objects.get_or_create(clave_hash=clave, defaults={"ventana": 0})
        espera = LimiteAcceso.objects.select_for_update().get(clave_hash=clave)
        if ahora - espera.ventana < ESPERA_REENVIO:
            result = respuesta({"mensaje": "Espera un minuto antes de solicitar otro código."}, 429)
            result["Retry-After"] = str(ESPERA_REENVIO - (ahora - espera.ventana))
            return result
        clave = _hmac(f"movil-recuperacion-correo:{correo}")
        ventana = ahora // 3600
        LimiteAcceso.objects.get_or_create(clave_hash=clave, defaults={"ventana": ventana})
        limite = LimiteAcceso.objects.select_for_update().get(clave_hash=clave)
        limite.cantidad = limite.cantidad + 1 if limite.ventana == ventana else 1
        limite.ventana = ventana
        limite.save(update_fields=["cantidad", "ventana", "actualizado_en"])
        if limite.cantidad > 5:
            return respuesta({"mensaje": "Demasiadas solicitudes. Intenta de nuevo más tarde."}, 429)
        espera.ventana = ahora
        espera.save(update_fields=["ventana", "actualizado_en"])
    return None


def _vigente(recuperacion, usuario, ahora, verificada=False):
    if not recuperacion or not usuario or not usuario.activo or recuperacion.usado_en:
        return False
    if not hmac.compare_digest(recuperacion.credencial_hash, _credencial(usuario)):
        return False
    if verificada:
        return bool(recuperacion.verificado_en and recuperacion.token_expira_en and recuperacion.token_expira_en > ahora)
    return recuperacion.expira_en > ahora and recuperacion.intentos < MAX_INTENTOS and not recuperacion.verificado_en


@require_POST
@api
def solicitar(request):
    comprobar_base()
    limitar(request, "recuperacion_solicitar", 20, 3600)
    datos = leer_json(request, 2048)
    correo = texto(datos, "correo", 254).lower()
    try:
        validate_email(correo)
    except ValidationError:
        raise ErrorAPI("Escribe un correo electrónico válido.")
    # Comprobar la configuración antes de consultar cuentas evita revelar su existencia.
    _comprobar_correo()
    limitado = _limitar_correo(correo)
    if limitado is not None:
        return limitado
    recuperacion_id = uuid4()
    ahora = timezone.now()
    expira_en = ahora + timedelta(seconds=_duracion("MOBILE_PASSWORD_RESET_CODE_TTL_SECONDS"))
    result = {"mensaje": MENSAJE_SOLICITUD, "recuperacion_id": str(recuperacion_id), "expira_en": expira_en.isoformat(), "reenviar_en": ESPERA_REENVIO}
    with transaction.atomic(using="movil"):
        # Siempre bloquear la cuenta antes del desafío: solicitud, verificación y cambio.
        usuario = Usuario.objects.select_for_update().filter(correo__iexact=correo, activo=True).first()
        if usuario:
            codigo = f"{secrets.randbelow(1000000):06d}"
            minutos = max(1, _duracion("MOBILE_PASSWORD_RESET_CODE_TTL_SECONDS") // 60)
            html = (
                f"<h1>Recupera tu contraseña</h1><p>Hola, {escape(usuario.nombre_completo)}.</p>"
                f"<p>Tu código de verificación de Proyectos MEG es:</p><p style='font-size:32px;letter-spacing:6px'><strong>{codigo}</strong></p>"
                f"<p>Vence en {minutos} minutos. Escríbelo en la aplicación para crear una contraseña nueva.</p>"
                "<p>No compartas este código. Si no solicitaste este cambio, ignora el mensaje; tu contraseña no ha cambiado.</p>"
            )
            try:
                enviar_correo_resend(destinatario=usuario.correo, asunto="Código para recuperar tu contraseña · Proyectos MEG", contenido_html=html,
                                     idempotency_key=f"movil-recuperacion/{recuperacion_id}")
            except (requests.RequestException, ValueError):
                # Una respuesta distinta para un fallo de entrega revelaría cuentas existentes.
                # No invalidar el código anterior si el reenvío no fue aceptado por Resend.
                logger.warning("No se pudo enviar un código de recuperación; se permite reintentar tras el límite de espera.")
            else:
                RecuperacionContrasena.objects.filter(usuario=usuario, usado_en__isnull=True).update(usado_en=ahora)
                RecuperacionContrasena.objects.create(
                    id=recuperacion_id, usuario=usuario, correo=usuario.correo,
                    credencial_hash=_credencial(usuario), codigo_hash=_codigo_hash(recuperacion_id, codigo), expira_en=expira_en,
                )
    return respuesta(result)


@require_POST
@api
def verificar(request):
    comprobar_base()
    limitar(request, "recuperacion_verificar", 40, 900)
    datos = leer_json(request, 2048)
    try:
        recuperacion_id = UUID(texto(datos, "recuperacion_id", 36))
    except (ValueError, ErrorAPI):
        return respuesta({"mensaje": ERROR_CODIGO}, 400)
    codigo = datos.get("codigo")
    if not isinstance(codigo, str) or not re.fullmatch(r"[0-9]{6}", codigo):
        raise ErrorAPI("Escribe el código de seis dígitos que recibiste por correo.")
    usuario_id = RecuperacionContrasena.objects.filter(pk=recuperacion_id).values_list("usuario_id", flat=True).first()
    with transaction.atomic(using="movil"):
        usuario = Usuario.objects.select_for_update().filter(pk=usuario_id).first() if usuario_id else None
        recuperacion = RecuperacionContrasena.objects.select_for_update().filter(pk=recuperacion_id).first() if usuario else None
        ahora = timezone.now()
        if not _vigente(recuperacion, usuario, ahora):
            return respuesta({"mensaje": ERROR_CODIGO}, 400)
        if not hmac.compare_digest(recuperacion.codigo_hash, _codigo_hash(recuperacion_id, codigo)):
            recuperacion.intentos += 1
            if recuperacion.intentos >= MAX_INTENTOS:
                recuperacion.usado_en = ahora
            recuperacion.save(update_fields=["intentos", "usado_en"])
            # Retornar dentro del atomic conserva el contador de fallos.
            return respuesta({"mensaje": ERROR_CODIGO}, 400)
        token = secrets.token_urlsafe(32)
        recuperacion.verificado_en = ahora
        recuperacion.token_hash = token_hash(token)
        recuperacion.token_expira_en = ahora + timedelta(seconds=_duracion("MOBILE_PASSWORD_RESET_TOKEN_TTL_SECONDS"))
        recuperacion.save(update_fields=["verificado_en", "token_hash", "token_expira_en"])
        return respuesta({"token_recuperacion": token, "expira_en": recuperacion.token_expira_en.isoformat()})


def enviar_notificacion(notificacion_id):
    """Intento inmediato y comando de reintentos comparten bloqueo e idempotencia."""
    with transaction.atomic(using="movil"):
        aviso = NotificacionSeguridad.objects.select_for_update().filter(pk=notificacion_id).first()
        if not aviso:
            return False
        if aviso.enviado_en:
            return True
        ahora = timezone.now()
        if aviso.reintentar_en > ahora:
            return False
        aviso.intentos += 1
        aviso.ultimo_intento_en = ahora
        fecha = timezone.localtime(aviso.creado_en).strftime("%d/%m/%Y %H:%M %Z")
        html = (
            f"<h1>Tu contraseña fue cambiada</h1><p>Hola, {escape(aviso.nombre)}.</p>"
            f"<p>La contraseña de tu cuenta de Proyectos MEG se cambió el {escape(fecha)}.</p>"
            "<p>Por seguridad, cerramos las sesiones anteriores. Ya puedes iniciar sesión con tu contraseña nueva.</p>"
            "<p>Si no reconoces este cambio, recupera tu cuenta desde la aplicación y contacta al administrador de tu organización.</p>"
        )
        try:
            enviar_correo_resend(destinatario=aviso.destinatario, asunto="Tu contraseña fue cambiada · Proyectos MEG", contenido_html=html,
                                 idempotency_key=f"movil-contrasena/{aviso.id}")
        except (requests.RequestException, ValueError):
            aviso.reintentar_en = ahora + timedelta(seconds=min(3600, 60 * (2 ** min(aviso.intentos - 1, 6))))
            logger.warning("Confirmación de contraseña pendiente de reintento: %s", aviso.id)
        else:
            aviso.enviado_en = ahora
        aviso.save(update_fields=["intentos", "ultimo_intento_en", "reintentar_en", "enviado_en"])
        return aviso.enviado_en is not None


@require_POST
@api
def restablecer(request):
    comprobar_base()
    limitar(request, "recuperacion_restablecer", 20, 900)
    datos = leer_json(request, 4096)
    token = datos.get("token_recuperacion")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return respuesta({"mensaje": ERROR_TOKEN}, 410)
    validar_clave(datos.get("contrasena"))
    if not isinstance(datos.get("confirmacion"), str) or datos["contrasena"] != datos["confirmacion"]:
        raise ErrorAPI("Las contraseñas no coinciden.")
    digest = token_hash(token)
    usuario_id = RecuperacionContrasena.objects.filter(token_hash=digest).values_list("usuario_id", flat=True).first()
    with transaction.atomic(using="movil"):
        usuario = Usuario.objects.select_for_update().filter(pk=usuario_id).first() if usuario_id else None
        recuperacion = RecuperacionContrasena.objects.select_for_update().filter(token_hash=digest).first() if usuario else None
        ahora = timezone.now()
        if not _vigente(recuperacion, usuario, ahora, verificada=True):
            return respuesta({"mensaje": ERROR_TOKEN}, 410)
        if check_password(datos["contrasena"], usuario.contrasena_hash):
            raise ErrorAPI("Elige una contraseña diferente de la anterior.")
        usuario.contrasena_hash = make_password(datos["contrasena"])
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None
        usuario.save(update_fields=["contrasena_hash", "intentos_fallidos", "bloqueado_hasta", "actualizado_en"])
        Sesion.objects.filter(usuario=usuario, revocado_en__isnull=True).update(revocado_en=ahora)
        RecuperacionContrasena.objects.filter(usuario=usuario, usado_en__isnull=True).update(usado_en=ahora)
        aviso = NotificacionSeguridad.objects.create(usuario=usuario, destinatario=usuario.correo, nombre=usuario.nombre_completo, reintentar_en=ahora)
    # El cambio y la notificación pendiente ya están guardados. Una caída de Resend
    # no deshace la contraseña nueva ni pierde el aviso: el comando puede reenviarlo.
    try:
        correo_enviado = enviar_notificacion(aviso.id)
    except DatabaseError:
        logger.warning("Confirmación de contraseña guardada y pendiente de reintento: %s", aviso.id)
        correo_enviado = False
    result = respuesta({"mensaje": "Tu contraseña fue cambiada. Inicia sesión con tu contraseña nueva.", "correo_enviado": correo_enviado})
    result.delete_cookie(COOKIE, path=COOKIE_PATH, samesite="Lax")
    return result
