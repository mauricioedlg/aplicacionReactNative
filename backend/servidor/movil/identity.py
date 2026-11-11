"""Alta de cuentas, organizaciones y aprobación de colaboradores."""
import re
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import CommonPasswordValidator, MinimumLengthValidator, NumericPasswordValidator
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .auth import COOKIE, COOKIE_PATH, ErrorAPI, acceso, api, comprobar_base, leer_json, limitar, membresia_actual, respuesta, usuario_publico
from .login_verification import comprobar_correo_login, iniciar_verificacion
from .models import Membresia, Organizacion, ROLES, Sesion, Usuario

HASH_RELLENO = make_password("clave de relleno para tiempos de respuesta")


def texto(datos, campo, maximo, requerido=True):
    value = datos.get(campo, "")
    if not isinstance(value, str) or "\x00" in value or len(value.strip()) > maximo or (requerido and not value.strip()):
        raise ErrorAPI(f"Revisa el campo {campo.replace('_', ' ')}.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ErrorAPI(f"Revisa el campo {campo.replace('_', ' ')}.")
    return value.strip()


def datos_cuenta(datos):
    nombre = texto(datos, "nombre_completo", 150)
    usuario = texto(datos, "usuario", 80).lower()
    correo = texto(datos, "correo", 254).lower()
    telefono = re.sub(r"[\s()+-]", "", texto(datos, "telefono", 40))
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,79}", usuario):
        raise ErrorAPI("El usuario debe tener de 3 a 80 caracteres, comenzar con una letra y usar letras, números, punto, guion o guion bajo.")
    try:
        validate_email(correo)
    except ValidationError:
        raise ErrorAPI("Escribe un correo válido.")
    if not re.fullmatch(r"[0-9]{7,15}", telefono):
        raise ErrorAPI("El teléfono debe tener de 7 a 15 dígitos, incluyendo la clave del país si aplica.")
    return {"nombre_completo": nombre, "usuario": usuario, "correo": correo, "telefono": telefono}


def validar_clave(clave):
    if not isinstance(clave, str) or len(clave) > 256:
        raise ErrorAPI("Escribe una contraseña de 10 a 256 caracteres.")
    if "\x00" in clave:
        raise ErrorAPI("La contraseña contiene caracteres inválidos.")
    try:
        clave.encode("utf-8")
    except UnicodeEncodeError:
        raise ErrorAPI("La contraseña contiene caracteres inválidos.")
    try:
        for validator in (MinimumLengthValidator(10), CommonPasswordValidator(), NumericPasswordValidator()):
            validator.validate(clave)
    except ValidationError:
        raise ErrorAPI("Usa una contraseña de al menos 10 caracteres que no sea común ni solo números.")


@require_GET
@ensure_csrf_cookie
def csrf(request):
    return respuesta({"csrfToken": get_token(request)})


@require_POST
@api
def registro(request):
    comprobar_base()
    limitar(request, "registro", 6, 3600)
    datos = leer_json(request, 8192)
    campos = datos_cuenta(datos)
    validar_clave(datos.get("contrasena"))
    comprobar_correo_login()
    with transaction.atomic(using="movil"):
        usuario = Usuario.objects.create(**campos, contrasena_hash=make_password(datos["contrasena"]))
        result = iniciar_verificacion(request, usuario)
        if result.status_code >= 400:
            # Permite repetir el registro si no se pudo enviar su primer código.
            transaction.set_rollback(True, using="movil")
        return result


@require_POST
@api
def login(request):
    comprobar_base()
    limitar(request, "login", 20, 900)
    datos = leer_json(request, 8192)
    identificador = texto(datos, "usuario", 254).lower()
    clave = datos.get("contrasena")
    if not isinstance(clave, str) or not 1 <= len(clave) <= 256:
        raise ErrorAPI("Completa usuario y contraseña.")
    telefono = re.sub(r"[\s()+-]", "", identificador)
    ahora = timezone.now()
    with transaction.atomic(using="movil"):
        usuario = Usuario.objects.select_for_update().filter(Q(usuario=identificador) | Q(correo=identificador) | Q(telefono=telefono)).first()
        correcto = check_password(clave, usuario.contrasena_hash if usuario else HASH_RELLENO)
        if not usuario or not usuario.activo or (usuario.bloqueado_hasta and usuario.bloqueado_hasta > ahora):
            return respuesta({"mensaje": "Usuario o contraseña incorrectos, o acceso temporalmente bloqueado."}, 401)
        if not correcto:
            usuario.intentos_fallidos += 1
            if usuario.intentos_fallidos >= 5:
                usuario.bloqueado_hasta = ahora + timedelta(minutes=15)
                usuario.intentos_fallidos = 0
            usuario.save(update_fields=["intentos_fallidos", "bloqueado_hasta", "actualizado_en"])
            return respuesta({"mensaje": "Usuario o contraseña incorrectos, o acceso temporalmente bloqueado."}, 401)
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None
        usuario.save(update_fields=["intentos_fallidos", "bloqueado_hasta", "actualizado_en"])
        return iniciar_verificacion(request, usuario)


@require_GET
@acceso()
def yo(request):
    return respuesta({"usuario": usuario_publico(request.usuario_movil, request.membresia_movil)})


@require_POST
@acceso()
def salir(request):
    request.sesion_movil.revocado_en = timezone.now()
    request.sesion_movil.save(update_fields=["revocado_en"])
    result = respuesta({})
    result.delete_cookie(COOKIE, path=COOKIE_PATH, samesite="Lax")
    return result


@require_http_methods(["GET", "PATCH"])
@acceso()
def cuenta(request):
    if request.method == "GET":
        return respuesta({"usuario": usuario_publico(request.usuario_movil)})
    datos = leer_json(request, 8192)
    if not set(datos).issubset({"nombre_completo", "usuario", "correo", "telefono", "contrasena", "contrasena_actual"}):
        raise ErrorAPI("La cuenta solo admite cambios de nombre, usuario, correo, teléfono y contraseña.")
    with transaction.atomic(using="movil"):
        usuario = Usuario.objects.select_for_update().get(pk=request.usuario_movil.pk)
        campos = datos_cuenta({campo: datos.get(campo, getattr(usuario, campo)) for campo in ("nombre_completo", "usuario", "correo", "telefono")})
        sensible = any(campos[campo] != getattr(usuario, campo) for campo in ("usuario", "correo", "telefono")) or bool(datos.get("contrasena"))
        if sensible and (not isinstance(datos.get("contrasena_actual"), str) or not check_password(datos["contrasena_actual"], usuario.contrasena_hash)):
            raise ErrorAPI("Escribe tu contraseña actual para cambiar estos datos.", 403)
        if datos.get("contrasena"):
            validar_clave(datos["contrasena"])
            usuario.contrasena_hash = make_password(datos["contrasena"])
            Sesion.objects.filter(usuario=usuario, revocado_en__isnull=True).exclude(pk=request.sesion_movil.pk).update(revocado_en=timezone.now())
        for campo, value in campos.items():
            setattr(usuario, campo, value)
        usuario.save()
        return respuesta({"usuario": usuario_publico(usuario), "mensaje": "Cuenta actualizada."})


def _sin_acceso_actual(usuario):
    if Membresia.objects.filter(usuario=usuario, estado__in=["aceptado", "pendiente"]).exists():
        raise ErrorAPI("Ya perteneces a una organización o tienes una solicitud pendiente.", 409)


def _miembro_publico(miembro):
    usuario = miembro.usuario
    return {"id": miembro.id, "usuario": {"id": usuario.id, "nombre_completo": usuario.nombre_completo, "usuario": usuario.usuario, "correo": usuario.correo, "telefono": usuario.telefono}, "rol": miembro.rol, "estado": miembro.estado}


def _bloquear_admin(request):
    """Serializa cambios administrativos y comprueba el permiso después de esperar.

    La fila de organización se bloquea antes de las membresías. Así dos admins
    no pueden eliminarse mutuamente usando permisos leídos antes del bloqueo.
    Debe llamarse dentro de transaction.atomic(using="movil").
    """
    org = Organizacion.objects.select_for_update().get(pk=request.organizacion_movil.pk)
    autorizado = Membresia.objects.filter(usuario=request.usuario_movil, organizacion=org,
                                          estado="aceptado", rol="admin").exists()
    if not autorizado:
        raise ErrorAPI("Tu acceso de administrador cambió. Actualiza la sesión.", 403)
    return org


@require_http_methods(["GET", "POST"])
@acceso()
def organizaciones(request):
    if request.method == "GET":
        buscar = request.GET.get("buscar", "").strip()[:120]
        orgs = Organizacion.objects.filter(nombre__icontains=buscar).order_by("nombre")[:25] if len(buscar) >= 2 else []
        return respuesta({"organizaciones": [{"id": str(org.id), "nombre": org.nombre, "descripcion": org.descripcion} for org in orgs]})
    datos = leer_json(request)
    nombre = texto(datos, "nombre", 120)
    descripcion = texto(datos, "descripcion", 2000, False)
    invitaciones = datos.get("invitaciones", [])
    if not isinstance(invitaciones, list) or len(invitaciones) > 30:
        raise ErrorAPI("Puedes invitar hasta 30 colaboradores al crear la organización.")
    with transaction.atomic(using="movil"):
        usuario = Usuario.objects.select_for_update().get(pk=request.usuario_movil.pk)
        _sin_acceso_actual(usuario)
        org = Organizacion.objects.create(nombre=nombre, descripcion=descripcion)
        Membresia.objects.create(usuario=usuario, organizacion=org, rol="admin", estado="aceptado")
        vistos = {usuario.usuario}
        for invitacion in invitaciones:
            if not isinstance(invitacion, dict):
                raise ErrorAPI("Revisa las invitaciones de colaboradores.")
            nombre_usuario = texto(invitacion, "usuario", 80).lower()
            rol = invitacion.get("rol")
            if rol not in ROLES or nombre_usuario in vistos:
                raise ErrorAPI("Revisa el rol y evita usuarios duplicados en las invitaciones.")
            vistos.add(nombre_usuario)
            invitado = Usuario.objects.select_for_update().filter(usuario=nombre_usuario, activo=True).first()
            if not invitado:
                raise ErrorAPI(f"El usuario {nombre_usuario} aún no tiene una cuenta.")
            _sin_acceso_actual(invitado)
            Membresia.objects.create(usuario=invitado, organizacion=org, rol=rol, estado="aceptado")
        return respuesta({"organizacion": {"id": str(org.id), "nombre": org.nombre, "descripcion": org.descripcion}, "usuario": usuario_publico(usuario)}, 201)


@require_POST
@acceso()
def solicitar(request):
    datos = leer_json(request, 8192)
    with transaction.atomic(using="movil"):
        usuario = Usuario.objects.select_for_update().get(pk=request.usuario_movil.pk)
        _sin_acceso_actual(usuario)
        org = Organizacion.objects.filter(pk=datos.get("organizacion_id")).first()
        if not org:
            raise ErrorAPI("No se encontró la organización.", 404)
        miembro, _ = Membresia.objects.update_or_create(usuario=usuario, organizacion=org, defaults={"estado": "pendiente", "rol": None})
        return respuesta({"usuario": usuario_publico(usuario, miembro), "mensaje": "Solicitud enviada. Espera la aprobación de un administrador."}, 201)


@require_http_methods(["GET", "PATCH"])
@acceso("admin")
def organizacion(request):
    org = request.organizacion_movil
    if request.method == "PATCH":
        datos = leer_json(request, 8192)
        with transaction.atomic(using="movil"):
            org = _bloquear_admin(request)
            org.nombre = texto(datos, "nombre", 120)
            org.descripcion = texto(datos, "descripcion", 2000, False)
            org.save(update_fields=["nombre", "descripcion"])
    return respuesta({"organizacion": {"id": str(org.id), "nombre": org.nombre, "descripcion": org.descripcion}})


@require_GET
@acceso("admin")
def miembros(request):
    buscar = request.GET.get("buscar", "").strip()[:150]
    registros = Membresia.objects.select_related("usuario").filter(organizacion=request.organizacion_movil, estado="aceptado")
    if buscar:
        registros = registros.filter(Q(usuario__nombre_completo__icontains=buscar) | Q(usuario__usuario__icontains=buscar))
    return respuesta({"miembros": [_miembro_publico(m) for m in registros.order_by("usuario__nombre_completo")[:100]]})


@require_http_methods(["DELETE"])
@acceso("admin")
def eliminar_miembro(request, miembro_id):
    with transaction.atomic(using="movil"):
        org = _bloquear_admin(request)
        miembro = Membresia.objects.select_for_update().filter(pk=miembro_id, organizacion=org, estado="aceptado").first()
        if not miembro:
            raise ErrorAPI("No se encontró al miembro.", 404)
        if miembro.usuario_id == request.usuario_movil.id:
            raise ErrorAPI("No puedes eliminar tu propia membresía de administrador.", 409)
        miembro.estado = "eliminado"
        miembro.save(update_fields=["estado", "actualizado_en"])
        return respuesta({"mensaje": "Miembro eliminado de la organización."})


@require_GET
@acceso("admin")
def solicitudes(request):
    registros = Membresia.objects.select_related("usuario").filter(organizacion=request.organizacion_movil, estado="pendiente").order_by("creado_en")[:100]
    return respuesta({"solicitudes": [_miembro_publico(m) for m in registros]})


@require_POST
@acceso("admin")
def decidir_solicitud(request, miembro_id):
    datos = leer_json(request, 8192)
    aceptar = datos.get("aceptar")
    if not isinstance(aceptar, bool) or (aceptar and datos.get("rol") not in ROLES):
        raise ErrorAPI("Indica si aceptas la solicitud y asigna un rol válido.")
    with transaction.atomic(using="movil"):
        org = _bloquear_admin(request)
        miembro = Membresia.objects.select_for_update().filter(pk=miembro_id, organizacion=org, estado="pendiente").first()
        if not miembro:
            raise ErrorAPI("La solicitud ya fue resuelta o no pertenece a tu organización.", 404)
        miembro.estado = "aceptado" if aceptar else "rechazado"
        miembro.rol = datos["rol"] if aceptar else None
        miembro.save(update_fields=["estado", "rol", "actualizado_en"])
        return respuesta({"mensaje": "Acceso aprobado." if aceptar else "Acceso rechazado."})
