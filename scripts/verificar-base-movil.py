"""Verifica conexión y esquema móvil en solo lectura, sin mostrar credenciales."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/servidor'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'servidor.settings')

import django
django.setup()

from django.apps import apps
from django.db import connections
from django.db.migrations.loader import MigrationLoader
from movil.models import Cliente

if 'movil' not in connections.databases:
    print('Falta NEON_MOBILE_DATABASE_URL en backend/servidor/.env.')
    sys.exit(1)

connection = connections['movil']
connection.settings_dict.setdefault('OPTIONS', {})['connect_timeout'] = 8
models = list(apps.get_app_config('movil').get_models())
try:
    with connection.cursor() as cursor:
        cursor.execute('BEGIN READ ONLY')
        try:
            cursor.execute("SET LOCAL statement_timeout = '15s'")
            missing = []
            missing_columns = []
            retired_columns = []
            for model in models:
                table = model._meta.db_table
                cursor.execute('SELECT to_regclass(%s)', [f'public.{table}'])
                if cursor.fetchone()[0] is None:
                    missing.append(table)
                    continue
                cursor.execute('SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s', ['public', table])
                columns = {row[0] for row in cursor.fetchall()}
                missing_columns.extend(f'{table}.{field.column}' for field in model._meta.local_fields if field.column not in columns)
                if model is Cliente:
                    retired_columns = sorted(columns & {'importe_anticipo', 'fecha_anticipo', 'proyecto_liquidado', 'fecha_liquidacion'})
            cursor.execute('SELECT to_regclass(%s)', ['public.django_migrations'])
            applied = set()
            if cursor.fetchone()[0] is not None:
                cursor.execute('SELECT name FROM django_migrations WHERE app = %s', ['movil'])
                applied = {row[0] for row in cursor.fetchall()}
            pending = [name for app, name in MigrationLoader(None).graph.leaf_nodes('movil') if name not in applied]
        finally:
            cursor.execute('ROLLBACK')
    if missing or missing_columns or retired_columns or pending:
        print('Conexión móvil correcta; el esquema necesita actualizarse.')
        if missing:
            print('Faltan tablas: ' + ', '.join(missing))
        if missing_columns:
            print('Faltan columnas: ' + ', '.join(missing_columns))
        if retired_columns:
            print('Persisten campos de pagos retirados: ' + ', '.join(retired_columns))
        if pending:
            print('Migraciones pendientes: ' + ', '.join(pending))
        print('Ejecuta: python backend/servidor/manage.py migrate movil --database=movil')
        sys.exit(1)
    print(f'Base móvil conectada. {len(models)} tablas, sus columnas y la migración vigente verificadas en solo lectura. Ventas con pago completo.')
except Exception as error:
    print(f'No se pudo verificar la base móvil ({type(error).__name__}). Revisa la red y la configuración privada.')
    sys.exit(1)
finally:
    connection.close()
