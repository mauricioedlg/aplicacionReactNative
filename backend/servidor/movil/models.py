from uuid import uuid4

from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


ROLES = ("admin", "desarrollador", "crm", "finanzas")
ESTADOS_ACCESO = ("pendiente", "aceptado", "rechazado", "eliminado")
ESTADOS_REQUERIMIENTO = ("por_hacer", "en_progreso", "completado")


class Usuario(models.Model):
    nombre_completo = models.CharField(max_length=150)
    usuario = models.CharField(max_length=80, unique=True)
    correo = models.EmailField(max_length=254, unique=True)
    telefono = models.CharField(max_length=15, unique=True)
    contrasena_hash = models.CharField(max_length=255)
    activo = models.BooleanField(default=True)
    intentos_fallidos = models.PositiveSmallIntegerField(default=0)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("usuario"), name="movil_usuario_minusculas_unico"),
            models.UniqueConstraint(Lower("correo"), name="movil_correo_minusculas_unico"),
        ]


class Organizacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    nombre = models.CharField(max_length=120)
    descripcion = models.CharField(max_length=2000, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("nombre"), name="movil_organizacion_nombre_unico")]


class Membresia(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="membresias")
    organizacion = models.ForeignKey(Organizacion, on_delete=models.PROTECT, related_name="membresias")
    rol = models.CharField(max_length=20, null=True, blank=True, choices=[(r, r) for r in ROLES])
    estado = models.CharField(max_length=20, default="pendiente", choices=[(e, e) for e in ESTADOS_ACCESO])
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["usuario", "organizacion"], name="movil_miembro_organizacion_unico"),
            models.UniqueConstraint(fields=["usuario"], condition=Q(estado__in=["pendiente", "aceptado"]), name="movil_usuario_organizacion_activa"),
            models.CheckConstraint(condition=Q(estado__in=ESTADOS_ACCESO), name="movil_estado_membresia_valido"),
            models.CheckConstraint(condition=Q(rol__isnull=True) | Q(rol__in=ROLES), name="movil_rol_valido"),
            models.CheckConstraint(condition=~Q(estado="aceptado") | Q(rol__isnull=False), name="movil_miembro_aceptado_tiene_rol"),
        ]


class Sesion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="sesiones")
    token_hash = models.CharField(max_length=64, unique=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    expira_en = models.DateTimeField(db_index=True, null=True, blank=True)
    revocado_en = models.DateTimeField(null=True, blank=True)


class VerificacionLogin(models.Model):
    """Prueba de correo de un solo uso, posterior a validar la contraseña."""
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="verificaciones_login")
    correo = models.EmailField(max_length=254)
    credencial_hash = models.CharField(max_length=64)
    codigo_hash = models.CharField(max_length=64)
    creado_en = models.DateTimeField(auto_now_add=True)
    expira_en = models.DateTimeField(db_index=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    usado_en = models.DateTimeField(null=True, blank=True)


class LimiteAcceso(models.Model):
    clave_hash = models.CharField(max_length=64, unique=True)
    ventana = models.BigIntegerField()
    cantidad = models.PositiveIntegerField(default=0)
    actualizado_en = models.DateTimeField(auto_now=True)


class RecuperacionContrasena(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="recuperaciones")
    correo = models.EmailField(max_length=254)
    credencial_hash = models.CharField(max_length=64)
    codigo_hash = models.CharField(max_length=64)
    creado_en = models.DateTimeField(auto_now_add=True)
    expira_en = models.DateTimeField(db_index=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    verificado_en = models.DateTimeField(null=True, blank=True)
    token_hash = models.CharField(max_length=64, null=True, blank=True, unique=True)
    token_expira_en = models.DateTimeField(null=True, blank=True)
    usado_en = models.DateTimeField(null=True, blank=True)


class NotificacionSeguridad(models.Model):
    """Confirmación de cambio de contraseña pendiente o aceptada por Resend."""
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="notificaciones_seguridad")
    destinatario = models.EmailField(max_length=254)
    nombre = models.CharField(max_length=150)
    creado_en = models.DateTimeField(auto_now_add=True)
    enviado_en = models.DateTimeField(null=True, blank=True)
    intentos = models.PositiveIntegerField(default=0)
    ultimo_intento_en = models.DateTimeField(null=True, blank=True)
    reintentar_en = models.DateTimeField(db_index=True)


class Cliente(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organizacion = models.ForeignKey(Organizacion, on_delete=models.PROTECT, related_name="clientes")
    nombre = models.CharField(max_length=150)
    correo = models.EmailField(max_length=254)
    telefono = models.CharField(max_length=25)
    comentarios = models.CharField(max_length=2000, blank=True)
    hubo_venta = models.BooleanField(default=False)
    monto_venta = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    descripcion_servicio = models.CharField(max_length=2000, blank=True)
    fecha_venta = models.DateField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("correo"), "organizacion", name="movil_cliente_correo_organizacion_unico"),
            models.CheckConstraint(condition=Q(monto_venta__isnull=True) | Q(monto_venta__gt=0), name="movil_monto_venta_positivo"),
            models.CheckConstraint(condition=~Q(hubo_venta=True) | Q(monto_venta__isnull=False), name="movil_venta_tiene_importe"),
        ]
        indexes = [models.Index(fields=["organizacion", "hubo_venta", "creado_en"])]


class Requerimiento(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="requerimientos")
    orden = models.PositiveSmallIntegerField()
    titulo = models.CharField(max_length=150)
    descripcion = models.CharField(max_length=2000, blank=True)
    estado = models.CharField(max_length=20, default="por_hacer", choices=[(e, e) for e in ESTADOS_REQUERIMIENTO])
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["orden"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "orden"], name="movil_requerimiento_orden_unico"),
            models.CheckConstraint(condition=Q(orden__gte=1, orden__lte=100), name="movil_requerimiento_orden_valido"),
            models.CheckConstraint(condition=Q(estado__in=ESTADOS_REQUERIMIENTO), name="movil_requerimiento_estado_valido"),
        ]


class GastoOperativo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organizacion = models.ForeignKey(Organizacion, on_delete=models.PROTECT, related_name="gastos")
    concepto = models.CharField(max_length=150)
    monto_mensual = models.DecimalField(max_digits=12, decimal_places=2)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(monto_mensual__gte=0), name="movil_gasto_monto_no_negativo")]


class GastoHistorial(models.Model):
    gasto = models.ForeignKey(GastoOperativo, on_delete=models.PROTECT, related_name="historial")
    vigente_desde = models.DateField()
    concepto = models.CharField(max_length=150)
    monto_mensual = models.DecimalField(max_digits=12, decimal_places=2)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["gasto", "vigente_desde"], name="movil_gasto_historial_mes_unico"),
            models.CheckConstraint(condition=Q(monto_mensual__gte=0), name="movil_gasto_historial_no_negativo"),
        ]
