from django.urls import path

from . import identity, login_verification, password_recovery

urlpatterns = [
    path("csrf/", identity.csrf),
    path("registro/", identity.registro),
    path("movil/registro/", identity.registro),
    path("login/", identity.login),
    path("movil/login/", identity.login),
    path("verificacion-login/verificar/", login_verification.verificar),
    path("verificacion-login/reenviar/", login_verification.reenviar),
    path("movil/verificacion-login/verificar/", login_verification.verificar),
    path("movil/verificacion-login/reenviar/", login_verification.reenviar),
    path("recuperacion/solicitar/", password_recovery.solicitar),
    path("recuperacion/verificar/", password_recovery.verificar),
    path("recuperacion/restablecer/", password_recovery.restablecer),
    path("movil/recuperacion/solicitar/", password_recovery.solicitar),
    path("movil/recuperacion/verificar/", password_recovery.verificar),
    path("movil/recuperacion/restablecer/", password_recovery.restablecer),
    path("yo/", identity.yo),
    path("salir/", identity.salir),
    path("cuenta/", identity.cuenta),
    path("organizaciones/", identity.organizaciones),
    path("organizaciones/solicitar/", identity.solicitar),
    path("organizacion/", identity.organizacion),
    path("organizacion/miembros/", identity.miembros),
    path("organizacion/miembros/<int:miembro_id>/", identity.eliminar_miembro),
    path("organizacion/solicitudes/", identity.solicitudes),
    path("organizacion/solicitudes/<int:miembro_id>/", identity.decidir_solicitud),
]
