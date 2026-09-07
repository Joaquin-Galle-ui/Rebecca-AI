# Boca Live: protección de cuota

El reloj de n8n puede seguir comprobando el estado cada minuto: esa consulta es
local y no usa una API paga. La consulta externa ahora pasa exclusivamente por
Rebecca Core; el workflow ya no contiene una clave de API-Football.

- Se inicia únicamente con `seguir_partido` y se detiene con `detener_partido`.
- Cada sesión vence a las 3 horas, aunque se repita la orden de inicio.
- Como máximo, una consulta externa cada 120 segundos durante el seguimiento.
- Si no encuentra partido en vivo, espera 5 minutos; al tercer intento se apaga.
- Al identificar un encuentro, sigue su ID para poder detectar el final. FT,
  AET y PEN, o cancelación/aplazamiento, apagan el seguimiento.
- Un error de cuota (incluso dentro de HTTP 200) o credenciales lo bloquea por el
  resto del día UTC. Tres fallos consecutivos también lo bloquean; entre fallos
  hay una pausa de 5 minutos. No hay reintentos automáticos de n8n.
- Máximo local de 60 intentos diarios, contando también búsqueda de equipos,
  agenda y fallos. Este número es una protección de Rebecca, no la cuota del plan
  del proveedor. No contabiliza consumo de otras apps que compartan la clave ni
  consumo anterior a su instalación.
- El contador se conserva en `user_state/football_budget.json`. No borrarlo para
  reintentar: se perdería la protección acumulada. Si se corrompe, no se consulta.
- Al cambiar el día se permite una nueva activación manual; no se reactiva solo.
- La agenda diaria es informativa y se guarda en caché; nunca enciende el relato.
- Preguntar el marcador reutiliza el último dato y aclara su antigüedad.
- Varias novedades recibidas juntas generan un solo resumen, no una llamada a
  Gemini por cada evento. Esto reduce consumo, pero no pone un límite a toda la
  cuota Gemini compartida con otras funciones de Rebecca.

## Publicación

Publicado en n8n el 2026-09-03, versión
`e63048f0-fec7-4653-85b5-93b75c3504d4` (Boca Juniors Live,
`U5tYfOIWB62pSb2a`). Se verificaron una ejecución automática y una manual
(39907 y 39908): ambas terminaron en el control local `If`, sin ejecutar la
consulta externa, Gemini ni Telegram, con el seguimiento apagado.
Los nodos HTTP publicados envían `accion` como campo JSON; el export local usa
un cuerpo JSON completo equivalente.

`tools/update_boca_safety.py` actualiza solamente el export local de Boca Live.
Importar/actualizar ese workflow en n8n y publicar sus cambios es un paso aparte.
Los dos nodos HTTP deben apuntar a `http://127.0.0.1:8000/ejecutar` con acciones
`consultar_estado_partido_protegido` y `consultar_partido_protegido` respectivamente.
Mantener la estructura de nodos, conexiones y credenciales de Telegram/Gemini.

Core 2026.09.02.5 deja cerrada la puerta del workflow antiguo
(`consultar_estado_partido` devuelve `activo: false`), de modo que importar una
copia vieja no vuelva a abrir el gasto. Actualizar el workflow para usarlo.
Las sesiones antiguas sin vencimiento también se desactivan automáticamente.

Las pruebas utilizan respuestas simuladas; no gastan cuota ni envían Telegram.
