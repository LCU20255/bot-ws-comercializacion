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

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log("\n📲 ESCANEA ESTE CÓDIGO QR CON WHATSAPP EN TU TELÉFONO:\n");
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

      // Si fue logout, limpiar sesión
      if (!shouldReconnect) {
        console.log("Sesión cerrada. Limpiando credenciales para nuevo QR...");
        try {
          fs.rmSync(AUTH_DIR, { recursive: true, force: true });
        } catch (e) {}
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
      const phone = remoteJid.replace("@s.whatsapp.net", "");

      // Extraer texto del mensaje
      let text =
        msg.message?.conversation ||
        msg.message?.extendedTextMessage?.text ||
        msg.message?.buttonsResponseMessage?.selectedButtonId ||
        msg.message?.listResponseMessage?.singleSelectReply?.selectedRowId ||
        "";

      text = text.trim();
      if (!text) continue;

      console.log(`📩 [WhatsApp Inbound] De: +${phone} | Mensaje: "${text}"`);

      try {
        // Enviar a nuestro motor de IA / NLU en Python
        const response = await axios.post(PYTHON_API_URL, {
          phone: `+${phone}`,
          message: text
        });

        const { reply, image_url } = response.data;
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

        // Si no hay imagen o falló la carga de imagen, enviar texto
        await sock.sendMessage(remoteJid, { text: reply });
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
