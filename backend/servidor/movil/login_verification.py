"""Verificación por correo antes de crear una sesión persistente."""
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import timedelta
from html import escape
from uuid import UUID

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_POST

from server.resend_service import enviar_correo_resend

from .auth import ErrorAPI, api, comprobar_base, iniciar_sesion, leer_json, limitar, respuesta
from .models import LimiteAcceso, Usuario, VerificacionLogin

logger = logging.getLogger(__name__)
ESPERA_REENVIO = 60
MAX_INTENTOS = 5
ERROR_CODIGO = "El código no es válido, venció o ya fue utilizado. Solicita uno nuevo si es necesario."


def _hmac(valor):
    return hmac.new(settings.SECRET_KEY.encode(), valor.encode(), hashlib.sha256).hexdigest()


def _credencial(usuario):
    return _hmac(f"movil-login-cuenta:{usuario.pk}:{usuario.correo}:{usuario.contrasena_hash}")


def _codigo_hash(verificacion_id, codigo):
    return _hmac(f"movil-login-codigo:{verificacion_id}:{codigo}")


def comprobar_correo_login():
    if not getattr(settings, "RESEND_API_KEY", "").strip() or not getattr(settings, "RESEND_SENDER_EMAIL", "").strip():
        raise ErrorAPI("El servicio de verificación por correo aún no está configurado en el servidor.", 503)


def _duracion():
    return max(60, min(1800, int(getattr(settings, "MOBILE_LOGIN_CODE_TTL_SECONDS", 600))))


def _limitar_envio(usuario):
    """El llamador mantiene bloqueada la cuenta; los rechazos se retornan para guardar límites."""
    if not getattr(settings, "SECURITY_RATE_LIMIT_ENABLED", True):
        return None
    ahora = int(time.time())
    espera, _ = LimiteAcceso.objects.get_or_create(
        clave_hash=_hmac(f"movil-login-espera:{usuario.pk}"), defaults={"ventana": 0})
    if ahora - espera.ventana < ESPERA_REENVIO:
        segundos = ESPERA_REENVIO - (ahora - espera.ventana)
        result = respuesta({"mensaje": "Espera un minuto antes de solicitar otro código.", "reenviar_en": segundos}, 429)
        result["Retry-After"] = str(segundos)
        return result
    ventana = ahora // 3600
    limite, _ = LimiteAcceso.objects.get_or_create(
        clave_hash=_hmac(f"movil-login-envios:{usuario.pk}"), defaults={"ventana": ventana})
    limite.cantidad = limite.cantidad + 1 if limite.ventana == ventana else 1
    limite.ventana = ventana
    limite.save(update_fields=["cantidad", "ventana", "actualizado_en"])
    if limite.cantidad > 5:
        return respuesta({"mensaje": "Demasiados códigos solicitados. Intenta de nuevo más tarde."}, 429)
    espera.ventana = ahora
    espera.save(update_fields=["ventana", "actualizado_en"])
    return None


def _correo_destino(correo):
    nombre, dominio = correo.rsplit("@", 1)
    return f"{nombre[:1]}***@{dominio}"


def iniciar_verificacion(request, usuario):
    """Requiere un bloqueo de Usuario que se conserva hasta aceptar el envío."""
    comprobar_correo_login()
    limitado = _limitar_envio(usuario)
    if limitado is not None:
        return limitado
    ahora = timezone.now()
    codigo = f"{secrets.randbelow(1000000):06d}"
    verificacion = VerificacionLogin(usuario=usuario, correo=usuario.correo,
                                     credencial_hash=_credencial(usuario),
                                     expira_en=ahora + timedelta(seconds=_duracion()))
    verificacion.codigo_hash = _codigo_hash(verificacion.id, codigo)
    # Primero comprobar y escribir la tabla. Nunca enviar un código cuya tabla
    # falta; los desafíos anteriores siguen válidos si el proveedor falla.
    verificacion.save(force_insert=True)
    minutos = max(1, _duracion() // 60)
    html = (
        f"<h1>Verifica tu inicio de sesión</h1><p>Hola, {escape(usuario.nombre_completo)}.</p>"
        "<p>Tu código para entrar a Proyectos MEG es:</p>"
        f"<p style='font-size:32px;letter-spacing:6px'><strong>{codigo}</strong></p>"
        f"<p>Vence en {minutos} minutos. Escríbelo en la aplicación para iniciar sesión.</p>"
        "<p>No compartas este código. Si no intentaste entrar, ignora este mensaje; "
        "tu sesión actual seguirá abierta hasta que se complete un nuevo inicio de sesión.</p>"
    )
    try:
        enviar_correo_resend(destinatario=usuario.correo, asunto="Código para iniciar sesión · Proyectos MEG",
                             contenido_html=html, idempotency_key=f"movil-login/{verificacion.id}")
    except (requests.RequestException, ValueError):
        verificacion.delete()
        logger.warning("No se pudo enviar el código de inicio de sesión; se conserva el desafío anterior.")
        return respuesta({"mensaje": "No pudimos enviar el código por correo. Espera un minuto e intenta de nuevo."}, 503)
    VerificacionLogin.objects.filter(usuario=usuario, usado_en__isnull=True).exclude(pk=verificacion.pk).update(usado_en=ahora)
    return respuesta({
        "requiere_verificacion": True,
        "verificacion_id": str(verificacion.id),
        "correo_destino": _correo_destino(usuario.correo),
        "expira_en": verificacion.expira_en.isoformat(),
        "reenviar_en": ESPERA_REENVIO,
        "mensaje": "Enviamos un código a tu correo. Escríbelo para completar el inicio de sesión.",
    })


def _id(datos):
    value = datos.get("verificacion_id")
    if not isinstance(value, str) or len(value) != 36:
        raise ErrorAPI(ERROR_CODIGO)
    try:
        return UUID(value)
    except ValueError:
        raise ErrorAPI(ERROR_CODIGO)


def _cuenta_vigente(verificacion, usuario):
    return bool(verificacion and usuario and usuario.activo and not verificacion.usado_en
                and hmac.compare_digest(verificacion.credencial_hash, _credencial(usuario)))


@require_POST
@api
def verificar(request):
    comprobar_base()
    limitar(request, "login_verificar", 40, 900)
    datos = leer_json(request, 2048)
    verificacion_id = _id(datos)
    codigo = datos.get("codigo")
    if not isinstance(codigo, str) or not re.fullmatch(r"[0-9]{6}", codigo):
        raise ErrorAPI("Escribe el código de seis dígitos que recibiste por correo.")
    usuario_id = VerificacionLogin.objects.filter(pk=verificacion_id).values_list("usuario_id", flat=True).first()
    with transaction.atomic(using="movil"):
        # Siempre cuenta antes de desafío, también al reenviar e iniciar sesión.
        usuario = Usuario.objects.select_for_update().filter(pk=usuario_id).first() if usuario_id else None
        verificacion = VerificacionLogin.objects.select_for_update().filter(pk=verificacion_id).first() if usuario else None
        ahora = timezone.now()
        if (not _cuenta_vigente(verificacion, usuario) or verificacion.expira_en <= ahora
                or verificacion.intentos >= MAX_INTENTOS):
            return respuesta({"mensaje": ERROR_CODIGO}, 400)
        if not hmac.compare_digest(verificacion.codigo_hash, _codigo_hash(verificacion.id, codigo)):
            verificacion.intentos += 1
            verificacion.save(update_fields=["intentos"])
            return respuesta({"mensaje": ERROR_CODIGO}, 400)
        # La creación invalida todos los desafíos y sesiones bajo el mismo bloqueo.
        return iniciar_sesion(request, usuario)


@require_POST
@api
def reenviar(request):
    comprobar_base()
    limitar(request, "login_reenviar", 20, 3600)
    datos = leer_json(request, 2048)
    verificacion_id = _id(datos)
    usuario_id = VerificacionLogin.objects.filter(pk=verificacion_id).values_list("usuario_id", flat=True).first()
    with transaction.atomic(using="movil"):
        usuario = Usuario.objects.select_for_update().filter(pk=usuario_id).first() if usuario_id else None
        verificacion = VerificacionLogin.objects.select_for_update().filter(pk=verificacion_id).first() if usuario else None
        # Permite pedir otro código después de agotar intentos o vencerlo, pero
        # no prolongar un desafío que ya fue consumido o tiene más de un día.
        if (not _cuenta_vigente(verificacion, usuario)
                or verificacion.creado_en <= timezone.now() - timedelta(days=1)):
            return respuesta({"mensaje": "Vuelve a escribir tu usuario y contraseña para solicitar un código nuevo."}, 400)
        return iniciar_verificacion(request, usuario)
