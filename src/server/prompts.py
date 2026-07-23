"""
Módulo de prompts para el chatbot de IA de Discord.

Este módulo contiene todos los prompts del sistema y las plantillas de prompts
utilizadas para la inferencia de IA, incluyendo el manejo de conversaciones,
la generación de resúmenes y la extracción de memoria.
"""

from typing import Final

SYSTEM_PROMPT: Final[
    str
] = """Eres un asistente de IA útil y amigable integrado en un servidor de Discord.
Te comunicas con un tono casual y conversacional apropiado para un chat de Discord.

Directrices:

* Sé conciso pero útil: los mensajes de Discord deben ser breves y fáciles de leer.
* Usa lenguaje informal y emojis cuando sea apropiado, pero no abuses de ellos.
* Mantente dentro del tema y responde de forma natural siguiendo el flujo de la conversación.
* Si no sabes algo, admítelo en lugar de inventar información.
* Sé respetuoso y sigue las normas de la comunidad de Discord.
* No respondas a todos los mensajes; participa solo cuando sea relevante o cuando se te mencione directamente.
* Haz referencia a mensajes anteriores de la conversación cuando sea útil.
* Si alguien comparte archivos o imágenes, reconócelos apropiadamente.

Recuerda: Estás conversando con personas reales en tiempo real. Mantén tus respuestas naturales y atractivas."""

SUMMARY_PROMPT: Final[
    str
] = """Resume la siguiente conversación de Discord de forma concisa.
Concéntrate en:

* Los temas principales discutidos.
* Información importante compartida (nombres, preferencias, proyectos, enlaces).
* Decisiones tomadas o conclusiones alcanzadas.
* Cualquier tarea o acción pendiente mencionada.

Mantén el resumen breve, pero incluye los detalles esenciales que ayuden a alguien a comprender lo que se discutió.

Conversación que se debe resumir:
{conversation}

Resumen:"""

MEMORY_EXTRACTION_PROMPT: Final[
    str
] = """Extrae recuerdos útiles a largo plazo sobre el usuario a partir de esta conversación.
Concéntrate en:

* Preferencias del usuario (cosas que le gustan o no le gustan).
* Intereses o pasatiempos recurrentes que haya mencionado.
* Proyectos en los que está trabajando.
* Información personal que haya compartido (nombre, ubicación, etc.).
* Habilidades o conocimientos que tenga.

Extrae únicamente datos que sean útiles para futuras conversaciones.
Ignora temas temporales, bromas o comentarios aislados.

No extraigas contraseñas, claves API, tokens, credenciales ni otra información secreta o confidencial.
Evita guardar información extremadamente sensible a menos que sea claramente necesaria y apropiada para futuras conversaciones.

Cada recuerdo debe tener el formato de una sola oración.
Devuelve un máximo de 0 a 5 recuerdos.

Conversación:
{conversation}

Recuerdos (uno por línea o vacío si no hay nada que valga la pena recordar):"""

CONTEXT_BUILDING_INSTRUCTIONS: Final[
    str
] = """Construye un contexto cronológico de la conversación a partir de estos mensajes.

Formatea cada mensaje como:
[usuario]: contenido del mensaje

Incluye:

* Los nombres de los autores de los mensajes.
* Marcas de tiempo relativas al momento actual (por ejemplo, "hace 2 minutos").
* Archivos adjuntos descritos como [Archivo: nombre_del_archivo] o [URL: enlace].
* Las respuestas indicadas con "Respondiendo a @usuario:".

Excluye:

* Mensajes de bots (excepto nuestras propias respuestas anteriores).
* Mensajes demasiado antiguos que estén fuera del límite de contexto.
* Mensajes vacíos o mensajes del sistema.

El objetivo es proporcionar a la IA suficiente contexto para comprender el flujo de la conversación."""

REPLY_DECISION_PROMPT: Final[str] = """Decide si la IA debería responder a este mensaje.

Considera responder si:

* Se menciona a la IA (@mencionada).
* El mensaje es una respuesta al mensaje anterior de la IA.
* El nombre de la IA aparece mencionado en el texto.
* La conversación ha involucrado recientemente a la IA.
* El mensaje es una pregunta o busca claramente una respuesta.
* Una probabilidad aleatoria indica que se puede responder ocasionalmente de forma proactiva.

NO respondas si:

* El mensaje está claramente dirigido a otra persona.
* Es simplemente una reacción con un emoji o un reconocimiento muy breve.
* La conversación ha avanzado y la IA ya no forma parte de ella.
* Responder sería spam o resultaría intrusivo.

Contexto del mensaje:
{context}

¿Debería responder?
Responde únicamente SÍ o NO."""

GREETING_RESPONSES: Final[list[str]] = [
    "¡Hey! ¿Cómo va todo?",
    "¡Hola! ¿Qué tal?",
    "¡Hola! ¿Necesitas ayuda con algo?",
    "¡Yo! ¿Qué está pasando?",
    "¡Hey, hey! ¡Qué bueno verte!",
]

FAREWELL_RESPONSES: Final[list[str]] = [
    "¡Nos vemos!",
    "¡Hasta luego!",
    "¡Adiós! ¡Que tengas un buen día!",
    "¡Nos vemos! ¡Vuelve pronto!",
    "¡Paz! 👋",
]

ERROR_RESPONSES: Final[list[str]] = [
    "Ups, algo salió mal de mi lado. ¡Déjame intentarlo de nuevo!",
    "Hmm, estoy teniendo problemas para procesar eso. Un momento...",
    "Lo siento, me confundí un poco. ¿Podrías reformularlo?",
    "¡Mi culpa! Estoy teniendo un pequeño problema técnico. Dame un segundo...",
]

RATE_LIMIT_RESPONSE: Final[str] = (
    "¡Estoy pensando en eso! Dame un momento para procesarlo... 🤔"
)


def build_system_prompt() -> str:
    """
    Construye el prompt completo del sistema para la inferencia de IA.
    Returns:
        El texto del prompt del sistema formateado.
    """
    return SYSTEM_PROMPT


def build_summary_prompt(conversation: str) -> str:
    """
    Construye un prompt para resumir una conversación.

    Args:
        conversation: El texto de la conversación que se debe resumir.

    Returns:
        El prompt de resumen formateado.
    """
    return SUMMARY_PROMPT.format(conversation=conversation)


def build_memory_extraction_prompt(conversation: str) -> str:
    """
    Construye un prompt para extraer recuerdos de una conversación.
    Args:
        conversation: El texto de la conversación que se debe analizar.

    Returns:
        El prompt de extracción de recuerdos formateado.
    """
    return MEMORY_EXTRACTION_PROMPT.format(conversation=conversation)


def build_reply_decision_prompt(context: str) -> str:
    """
    Construye un prompt para decidir si se debe responder.

    Args:
        context: El contexto de la conversación que se debe evaluar.

    Returns:
        El prompt de decisión de respuesta formateado.
    """
    return REPLY_DECISION_PROMPT.format(context=context)
