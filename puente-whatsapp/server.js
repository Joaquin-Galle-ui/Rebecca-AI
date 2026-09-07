const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const express = require('express');
const axios = require('axios');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const app = express();
app.use(express.json({ limit: '1mb' }));
let whatsappReady = false;
let inicializandoWhatsApp = false;
let temporizadorReconexion = null;
const MAX_COMPROBANTE_BYTES = 12 * 1024 * 1024;
const COMPROBANTES_DIR = path.join(__dirname, 'comprobantes_pago_entrantes');
const TIPOS_COMPROBANTE = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
    'application/pdf': '.pdf'
};
const MENSAJES_PENDIENTES = new Map();
const VENTANA_AGRUPACION_MS = Number(process.env.SUBLIZEN_AGRUPAR_MS || 3500);
const ESPERA_MAXIMA_MS = Number(process.env.SUBLIZEN_ESPERA_MAXIMA_MS || 8000);

fs.mkdirSync(COMPROBANTES_DIR, { recursive: true });

function extensionComprobante(mime) {
    return TIPOS_COMPROBANTE[String(mime || '').toLowerCase()] || '';
}

function firmaValida(buffer, mime) {
    if (!Buffer.isBuffer(buffer) || buffer.length < 8) return false;
    if (mime === 'application/pdf') return buffer.subarray(0, 5).toString('ascii') === '%PDF-';
    if (mime === 'image/jpeg') return buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff;
    if (mime === 'image/png') return buffer.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
    if (mime === 'image/webp') return buffer.subarray(0, 4).toString('ascii') === 'RIFF' && buffer.subarray(8, 12).toString('ascii') === 'WEBP';
    return false;
}

async function guardarAdjuntoComprobante(msg) {
    if (!msg.hasMedia) return {};
    const media = await msg.downloadMedia();
    if (!media || !media.data) throw new Error('WhatsApp no entregó el contenido del adjunto.');
    const mime = String(media.mimetype || '').toLowerCase();
    const extension = extensionComprobante(mime);
    if (!extension) throw new Error('Formato no admitido. Enviá una imagen JPG, PNG, WEBP o un PDF.');
    const buffer = Buffer.from(media.data, 'base64');
    if (buffer.length > MAX_COMPROBANTE_BYTES) throw new Error('El comprobante supera el límite de 12 MB.');
    if (!firmaValida(buffer, mime)) throw new Error('El contenido del archivo no coincide con su formato declarado.');

    const sha256 = crypto.createHash('sha256').update(buffer).digest('hex');
    const nombreSeguro = `${Date.now()}_${sha256.slice(0, 16)}${extension}`;
    const ruta = path.join(COMPROBANTES_DIR, nombreSeguro);
    if (!fs.existsSync(ruta)) fs.writeFileSync(ruta, buffer, { flag: 'wx' });
    return {
        adjunto_path: ruta,
        adjunto_mime: mime,
        adjunto_nombre: String(media.filename || nombreSeguro).slice(0, 180),
        adjunto_sha256: sha256,
        adjunto_tamano: buffer.length
    };
}

function normalizarNumero(numero) {
    const valor = String(numero || '').trim();
    if (valor.endsWith('@c.us')) return valor;
    const digitos = valor.replace(/\D/g, '');
    return digitos ? `${digitos}@c.us` : '';
}

async function procesarLote(origen) {
    const lote = MENSAJES_PENDIENTES.get(origen);
    if (!lote) return;
    MENSAJES_PENDIENTES.delete(origen);
    if (lote.timer) clearTimeout(lote.timer);
    const mensaje = lote.mensajes.filter(Boolean).join('\n').trim()
        || `[El cliente adjuntó un archivo de tipo ${lote.tipo_adjunto || 'desconocido'}]`;
    try {
        const control = await axios.post('http://127.0.0.1:8000/sublizen/estado-atencion', {
            numero: origen,
            nombre: lote.nombre
        }, { timeout: 5000 });
        if (control.data && control.data.responder_automaticamente === false) {
            console.log(`Atención humana activa para ${origen}; Rebecca no respondió.`);
            return;
        }
        await axios.post('http://127.0.0.1:5678/webhook/whatsapp-in', {
            numero: origen,
            nombre: lote.nombre,
            mensaje,
            mensajes_agrupados: lote.mensajes.length,
            tiene_adjunto: Boolean(lote.tiene_adjunto),
            tipo_adjunto: lote.tipo_adjunto || '',
            adjunto_error: lote.adjunto_error || '',
            ...(lote.adjunto || {})
        }, { timeout: 15000 });
    } catch (error) {
        console.error('❌ Error enviando el lote a Rebecca:', error.message);
    }
}

function agregarAlLote(origen, entrada) {
    const ahora = Date.now();
    const lote = MENSAJES_PENDIENTES.get(origen) || {
        primero_en: ahora, mensajes: [], nombre: entrada.nombre || 'Cliente', adjunto: {},
        tiene_adjunto: false, tipo_adjunto: '', adjunto_error: '', timer: null
    };
    if (entrada.mensaje) lote.mensajes.push(entrada.mensaje);
    lote.nombre = entrada.nombre || lote.nombre;
    if (entrada.tiene_adjunto) {
        lote.tiene_adjunto = true;
        lote.tipo_adjunto = entrada.tipo_adjunto || lote.tipo_adjunto;
        lote.adjunto = entrada.adjunto || lote.adjunto;
    }
    if (entrada.adjunto_error) {
        lote.adjunto_error = [lote.adjunto_error, entrada.adjunto_error].filter(Boolean).join(' | ');
    }
    if (lote.timer) clearTimeout(lote.timer);
    const restante = Math.max(100, ESPERA_MAXIMA_MS - (ahora - lote.primero_en));
    lote.timer = setTimeout(() => procesarLote(origen), Math.min(VENTANA_AGRUPACION_MS, restante));
    MENSAJES_PENDIENTES.set(origen, lote);
}

const client = new Client({
    authStrategy: new LocalAuth({ clientId: "sublizen-bot" }),
    authTimeoutMs: 120000,
    puppeteer: {
        headless: true,
        timeout: 120000,
        protocolTimeout: 120000,
        args: ['--disable-gpu']
    }
});

function programarReconexion(delayMs = 10000) {
    if (whatsappReady || temporizadorReconexion) return;
    temporizadorReconexion = setTimeout(async () => {
        temporizadorReconexion = null;
        try { await client.destroy(); } catch (_) {}
        iniciarWhatsApp();
    }, delayMs);
}

async function iniciarWhatsApp() {
    if (whatsappReady || inicializandoWhatsApp) return;
    inicializandoWhatsApp = true;
    try {
        await client.initialize();
    } catch (error) {
        whatsappReady = false;
        console.error('❌ No se pudo iniciar WhatsApp Web:', error.message || error);
        console.error('Se reintentará automáticamente en 15 segundos.');
        programarReconexion(15000);
    } finally {
        inicializandoWhatsApp = false;
    }
}

client.on('qr', (qr) => {
    console.log('Escaneá este QR con tu WhatsApp Business:');
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    whatsappReady = true;
    if (temporizadorReconexion) clearTimeout(temporizadorReconexion);
    temporizadorReconexion = null;
    console.log('✅ ¡Puente conectado! Rebecca ya tiene acceso a WhatsApp.');
});

client.on('disconnected', () => {
    whatsappReady = false;
    console.log('⚠️ WhatsApp se desconectó. Se intentará reconectar.');
    programarReconexion();
});

client.on('auth_failure', (mensaje) => {
    whatsappReady = false;
    console.error('❌ Falló la autenticación de WhatsApp:', mensaje);
    programarReconexion();
});

client.on('message', async msg => {
    const origen = String(msg.from || '');
    const esChatPrivado = origen.endsWith('@c.us');
    if ((msg.body || msg.hasMedia) && esChatPrivado) {
        try {
            console.log(`Mensaje de ${msg.from}: ${msg.body}`);
            let nombre = 'Cliente';
            try {
                const contacto = await msg.getContact();
                nombre = contacto.pushname || contacto.name || nombre;
            } catch (_) {}
            let adjunto = {};
            let adjuntoError = '';
            if (msg.hasMedia) {
                try {
                    adjunto = await guardarAdjuntoComprobante(msg);
                } catch (errorAdjunto) {
                    adjuntoError = errorAdjunto.message;
                    console.error('❌ Adjunto rechazado:', adjuntoError);
                }
            }
            agregarAlLote(origen, {
                nombre,
                mensaje: String(msg.body || '').trim(),
                tiene_adjunto: Boolean(msg.hasMedia),
                tipo_adjunto: msg.hasMedia ? String(msg.type || 'archivo') : '',
                adjunto_error: adjuntoError,
                adjunto
            });
        } catch (error) {
            console.error('❌ Error preparando el mensaje:', error.message);
        }
    }
});

iniciarWhatsApp();

app.get('/health', (_req, res) => {
    res.status(whatsappReady ? 200 : 503).json({
        status: whatsappReady ? 'ok' : 'waiting',
        whatsapp: whatsappReady ? 'connected' : 'disconnected'
    });
});

app.post('/reconectar', (_req, res) => {
    if (whatsappReady) return res.json({ status: 'ok', message: 'WhatsApp ya está conectado' });
    if (temporizadorReconexion) clearTimeout(temporizadorReconexion);
    temporizadorReconexion = null;
    programarReconexion(50);
    res.status(202).json({ status: 'waiting', message: 'Reconexión solicitada' });
});

// Enviar texto normal
app.post('/enviar', async (req, res) => {
    const { numero, texto } = req.body;
    const chatId = normalizarNumero(numero);
    if (!whatsappReady) return res.status(503).json({ status: 'error', message: 'WhatsApp todavía no está conectado' });
    if (!chatId || !String(texto || '').trim()) return res.status(400).json({ status: 'error', message: 'Faltan número o texto' });
    try {
        await client.sendMessage(chatId, String(texto).trim());
        res.json({ status: 'success', message: 'Mensaje despachado' });
    } catch (error) {
        res.status(500).json({ status: 'error', message: error.message });
    }
});

// NUEVO: Enviar Archivos PDF
app.post('/enviar_pdf', async (req, res) => {
    const { numero, ruta_pdf, caption } = req.body;
    const chatId = normalizarNumero(numero);
    if (!whatsappReady) return res.status(503).json({ status: 'error', message: 'WhatsApp todavía no está conectado' });
    if (!chatId || !ruta_pdf) return res.status(400).json({ status: 'error', message: 'Faltan número o PDF' });
    if (!fs.existsSync(ruta_pdf)) return res.status(404).json({ status: 'error', message: 'No se encontró el PDF' });
    try {
        const media = MessageMedia.fromFilePath(ruta_pdf);
        await client.sendMessage(chatId, media, { caption: caption || 'Acá tenés tu comprobante' });
        res.json({ status: 'success', message: 'PDF enviado por WhatsApp' });
    } catch (error) {
        res.status(500).json({ status: 'error', message: error.message });
    }
});

app.listen(3000, '127.0.0.1', () => {
    console.log('🚀 Servidor puente escuchando en el puerto 3000');
});

// Enviar una muestra de diseño validada por Rebecca Core.
app.post('/enviar_archivo', async (req, res) => {
    const { numero, ruta_archivo, caption } = req.body;
    const chatId = normalizarNumero(numero);
    if (!whatsappReady) return res.status(503).json({ status: 'error', message: 'WhatsApp todavía no está conectado' });
    if (!chatId || !ruta_archivo) return res.status(400).json({ status: 'error', message: 'Faltan número o archivo' });
    if (!fs.existsSync(ruta_archivo)) return res.status(404).json({ status: 'error', message: 'No se encontró el archivo' });
    const extension = path.extname(ruta_archivo).toLowerCase();
    if (!['.jpg', '.jpeg', '.png', '.webp', '.pdf'].includes(extension)) {
        return res.status(400).json({ status: 'error', message: 'Formato de diseño no permitido' });
    }
    if (fs.statSync(ruta_archivo).size > MAX_COMPROBANTE_BYTES) {
        return res.status(413).json({ status: 'error', message: 'El archivo supera 12 MB' });
    }
    try {
        const media = MessageMedia.fromFilePath(ruta_archivo);
        await client.sendMessage(chatId, media, { caption: caption || 'Muestra de diseño para tu aprobación' });
        res.json({ status: 'success', message: 'Diseño enviado por WhatsApp' });
    } catch (error) {
        res.status(500).json({ status: 'error', message: error.message });
    }
});
