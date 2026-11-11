import requests

from django.conf import settings


# =========================================================
# RESEND
# =========================================================

RESEND_EMAILS_URL = "https://api.resend.com/emails"


# =========================================================
# ENVIAR CORREO CON RESEND
# =========================================================

def enviar_correo_resend(
    destinatario,
    asunto,
    contenido_html,
    reply_to=None,
    idempotency_key=None,
):

    """
    Envía un correo utilizando la API HTTP de Resend.

    destinatario:
        Correo que recibe el mensaje.

    asunto:
        Asunto del correo.

    contenido_html:
        Contenido HTML del correo.

    reply_to:
        Correo al que se enviará la respuesta cuando
        presiones "Responder".
    """


    # =====================================================
    # VALIDAR API KEY
    # =====================================================

    if not settings.RESEND_API_KEY:

        raise ValueError(
            "RESEND_API_KEY no está configurada."
        )


    # =====================================================
    # VALIDAR REMITENTE
    # =====================================================

    if not settings.RESEND_SENDER_EMAIL:

        raise ValueError(
            "RESEND_SENDER_EMAIL no está configurado."
        )


    # =====================================================
    # VALIDAR DESTINATARIO
    # =====================================================

    if not destinatario:

        raise ValueError(
            "El destinatario de Resend está vacío."
        )


    # =====================================================
    # HEADERS
    # =====================================================

    headers = {

        "Authorization":
            f"Bearer {settings.RESEND_API_KEY}",

        "Content-Type":
            "application/json",

    }


    # =====================================================
    # REMITENTE
    # =====================================================

    remitente = (
        f"{settings.RESEND_SENDER_NAME} "
        f"<{settings.RESEND_SENDER_EMAIL}>"
    )


    # =====================================================
    # PAYLOAD
    # =====================================================

    payload = {

        "from":
            remitente,

        "to": [
            destinatario
        ],

        "subject":
            asunto,

        "html":
            contenido_html,

    }


    # =====================================================
    # REPLY TO
    # =====================================================

    if reply_to:

        payload["reply_to"] = reply_to

    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key


    # =====================================================
    # PETICIÓN A RESEND
    # =====================================================

    response = requests.post(

        RESEND_EMAILS_URL,

        headers=headers,

        json=payload,

        timeout=15

    )


    # =====================================================
    # VALIDAR RESPUESTA
    # =====================================================

    if not response.ok:

        raise requests.RequestException(
            (
                f"Resend respondió con status "
                f"{response.status_code}: "
                f"{response.text}"
            )
        )


    # =====================================================
    # RESPUESTA
    # =====================================================

    return response.json()
