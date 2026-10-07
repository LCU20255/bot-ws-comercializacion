const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeInMemoryStore
} = require("@whiskeysockets/baileys");
const pino = require("pino");
const qrcodeTerminal = require("qrcode-terminal");
const QRCode = require("qrcode");
const axios = require("axios");
const path = require("path");
const fs = require("fs");

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000/api/chat/simulate";
const QR_IMAGE_PATH = path.resolve(__dirname, "../app/static/images/baileys_qr.png");
const AUTH_DIR = path.resolve(__dirname, "auth_info_baileys");

async function notifyPython(payload, retries = 10) {
  const pyBase = PYTHON_API_URL.replace("/api/chat/simulate", "");
  for (let i = 0; i < retries; i++) {
    try {
      await axios.post(`${pyBase}/api/baileys/internal-status`, payload, { timeout: 3000 });
      return;
    } catch (e) {
      if (i < retries - 1) {
        await new Promise(r => setTimeout(r, 1500));
      }
    }
  }
}

let globalSock = null;

async function connectToWhatsApp() {
  console.log("==================================================");
  console.log("🚀 Iniciando cliente de WhatsApp con Baileys...");
  console.log("==================================================");

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version, isLatest } = await fetchLatestBaileysVersion();
  console.log(`Usando versión de WhatsApp Web v${version.join(".")} (Última: ${isLatest})`);

  const sock = makeWASocket({
    version,
    logger: pino({ level: "silent" }),
    printQRInTerminal: false,
    auth: state,
    browser: ["Comercializacion Bot", "Chrome", "1.0.0"]
  });
  globalSock = sock;

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log("\n📲 NUEVO CÓDIGO QR GENERADO POR BAILEYS\n");
      console.log(`[BAILEYS_QR_DATA]${qr}[/BAILEYS_QR_DATA]`);
      qrcodeTerminal.generate(qr, { small: true });

      // Guardar también como imagen para el panel web
      try {
        await QRCode.toFile(QR_IMAGE_PATH, qr, {
          width: 300,
          margin: 2,
          color: { dark: "#0f172a", light: "#ffffff" }
        });
        console.log(`[QR] Imagen guardada en: ${QR_IMAGE_PATH}`);
      } catch (err) {
        console.error("Error generando imagen QR:", err.message);
      }

      // Notificar a Python con reintentos
      notifyPython({
        status: "QR",
        qr: qr,
        timestamp: Date.now()
      });
    }

    if (connection === "close") {
      const shouldReconnect =
        lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
      console.log(
        "❌ Conexión cerrada debida a:",
        lastDisconnect?.error,
        ", reconectando:",
        shouldReconnect
      );
      console.log("[BAILEYS_STATUS]DISCONNECTED[/BAILEYS_STATUS]");

      notifyPython({
        status: "DISCONNECTED"
      });

      // Si fue logout, limpiar sesión
      if (!shouldReconnect) {
        console.log("Sesión cerrada. Limpiando credenciales para nuevo QR...");
        try {
          fs.rmSync(AUTH_DIR, { recursive: true, force: true });
        } catch (e) {}
      }

      if (global.heartbeatTimer) {
        clearInterval(global.heartbeatTimer);
        global.heartbeatTimer = null;
      }

      setTimeout(connectToWhatsApp, 3000);
    } else if (connection === "open") {
      console.log("\n==================================================");
      console.log("✅ ¡WHATSAPP CONECTADO EXITOSAMENTE VÍA BAILEYS!");
      console.log("El bot de comercialización ya está atendiendo clientes.");
      console.log("==================================================\n");

      // Limpiar archivo QR al estar conectado
      try {
        if (fs.existsSync(QR_IMAGE_PATH)) {
          fs.unlinkSync(QR_IMAGE_PATH);
        }
      } catch (e) {}

      const userPhone = sock.user?.id ? sock.user.id.split(":")[0] : "";
      console.log(`[BAILEYS_CONNECTED]${userPhone}[/BAILEYS_CONNECTED]`);

      notifyPython({
        status: "CONNECTED",
        phone: userPhone
      });

      // Heartbeat periódico a Python cada 6 segundos para mantener sincronizado el panel
      if (global.heartbeatTimer) clearInterval(global.heartbeatTimer);
      global.heartbeatTimer = setInterval(() => {
        notifyPython({
          status: "CONNECTED",
          phone: userPhone
        }, 1);
      }, 6000);
    }
  });

  // Escuchar mensajes entrantes
  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const msg of messages) {
      // Ignorar mensajes propios, de grupos o de estados
      if (
        msg.key.fromMe ||
        msg.key.remoteJid.includes("@g.us") ||
        msg.key.remoteJid.includes("status@broadcast")
      ) {
        continue;
      }

      const remoteJid = msg.key.remoteJid;
      let phone = remoteJid.replace("@s.whatsapp.net", "");

      // Si remoteJid es un @lid, intentar resolver el número real si WhatsApp lo incluye en metadatos
      if (remoteJid.includes("@lid")) {
        if (msg.key.remoteJidAlt && msg.key.remoteJidAlt.includes("@s.whatsapp.net")) {
          phone = msg.key.remoteJidAlt.split("@")[0];
        } else if (msg.key.participant && msg.key.participant.includes("@s.whatsapp.net")) {
          phone = msg.key.participant.split("@")[0];
        } else if (msg.participant && msg.participant.includes("@s.whatsapp.net")) {
          phone = msg.participant.split("@")[0];
        }
      }

      // Desenvolver mensajes anidados (ephemeral, viewOnce, viewOnceV2, etc.)
      let messageContent = msg.message;
      while (
        messageContent?.ephemeralMessage ||
        messageContent?.viewOnceMessage ||
        messageContent?.viewOnceMessageV2 ||
        messageContent?.documentWithCaptionMessage
      ) {
        messageContent =
          messageContent?.ephemeralMessage?.message ||
          messageContent?.viewOnceMessage?.message ||
          messageContent?.viewOnceMessageV2?.message ||
          messageContent?.documentWithCaptionMessage?.message;
      }

      // Extraer texto del mensaje o imagen
      let text =
        messageContent?.conversation ||
        messageContent?.extendedTextMessage?.text ||
        messageContent?.imageMessage?.caption ||
        messageContent?.documentMessage?.caption ||
        messageContent?.buttonsResponseMessage?.selectedButtonId ||
        messageContent?.listResponseMessage?.singleSelectReply?.selectedRowId ||
        "";

      let imageBase64 = null;
      const hasImage =
        !!messageContent?.imageMessage ||
        (messageContent?.documentMessage && messageContent?.documentMessage?.mimetype?.startsWith("image/"));

      if (hasImage) {
        try {
          const { downloadMediaMessage } = require("@whiskeysockets/baileys");
          const msgToDownload = {
            key: msg.key,
            message: messageContent
          };
          const buffer = await downloadMediaMessage(
            msgToDownload,
            "buffer",
            {},
            {
              logger: pino({ level: "silent" }),
              reuploadRequest: sock.updateMediaMessage
            }
          );
          if (buffer) {
            imageBase64 = buffer.toString("base64");
            console.log(`📸 [Comprobante Recibido] Imagen capturada para +${phone} (${buffer.length} bytes)`);
          }
        } catch (mediaErr) {
          console.error("Error descargando comprobante de pago:", mediaErr.message);
        }
      }

      const isAudio = !!messageContent?.audioMessage;
      if (isAudio && !text) {
        text = "[NOTA_DE_VOZ]";
      }

      text = text.trim();
      if (!text && !imageBase64) continue;

      console.log(`📩 [WhatsApp Inbound] De: +${phone} (JID: ${remoteJid}) | Mensaje: "${text}" | Tiene Imagen: ${!!imageBase64} | Audio: ${isAudio}`);

      try {
        // Enviar a nuestro motor de IA / NLU en Python
        const response = await axios.post(PYTHON_API_URL, {
          phone: phone.startsWith("+") ? phone : `+${phone}`,
          jid: remoteJid,
          message: text,
          image_base64: imageBase64
        });

        const { reply, image_url, messages } = response.data;
        console.log(`🤖 [Bot Respuesta] Para: +${phone}`);

        // Enviar respuesta con o sin imagen
        if (image_url) {
          let imagePayload = null;

          if (image_url.startsWith("http://") || image_url.startsWith("https://")) {
            imagePayload = { url: image_url };
          } else {
            // Ruta estática local
            const localPath = path.resolve(__dirname, `../app${image_url}`);
            if (fs.existsSync(localPath)) {
              imagePayload = fs.readFileSync(localPath);
            }
          }

          if (imagePayload) {
            await sock.sendMessage(remoteJid, {
              image: imagePayload,
              caption: reply
            });
            continue;
          }
        }

        // Si la respuesta contiene mensajes múltiples individuales (ej: resumen, pago móvil, transferencia por separado)
        if (Array.isArray(messages) && messages.length > 0) {
          for (const m of messages) {
            if (m && m.trim()) {
              await sock.sendMessage(remoteJid, { text: m.trim() });
              await new Promise(r => setTimeout(r, 600)); // pausa natural de 600ms entre mensajes
            }
          }
        } else if (reply && reply.trim()) {
          // Si no hay array de mensajes, enviar mensaje individual
          await sock.sendMessage(remoteJid, { text: reply.trim() });
        }
      } catch (err) {
        console.error(`Error procesando mensaje para +${phone}:`, err.message);
        await sock.sendMessage(remoteJid, {
          text: "⚠️ Ocurrió un inconveniente temporal. Por favor escribe *Hola* para reiniciar la conversación."
        });
      }
    }
  });
}

connectToWhatsApp();

// Servidor HTTP interno para envío de mensajes salientes (Notificaciones automáticas / Lista de espera)
const http = require("http");
const outboundServer = http.createServer((req, res) => {
  if (req.method === "POST" && req.url === "/send") {
    let body = "";
    req.on("data", chunk => { body += chunk; });
    req.on("end", async () => {
      try {
        const parsed = JSON.parse(body || "{}");
        const phone = parsed.phone || "";
        const text = parsed.text || "";
        let cleanPhone = phone.replace(/[^0-9]/g, "");
        if (!cleanPhone.endsWith("@s.whatsapp.net")) cleanPhone = `${cleanPhone}@s.whatsapp.net`;

        if (globalSock && globalSock.user) {
          await globalSock.sendMessage(cleanPhone, { text });
          console.log(`📤 [Outbound Enviado vía Baileys] Para: ${cleanPhone}`);
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ status: "sent", to: cleanPhone }));
        } else {
          res.writeHead(503, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ status: "not_connected" }));
        }
      } catch (err) {
        console.error("Error en Outbound Baileys:", err.message);
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: err.message }));
      }
    });
  } else {
    res.writeHead(404);
    res.end();
  }
});

outboundServer.listen(3001, () => {
  console.log("📡 Servidor Outbound Baileys escuchando en puerto 3001");
});

