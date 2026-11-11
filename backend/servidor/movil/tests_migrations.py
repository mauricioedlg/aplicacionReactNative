"""La eliminación del flujo de pagos conserva las ventas y sus fechas conocidas."""
from datetime import date, datetime, timezone
from decimal import Decimal

from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class FullPaymentMigrationTests(TransactionTestCase):
    databases = {'default', 'movil'}
    migrate_from = [('movil', '0001_initial')]
    migrate_to = [('movil', '0002_ventas_pago_completo')]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connections['movil'])
        self.executor.migrate(self.migrate_from)
        self.apps_before = self.executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        executor = MigrationExecutor(connections['movil'])
        executor.migrate(executor.loader.graph.leaf_nodes('movil'))
        super().tearDown()

    def test_sale_amounts_and_known_dates_survive_without_future_receipts(self):
        Cliente = self.apps_before.get_model('movil', 'Cliente')
        Organizacion = self.apps_before.get_model('movil', 'Organizacion')
        org = Organizacion.objects.using('movil').create(nombre='Migración de ventas')
        cases = [
            ({'fecha_venta': date(2020, 1, 15), 'proyecto_liquidado': True,
              'fecha_liquidacion': date(2020, 2, 15), 'fecha_anticipo': date(2020, 1, 1)},
             datetime(2020, 1, 1, tzinfo=timezone.utc), date(2020, 1, 15)),
            ({'fecha_venta': None, 'proyecto_liquidado': True, 'fecha_liquidacion': date(2020, 2, 15),
              'fecha_anticipo': date(2020, 1, 15)},
             datetime(2020, 1, 1, tzinfo=timezone.utc), date(2020, 2, 15)),
            ({'fecha_venta': None, 'fecha_anticipo': date(2020, 1, 15)},
             datetime(2020, 1, 1, tzinfo=timezone.utc), date(2020, 1, 15)),
            ({'fecha_venta': None}, datetime(2020, 1, 2, 2, tzinfo=timezone.utc), date(2020, 1, 1)),
            ({'fecha_venta': date(2999, 1, 1), 'fecha_anticipo': date(2999, 1, 2)},
             datetime(2021, 5, 2, 12, tzinfo=timezone.utc), date(2021, 5, 2)),
            ({'fecha_venta': date(2999, 1, 1), 'fecha_anticipo': date(2999, 1, 2)},
             datetime(2999, 1, 2, 12, tzinfo=timezone.utc), None),
        ]
        created = []
        for index, (fields, created_at, expected_date) in enumerate(cases):
            cliente = Cliente.objects.using('movil').create(
                organizacion=org, nombre=f'Venta {index}', correo=f'venta{index}@example.com',
                telefono='5555555555', hubo_venta=True, monto_venta=Decimal('1000.50'),
                importe_anticipo=Decimal('250.25'), descripcion_servicio='Sistema', **fields,
            )
            Cliente.objects.using('movil').filter(pk=cliente.pk).update(creado_en=created_at)
            created.append((cliente.pk, expected_date))

        self.executor = MigrationExecutor(connections['movil'])
        self.executor.migrate(self.migrate_to)
        ClienteAfter = self.executor.loader.project_state(self.migrate_to).apps.get_model('movil', 'Cliente')
        self.assertEqual(ClienteAfter.objects.using('movil').count(), len(cases))
        for cliente_id, expected_date in created:
            with self.subTest(cliente=cliente_id):
                cliente = ClienteAfter.objects.using('movil').get(pk=cliente_id)
                self.assertEqual(cliente.monto_venta, Decimal('1000.50'))
                self.assertEqual(cliente.fecha_venta, expected_date)
                self.assertTrue(cliente.hubo_venta)
        with connections['movil'].cursor() as cursor:
            columns = {column.name for column in connections['movil'].introspection.get_table_description(cursor, 'movil_cliente')}
        self.assertFalse({'importe_anticipo', 'fecha_anticipo', 'proyecto_liquidado', 'fecha_liquidacion'} & columns)
