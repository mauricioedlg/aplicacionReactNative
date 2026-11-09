
from django.contrib import admin
from django.urls import include, path
from .deployment import healthz


# =========================================================
# URLS PRINCIPALES DEL PROYECTO
# =========================================================

urlpatterns = [

    path("healthz", healthz, name="healthz"),

    # Panel de administración Django
    path(
        "admin/",
        admin.site.urls
    ),

    # Endpoints de nuestra aplicación
    path(
        "api/",
        include("server.urls")
    ),

]
