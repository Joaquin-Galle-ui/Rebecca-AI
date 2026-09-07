# Seguridad

- Rebecca Core escucha solamente en `127.0.0.1:8000`.
- n8n y el puente de WhatsApp también deben permanecer locales durante la configuración.
- No expongas el puerto 8000 directamente a Internet: permite controlar aplicaciones y el equipo.
- Si publicás un dashboard, definí `DASHBOARD_ACCESS_TOKEN` y usá HTTPS con autenticación delante del servicio.
- Los workflows se distribuyen desactivados, sin IDs de instancia ni referencias a credenciales reales.
- `.env`, bases de datos, sesiones de WhatsApp, capturas, audios, comprobantes y logs están ignorados por Git.
- Apagar, reiniciar, imprimir, borrar, enviar archivos y modificar datos requieren una orden explícita. Revisá esas funciones antes de permitir acceso a terceros.

Si una clave fue publicada alguna vez, eliminarla del repositorio no alcanza: revocala en el proveedor y generá una nueva.
