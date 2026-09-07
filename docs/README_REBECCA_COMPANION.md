# Rebecca Companion: estado actual

Rebecca personal y Rebecca de SubliZen están separadas. La aplicación de escritorio, Telegram, Becca Curiosa, Boca Juniors Live, Isaac y Minecraft usan la memoria personal. WhatsApp Business conserva una memoria independiente por cliente.

## Usar el avatar y la voz

1. Dejá n8n encendido.
2. Hacé doble clic en `iniciar_rebecca_companion.bat`.
3. Escribile desde la caja inferior o activá `Espectadora`.

El avatar queda como un retrato neutral estático sobre un fondo azul grisáceo, sin cambios de expresión ni animación de boca. Cuando hay dos pantallas abre centrado en la segunda, aunque todavía puede arrastrarse. `Ctrl+Alt+O` pide un comentario inmediato y `Ctrl+Alt+Espacio` escucha una frase. Los modos silenciosa, normal y charlatana esperan 180, 90 y 45 segundos como mínimo entre análisis, respectivamente. Además exigen un cambio visible de escena.

La escucha activa exige comenzar con “Rebecca”, “Rebeca” o “Becca”. La escucha manual no exige esa palabra. Ambas vías envían el texto reconocido a la misma conversación, por lo que sirven tanto para responder comentarios anteriores como para pedir acciones de la PC.

La voz usa Fish Audio y cae automáticamente a la voz local de Windows cuando Fish no está disponible. El botón de silencio detiene la reproducción WAV actual.

## Isaac

El mod actualizado está en `BotCoop` y el puente en `Isaac_IA_Python`. Consultá su README para copiar el mod y arrancarlo. Rebecca recibe datos reales del juego, no sólo capturas. El piloto automático se mantiene desactivado hasta probar controles, navegación y botón de emergencia dentro de una partida.

## Minecraft

El personaje está en `Minecraft_Rebecca_Bot`. Las dependencias ya están instaladas. Copiá `config.example.json` como `config.json`, indicá servidor, cuenta y tu usuario, y arrancá `iniciar_minecraft.bat`. Sólo el dueño configurado puede darle órdenes; romper bloques y combatir quedan desactivados.

## Consumo

Hablar manualmente usa una llamada de texto y, si Fish funciona, una llamada de voz. `Opinar ahora` agrega una llamada de visión. El modo espectadora reduce ese consumo mediante detección local de cambios y tiempos mínimos. Isaac comenta sólo eventos importantes y aplica un tiempo mínimo de 25 segundos.

## Seguridad

El control de la PC sigue escuchando únicamente en `127.0.0.1`. No se guardaron claves nuevas en los prototipos. Minecraft no almacena contraseñas y el mando de Isaac siempre arranca neutral.
