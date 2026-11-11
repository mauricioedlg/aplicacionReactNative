"""Reintenta los avisos persistentes de cambio de contraseña."""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from movil.auth import ErrorAPI, comprobar_base
from movil.models import NotificacionSeguridad
from movil.password_recovery import enviar_notificacion


class Command(BaseCommand):
    help = "Reintenta confirmaciones pendientes de cambio de contraseña con Resend."

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=100)

    def handle(self, *args, **options):
        limite = options["limite"]
        if not 1 <= limite <= 1000:
            raise CommandError("El límite debe estar entre 1 y 1000.")
        try:
            comprobar_base()
        except ErrorAPI as exc:
            raise CommandError(str(exc)) from exc
        ids = list(NotificacionSeguridad.objects.filter(enviado_en__isnull=True, reintentar_en__lte=timezone.now())
                   .order_by("creado_en").values_list("id", flat=True)[:limite])
        enviados = sum(enviar_notificacion(notificacion_id) for notificacion_id in ids)
        self.stdout.write(f"Notificaciones revisadas: {len(ids)}; enviadas: {enviados}; pendientes: {len(ids) - enviados}.")
