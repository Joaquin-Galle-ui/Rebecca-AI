# Rebecca en Minecraft

Esta primera versión entra como un personaje real, sigue al dueño y conversa usando la memoria personal de Rebecca. No rompe bloques ni combate por cuenta propia.

## Preparación

1. Instalá Node.js si todavía no está disponible.
2. En esta carpeta ejecutá `npm install` una sola vez.
3. Copiá `config.example.json` como `config.json` y completá servidor, cuenta y tu nombre exacto.
4. Ejecutá `iniciar_minecraft.bat`.

Órdenes dentro del chat del juego:

- `!seguime` o `!ven`: te sigue.
- `!quieta`: se detiene y suelta todos los controles.
- `!salta`: salta una vez.
- `!estado`: informa vida y comida.
- `rebecca <mensaje>`: conversa con Rebecca usando su memoria principal.

Solo el usuario indicado en `owner` puede controlarla. Para servidores online conviene usar una cuenta Microsoft separada para Rebecca; no pongas contraseñas en `config.json`.

Nota de dependencias: la versión actual de Mineflayer trae transitivamente un aviso moderado de `uuid` en sus módulos de autenticación. No se aplicó `npm audit fix --force` porque intentaba reemplazar Mineflayer por una versión antigua e incompatible. Mantené Mineflayer actualizado y usá el modo offline sólo en servidores locales de confianza.
