# Rebecca Companion

Aplicación local de Rebecca personal. Muestra el avatar, conversa usando la memoria de n8n,
reproduce la voz por la PC y activa un modo espectadora con consumo adaptativo.

## Arranque

1. Mantener n8n encendido y el workflow `Rebecca Companion` publicado.
2. Hacer doble clic en `iniciar_rebecca_companion.bat` desde la raíz.

Para iniciar de una sola vez n8n, ngrok, WhatsApp, Rebecca Core y Companion, hacer doble clic en
`Iniciar Rebecca.exe` desde la carpeta principal. El ejecutable reutiliza el inicio seguro, por lo
que no abre copias de los servicios que ya estén funcionando.

El iniciador enciende Rebecca Core sólo si todavía no está funcionando. También se puede abrir manualmente desde la raíz:

```powershell
python -m rebecca_companion.launcher
```

Atajos:

- `Ctrl+Alt+O`: solicita una opinión inmediata de la ventana activa.
- `Ctrl+Alt+Espacio`: escucha una orden o respuesta sin exigir palabra de llamada.

`Escucha activa` mantiene el micrófono atento. En este modo sólo procesa frases que comienzan
con `Rebecca`, `Rebeca` o `Becca`; por ejemplo: “Rebecca, abrí Steam”. Así reduce órdenes
accidentales producidas por el juego, un video o la propia voz de Rebecca. El reconocimiento
descarta el audio ambiente sin mostrar que Rebecca está “entendiendo” hasta haber validado la
palabra de llamada. El micrófono queda pausado mientras observa, piensa o habla.

El modo espectadora abre una sesión persistente de Gemini Live y le envía la ventana activa como
un video liviano de hasta un cuadro por segundo. Así conserva continuidad entre escenas y responde
mucho más rápido que el análisis de capturas sueltas. `Opinar ahora` usa esa misma sesión cuando
está conectada. Si Live no está disponible durante 20 segundos, Companion vuelve automáticamente
al analizador clásico. El OCR local de Dead by Daylight sigue funcionando como respuesta inmediata
y como respaldo. La voz clonada usa Fish Audio; si falla, Rebecca usa la voz SAPI local de Windows.

Los intervalos de comentario de Live son 60 segundos en `silenciosa`, 30 en `normal` y 15 en
`charlatana`. Rebecca puede decidir guardar silencio cuando no observa un hecho concreto y nuevo.
Su estilo busca acompañar: reacciones, lecturas breves de la jugada y sugerencias ocasionales,
sin limitarse a narrar la pantalla ni inventar detalles. La selección de rol `automatico` vuelve
a dejar la identificación en manos de la visión y reemplaza el rol manual anterior.

La voz se procesa por separado: mientras sintetiza o reproduce una frase, Live sigue enviando
y recibiendo imágenes. No se acumulan comentarios para reproducirlos tarde. Cada respuesta conserva
la ventana, el rol y la hora de la captura que motivó la consulta; se descarta si ese contexto cambió
o si supera 18 segundos (25 para una opinión manual). El Core comprueba nuevamente el plazo después
de sintetizar y antes de iniciar la reproducción, también para el respaldo de voz local. No se
descarta una escena sólo porque sus píxeles se muevan.

`user_state/comments.jsonl` registra exclusivamente los comentarios, su origen (`live`, `ocr` o
`fallback`), horarios UTC, tiempos y motivos de descarte. `voice_completed` indica reproducción
completada según el Core; `submitted_to_voice` por sí solo no garantiza que se haya oído. No guarda
capturas, audio, claves ni conversaciones del micrófono. Rota en tres archivos de aproximadamente
500 KB cada uno; se puede desactivar con `REBECCA_COMMENT_LOG=0` en el entorno.
Para desactivar temporalmente esta función se puede definir `REBECCA_LIVE_VISION=0`; el modelo se
puede cambiar mediante `REBECCA_LIVE_MODEL`.

El retrato se compone desde las capas de `Rebecca_Sprites`: base, seis expresiones y tres
posiciones de boca. La expresión se elige a partir de la respuesta, la boca se anima mientras
Rebecca habla y el avatar parpadea ocasionalmente cuando está disponible. El selector `Pantalla`
mueve a Rebecca al monitor elegido. `Solo avatar` oculta el fondo, el marco y todos los controles,
pero mantiene activos la voz, la escucha y el modo espectadora. En ese modo se puede arrastrar el
avatar libremente y su posición queda guardada por cada pantalla. Doble clic sobre Rebecca vuelve
a mostrar el panel; el clic derecho permite recuperarla, ocultarla o enviarla a otro monitor. La
opción `Ocultar` mantiene encendida la escucha activa: al decir “Rebecca” reaparece exactamente
donde se la dejó. Esto también funciona si se minimiza la ventana desde el marco normal. Rebecca
permanece disponible en la bandeja del sistema con un menú para mostrar el panel, mostrar sólo el
avatar, ocultarla o cerrarla por completo.

El panel respeta el área útil del monitor y no se extiende debajo de la barra de tareas. Cuando la
altura disponible no alcanza, el avatar queda visible arriba y las opciones inferiores se recorren
con la rueda del mouse o con la barra de desplazamiento lateral.

Si existe `Rebecca_Sprites/base_limpia.png`, Companion la utiliza antes que `base.png`. El archivo
original permanece como respaldo para poder volver atrás sin perder el sprite fuente.

El chat del Companion también es el control personal habitual de la PC: puede abrir programas,
poner o controlar música, mirar la pantalla, revisar el estado del equipo y activar el
seguimiento de un partido. Las acciones destructivas siguen requiriendo una orden explícita.

Rebecca conserva una memoria local pequeña para datos expresados de forma explícita, como
“recordá que…”, gustos, preferencias y fechas personales. No archiva indiscriminadamente cada
charla emocional. Los comentarios visuales recientes también se conservan como contexto corto,
por lo que se les puede responder por texto o voz sin repetir qué estaba pasando. El micrófono,
la salida de audio y el modo de espectadora elegidos se restauran al volver a abrir Companion.

## Conversar sobre la partida

Con Espectadora encendida y visión Live conectada, podés decir «Rebecca, ¿viste
eso?», «¿por qué perdí esa persecución?» o «¿qué harías ahora?». La pregunta usa la
misma sesión que recibe las imágenes; su respuesta vuelve por el turno normal de
chat y voz, sin generar además un comentario automático duplicado. Los comandos
directos de PC conservan prioridad de enrutamiento.

Companion mantiene en RAM hasta 12 recuerdos de los últimos cinco minutos:
comentarios (etiquetados como interpretaciones), preguntas y respuestas del juego.
Se descartan al cambiar de ventana observada, apagar Espectadora o cerrar Companion.
No se guardan imágenes ni grabaciones en esa memoria. La conversación textual
habitual de n8n y el registro de comentarios existente conservan sus políticas propias.
«Estoy jugando de asesino/superviviente» corrige el rol si la ventana es Dead by
Daylight; no impone esos roles a otros juegos.

Durante la captura de voz y la conversación no se lanzan comentarios automáticos.
Una respuesta visual automática anterior queda invalidada al iniciar la conversación;
al terminar, se dejan ocho segundos antes de reanudar los comentarios. Si escribís
mientras Rebecca termina de hablar, un mensaje queda esperando con prioridad.
Para iniciar una charla, la escucha activa sigue exigiendo la palabra Rebecca;
tampoco escucha durante su propia voz, para evitar ecos y falsas interrupciones.

Después de una frase válida que comienza con «Rebecca», queda abierta una charla
por voz durante 45 segundos desde el final de cada respuesta. En ese intervalo,
las continuaciones conversacionales no necesitan repetir su nombre. «Listo,
gracias», «dejémoslo acá», «terminemos la charla» o «podés dejar de escuchar»
cierran el turno; el silencio lo cierra automáticamente. Para evitar que voces del
juego dejen el micrófono abierto indefinidamente, la sesión admite como máximo ocho
transcripciones y luego vuelve a exigir «Rebecca».

Los turnos sin palabra de activación sirven para conversar, pero no ejecutan
herramientas ni comandos del equipo, aunque el modelo intente prepararlos. Para
abrir un programa, cambiar música o modificar datos durante la charla, hay que
decir «Rebecca» junto con la orden. Esa frase vuelve a ser un turno autorizado sin
cerrar la conversación.

Las preguntas visuales tienen una espera máxima de 20 segundos y no generan
reintentos ni una segunda llamada de respaldo. Si falta una imagen reciente o se
corta la conexión, Rebecca lo indica en lugar de afirmar que vio la jugada. Al
escribir en el panel puede utilizar los últimos segundos previos a enfocarlo,
pero no envía imágenes del propio chat. No equivale a una repetición grabada:
para comentar un momento que no alcanzó a ver, hay que contárselo.
