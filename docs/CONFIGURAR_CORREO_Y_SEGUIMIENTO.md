# Correo y seguimiento de SubliZen

## Correo recomendado

Creá una cuenta de Gmail exclusiva para el negocio. No uses el correo personal ni compartas la contraseña normal con Rebecca.

1. Activá la verificación en dos pasos de la cuenta.
2. Creá una contraseña de aplicación para Rebecca.
3. Abrí `.env` en esta misma carpeta y completá `EMAIL_USUARIO` y `EMAIL_PASSWORD`.
4. Opcionalmente cambiá `EMAIL_FROM_NAME=SubliZen`.

La contraseña de aplicación se guarda solamente en `.env`, archivo excluido del control de versiones. No la pegues en chats, workflows ni capturas.

## Enlace permanente del QR

`PUBLIC_BASE_URL` debe apuntar a una dirección HTTPS que no cambie. Un dominio temporal de túnel puede invalidar todos los QR impresos cuando cambia la dirección.

Opciones recomendadas:

- Dominio o subdominio propio conectado a un túnel permanente.
- Dominio estático reservado en ngrok.
- Cloudflare Tunnel con un subdominio propio.

El QR contiene un token privado y la página consulta el pedido cada 15 segundos. Cambiar el estado o las tareas desde el dashboard actualiza la página sin volver a emitir el PDF.

## Qué documento se envía

Rebecca envía automáticamente un comprobante de seña por correo y WhatsApp, pero solamente después de este circuito:

1. El cliente confirma el pedido y recibe el importe exacto de la seña (50%).
2. Envía una captura JPG/PNG/WEBP o un PDF de hasta 12 MB.
3. Rebecca guarda el archivo y deja el pago como **pendiente de verificación**.
4. El dueño abre el archivo desde el dashboard, comprueba que el dinero ingresó en la cuenta y pulsa **Aprobar** o **Rechazar**.
5. Al aprobar, se registra la seña y se envía el recibo por correo y WhatsApp. Si un canal falla, se reintenta sin duplicar el que ya salió bien.

Una captura puede falsificarse: recibirla nunca acredita el pago por sí solo. El PDF emitido por Rebecca es un comprobante de seña, no una factura fiscal electrónica. La emisión fiscal debe integrarse por separado con ARCA y requiere la configuración tributaria de SubliZen.

## Privacidad del dashboard

Desde esta PC, `http://127.0.0.1:8000/dashboard` abre normalmente. El dashboard queda bloqueado cuando se intenta entrar por el dominio público. Si necesitás acceso remoto, definí una clave larga en `DASHBOARD_ACCESS_TOKEN` y abrí una vez `https://tu-dominio/dashboard?token=tu-clave`.

Las capturas y PDF de transferencias se guardan en carpetas privadas excluidas del control de versiones. No se muestran en la página pública de seguimiento del cliente.
