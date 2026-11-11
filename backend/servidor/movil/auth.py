"""Sesiones y permisos de la aplicación móvil, independientes de la web."""
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from functools import wraps

from django.conf import settings
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.db import DatabaseError, IntegrityError, connections, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.middleware.csrf import rotate_token
from django.utils import timezone

from .models import LimiteAcceso, Membresia, Sesion, Usuario, VerificacionLogin

logger = logging.getLogger(__name__)
COOKIE = "movil_sesion"
COOKIE_PATH = "/api/sistema/app/"
# Los navegadores limitan la duración de las cookies. Se renueva al usar la app;
# la sesión del servidor permanece vigente hasta su revocación explícita.
DURACION_COOKIE_DIAS = 400


class ErrorAPI(ValueError):
    def __init__(self, mensaje, status=400):
        super().__init__(mensaje)
        self.status = status


def respuesta(datos, status=200):
    result = JsonResponse({"ok": status < 400, **datos}, status=status)
    result["Cache-Control"] = "no-store"
    result["Pragma"] = "no-cache"
    return result


def leer_json(request, max_bytes=65536):
    if request.content_type != "application/json":
        raise ErrorAPI("Envía un objeto JSON.", 415)
    if len(request.body) > max_bytes:
        raise ErrorAPI("Solicitud demasiado grande.", 413)
    try:
        datos = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ErrorAPI("JSON inválido.")
    if not isinstance(datos, dict):
        raise ErrorAPI("Se esperaba un objeto JSON.")
    return datos


def api(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except RequestDataTooBig:
            return respuesta({"mensaje": "Solicitud demasiado grande."}, 413)
        except ErrorAPI as exc:
            return respuesta({"mensaje": str(exc)}, exc.status)
        except (ValueError, ValidationError):
            return respuesta({"mensaje": "Revisa los datos enviados."}, 400)
        except IntegrityError:
            return respuesta({"mensaje": "Estos datos ya están registrados o el registro cambió. Actualiza e intenta de nuevo."}, 409)
        except DatabaseError:
            logger.exception("No se pudo completar una operación de la base móvil.")
            return respuesta({"mensaje": "La base móvil no está disponible. Revisa su conexión e instalación."}, 503)
    return wrapped


def comprobar_base():
    if "movil" not in connections.databases:
        raise ErrorAPI("Configura NEON_MOBILE_DATABASE_URL en el servidor para habilitar la base móvil.", 503)
    if len(settings.SECRET_KEY) < 32 or settings.SECRET_KEY.startswith("django-insecure"):
        raise ErrorAPI("Configura una SECRET_KEY segura en el servidor.", 503)


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extraer_token(request):
    if "HTTP_AUTHORIZATION" in request.META:
        match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{32,160})", request.META.get("HTTP_AUTHORIZATION", ""), re.I)
        return match.group(1) if match else None
    value = request.COOKIES.get(COOKIE, "")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{32,160}", value) else None


def membresia_actual(usuario):
    base = Membresia.objects.select_related("organizacion").filter(usuario=usuario)
    return base.filter(estado__in=["aceptado", "pendiente"]).first() or base.order_by("-actualizado_en", "-id").first()


def usuario_publico(usuario, membresia=None):
    if membresia is None:
        membresia = membresia_actual(usuario)
    org = membresia.organizacion if membresia else None
    return {
        "id": usuario.id, "nombre_completo": usuario.nombre_completo,
        "usuario": usuario.usuario, "correo": usuario.correo, "telefono": usuario.telefono,
        "rol": membresia.rol if membresia and membresia.estado == "aceptado" else None,
        "estado_acceso": membresia.estado if membresia else "sin_organizacion",
        "organizacion": {"id": str(org.id), "nombre": org.nombre, "descripcion": org.descripcion} if org else None,
    }


def acceso(*roles):
    """Sin roles: cuenta autenticada; con roles: miembro aceptado del módulo."""
    def decorator(view):
        @api
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            comprobar_base()
            token = extraer_token(request)
            sesion = Sesion.objects.select_related("usuario").filter(
                Q(expira_en__isnull=True) | Q(expira_en__gt=timezone.now()),
                token_hash=token_hash(token), revocado_en__isnull=True,
                usuario__activo=True,
            ).first() if token else None
            if not sesion:
                return respuesta({"mensaje": "Inicia sesión para continuar."}, 401)
            membresia = membresia_actual(sesion.usuario)
            request.sesion_movil = sesion
            request.usuario_movil = sesion.usuario
            request.membresia_movil = membresia
            request.organizacion_movil = membresia.organizacion if membresia and membresia.estado == "aceptado" else None
            if roles and (not membresia or membresia.estado != "aceptado" or membresia.rol not in ("admin", *roles)):
                return respuesta({"mensaje": "Tu cuenta no tiene acceso a este módulo de la organización."}, 403)
            result = view(request, *args, **kwargs)
            if (result.status_code < 400 and "HTTP_AUTHORIZATION" not in request.META
                    and COOKIE not in result.cookies and not sesion.revocado_en):
                establecer_cookie(result, token)
            return result
        return wrapped
    return decorator


def limitar(request, tipo, limite=20, segundos=900):
    if not getattr(settings, "SECURITY_RATE_LIMIT_ENABLED", True):
        return
    # No confiar en cabeceras de IP proporcionadas por el cliente.
    ip = request.META.get("REMOTE_ADDR", "desconocida")
    clave = hmac.new(settings.SECRET_KEY.encode(), f"movil:{tipo}:{ip}".encode(), hashlib.sha256).hexdigest()
    ventana = int(time.time()) // segundos
    with transaction.atomic(using="movil"):
        LimiteAcceso.objects.get_or_create(clave_hash=clave, defaults={"ventana": ventana})
        fila = LimiteAcceso.objects.select_for_update().get(clave_hash=clave)
        fila.cantidad = fila.cantidad + 1 if fila.ventana == ventana else 1
        fila.ventana = ventana
        fila.save(update_fields=["cantidad", "ventana", "actualizado_en"])
    if fila.cantidad > limite:
        raise ErrorAPI("Demasiadas solicitudes. Intenta de nuevo más tarde.", 429)


def establecer_cookie(result, token):
    result.set_cookie(COOKIE, token, max_age=DURACION_COOKIE_DIAS * 86400,
                      path=COOKIE_PATH, secure=not settings.DEBUG, httponly=True, samesite="Lax")


def iniciar_sesion(request, usuario):
    """Solo se llama tras verificar el correo; serializa reemplazos de sesión."""
    with transaction.atomic(using="movil"):
        usuario = Usuario.objects.select_for_update().get(pk=usuario.pk)
        ahora = timezone.now()
        token = secrets.token_urlsafe(48)
        Sesion.objects.filter(usuario=usuario, revocado_en__isnull=True).update(revocado_en=ahora)
        VerificacionLogin.objects.filter(usuario=usuario, usado_en__isnull=True).update(usado_en=ahora)
        Sesion.objects.create(usuario=usuario, token_hash=token_hash(token), expira_en=None)
        datos = {"usuario": usuario_publico(usuario)}
        native = request.path.startswith("/api/sistema/app/movil/")
        if native:
            datos["token"] = token
        result = respuesta(datos)
        if not native:
            rotate_token(request)
            establecer_cookie(result, token)
        return result
