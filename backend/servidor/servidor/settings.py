import os

from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv

from movil.database_config import configuracion_neon_movil
from .deployment import allowed_hosts_from_environment, csrf_trusted_origins_from_environment


# =========================================
# RUTAS BASE
# =========================================

BASE_DIR = Path(__file__).resolve().parent.parent


# =========================================
# VARIABLES DE ENTORNO
# =========================================

ENV_FILE = BASE_DIR / ".env"

load_dotenv(
    dotenv_path=ENV_FILE,
    override=False
)


# =========================================
# SEGURIDAD
# =========================================

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-development-key"
)


DEBUG = os.getenv(
    "DEBUG",
    "False"
).strip().lower() == "true"


ALLOWED_HOSTS = allowed_hosts_from_environment(os.environ)

# Habilitar solo cuando Gunicorn escucha en loopback detrás del Node local.
ENABLE_INTERNAL_PROXY = os.getenv("ENABLE_INTERNAL_PROXY", "False").strip().lower() == "true"


# =========================================
# APLICACIONES
# =========================================

INSTALLED_APPS = [

    "django.contrib.admin",

    "django.contrib.auth",

    "django.contrib.contenttypes",

    "django.contrib.sessions",

    "django.contrib.messages",

    "django.contrib.staticfiles",

    # CORS
    "corsheaders",

    # Aplicación propia
    "server",
    "movil",

]


# =========================================
# MIDDLEWARE
# =========================================

MIDDLEWARE = [

    "servidor.deployment.InternalProxyMiddleware",

    "django.middleware.security.SecurityMiddleware",

    "corsheaders.middleware.CorsMiddleware",

    # Protección compartida por Neon: tamaño JSON y límites de solicitudes.
    "server.security.SeguridadAPIMiddleware",

    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "server.mobile_security.SistemaCsrfMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",

]


# =========================================
# URLS
# =========================================

ROOT_URLCONF = "servidor.urls"


# =========================================
# TEMPLATES
# =========================================

TEMPLATES = [

    {

        "BACKEND":
            "django.template.backends.django.DjangoTemplates",

        "DIRS": [],

        "APP_DIRS": True,

        "OPTIONS": {

            "context_processors": [

                "django.template.context_processors.request",

                "django.contrib.auth.context_processors.auth",

                "django.contrib.messages.context_processors.messages",

            ],

        },

    },

]


# =========================================
# WSGI
# =========================================

WSGI_APPLICATION = "servidor.wsgi.application"


# =========================================
# BASE DE DATOS
# =========================================

DATABASES = {

    "default": {

        "ENGINE":
            "django.db.backends.sqlite3",

        "NAME":
            BASE_DIR / "db.sqlite3",

    }

}


# =========================================
# NEON - CONEXIÓN ADICIONAL PARA CLIENTES Y RESEÑAS
# =========================================
# Mantener SQLite como base predeterminada evita alterar las aplicaciones actuales.
# No imprimir ni enviar NEON_DATABASE_URL al frontend.
NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL", "").strip()

if NEON_DATABASE_URL:
    partes = urlsplit(NEON_DATABASE_URL)
    if partes.scheme not in ("postgresql", "postgres"):
        raise ValueError("NEON_DATABASE_URL debe empezar con postgresql://")
    DATABASES["neon"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(partes.path.lstrip("/")),
        "USER": unquote(partes.username or ""),
        "PASSWORD": unquote(partes.password or ""),
        "HOST": partes.hostname or "",
        "PORT": partes.port or 5432,
        "OPTIONS": {"sslmode": "require"},
        "CONN_MAX_AGE": 0,
    }

# Base independiente para la aplicación de organizaciones. No reutilizar la URL
# del negocio: tampoco hay fallback a SQLite ni a la conexión `neon` original.
NEON_MOBILE_DATABASE_URL = os.getenv("NEON_MOBILE_DATABASE_URL", "").strip()
mobile_database_config = configuracion_neon_movil(NEON_MOBILE_DATABASE_URL, NEON_DATABASE_URL)
if mobile_database_config:
    DATABASES["movil"] = mobile_database_config
DATABASE_ROUTERS = ["movil.router.MovilRouter"]


# =========================================
# VALIDADORES DE CONTRASEÑA
# =========================================

AUTH_PASSWORD_VALIDATORS = []


# =========================================
# IDIOMA Y ZONA HORARIA
# =========================================

LANGUAGE_CODE = "es-mx"


TIME_ZONE = "America/Monterrey"


USE_I18N = True


USE_TZ = True


# =========================================
# ARCHIVOS ESTÁTICOS
# =========================================

STATIC_URL = "static/"


STATIC_ROOT = BASE_DIR / "staticfiles"


# =========================================
# MODELOS
# =========================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# =========================================
# CORS
# =========================================

CORS_ALLOWED_ORIGINS = [

    origin.strip()

    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")

    if origin.strip()

]


CORS_URLS_REGEX = r"^/api/.*$"

# Necesario para cookies con fetch credentials='include' entre
# www.proyectosmeg.mx y api.proyectosmeg.mx (diferentes orígenes, mismo sitio).
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = csrf_trusted_origins_from_environment(os.environ, CORS_ALLOWED_ORIGINS)
CSRF_COOKIE_HTTPONLY = True


# =========================================
# BREVO
# =========================================
#
# BREVO SE CONSERVA PARA EL FORMULARIO
# DE CONTACTO NORMAL DEL SITIO.
#
# EL LEAD MAGNET NO UTILIZA BREVO.
# =========================================

BREVO_API_KEY = os.getenv(
    "BREVO_API_KEY",
    ""
).strip()


BREVO_SENDER_NAME = os.getenv(
    "BREVO_SENDER_NAME",
    "Proyectos MEG"
).strip()


BREVO_SENDER_EMAIL = os.getenv(
    "BREVO_SENDER_EMAIL",
    ""
).strip()


CONTACT_RECEIVER_EMAIL = os.getenv(
    "CONTACT_RECEIVER_EMAIL",
    ""
).strip()


# =========================================
# RESEND
# =========================================
#
# RECUPERACIÓN Y AVISOS DE CONTRASEÑA DE LA APP,
# ADEMÁS DE CORREOS DEL SISTEMA WEB Y LEAD MAGNET.
# =========================================

RESEND_API_KEY = os.getenv(
    "RESEND_API_KEY",
    ""
).strip()


RESEND_SENDER_NAME = os.getenv(
    "RESEND_SENDER_NAME",
    "Proyectos MEG"
).strip()


RESEND_SENDER_EMAIL = os.getenv(
    "RESEND_SENDER_EMAIL",
    "onboarding@resend.dev"
).strip()


# Verificación y recuperación de cuentas; secretos y envío solo en Django.
MOBILE_LOGIN_CODE_TTL_SECONDS = int(os.getenv("MOBILE_LOGIN_CODE_TTL_SECONDS", "600"))
MOBILE_PASSWORD_RESET_CODE_TTL_SECONDS = int(os.getenv(
    "MOBILE_PASSWORD_RESET_CODE_TTL_SECONDS", "600"
))
MOBILE_PASSWORD_RESET_TOKEN_TTL_SECONDS = int(os.getenv(
    "MOBILE_PASSWORD_RESET_TOKEN_TTL_SECONDS", "600"
))


# =========================================
# CORREO CORPORATIVO LEAD MAGNET
# =========================================
#
# AQUÍ LLEGARÁN LOS DATOS DEL FORMULARIO.
# =========================================

LEAD_MAGNET_RECEIVER_EMAIL = os.getenv(
    "LEAD_MAGNET_RECEIVER_EMAIL",
    ""
).strip()


# =========================================
# VARIABLES RESEND DE PRUEBA
# =========================================
#
# SE CONSERVAN POR SI LAS UTILIZAS
# EN ALGUNA OTRA PARTE DEL PROYECTO.
#
# lead_magnet() NO LAS UTILIZA.
# =========================================

RESEND_TEST_MODE = os.getenv(
    "RESEND_TEST_MODE",
    "True"
).strip().lower() == "true"


RESEND_TEST_RECEIVER_EMAIL = os.getenv(
    "RESEND_TEST_RECEIVER_EMAIL",
    ""
).strip()


# =========================================
# FRONTEND URL
# =========================================

FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "http://localhost:5173"
).strip().rstrip("/")

# =========================================================
# REFUERZO DE SEGURIDAD - SISTEMA INTERNO Y RESEÑAS
# =========================================================
# IMPORTANTE: hacer pruebas localmente antes de activar opciones HTTPS/HSTS.
SECURITY_RATE_LIMIT_ENABLED = os.getenv(
    "SECURITY_RATE_LIMIT_ENABLED", "True"
).strip().lower() == "true"

# Por defecto solo usamos REMOTE_ADDR. No confiar en X-Forwarded-For
# de usuarios arbitrarios. El administrador debe validar el proxy antes
# de establecer HTTP_CF_CONNECTING_IP o HTTP_X_FORWARDED_FOR aquí.
TRUSTED_CLIENT_IP_HEADER = "" if ENABLE_INTERNAL_PROXY else os.getenv("TRUSTED_CLIENT_IP_HEADER", "").strip()

DATA_UPLOAD_MAX_MEMORY_SIZE = 65536
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
# Docker comprueba Django por HTTP local aunque la web pública fuerce HTTPS.
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# La sesión actual del CRM usa Bearer; estas opciones protegen las
# cookies de Django (admin) y preparan una posible migración futura.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    if len(SECRET_KEY) < 32 or SECRET_KEY.startswith("django-insecure") \
       or SECRET_KEY == "pon-aqui-una-clave-secreta-larga":
        raise RuntimeError("Configura una SECRET_KEY realmente aleatoria y secreta")

    # Activar SOLO después de confirmar terminación TLS y proxy en Render.
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "False").lower() == "true"
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

# Render u otro proxy: activar SOLO si confías en cabecera inyectada
# o reescrita por el proxy de confianza; nunca aceptar sin verificar.
if os.getenv("TRUST_PROXY_HTTPS", "False").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
