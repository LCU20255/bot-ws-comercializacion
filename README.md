# 🤖 Bot de Comercialización y Agendamiento (WhatsApp + Baileys + Panel Web)

Sistema integral de autogestión comercial para captación de clientes, catálogo interactivo de productos con imágenes, y agendamiento de citas de retiro presencial en sede.

---

## 🚀 Inicio Rápido (1 Clic)

Simplemente ejecuta el archivo:
```bash
iniciar_todo.bat
```
Este script:
1. Inicia el servidor Backend en Python (`http://localhost:8000/admin`).
2. Levanta el cliente de WhatsApp con **Baileys** y muestra el **Código QR** en pantalla para escanear con tu teléfono.
3. Abre automáticamente tu navegador en el panel administrativo.

---

## 📱 Cómo Vincular tu WhatsApp

1. Abre WhatsApp en tu teléfono.
2. Ve a **Ajustes > Dispositivos vinculados > Vincular un dispositivo**.
3. Escanea el código QR que aparece en la ventana negra de Baileys (o en la pestaña *Conexión QR / API* del panel web).
4. ¡Listo! El bot responderá automáticamente a cualquier cliente que escriba a ese número.

---

## 🛠️ Componentes del Sistema

### 1. Motor NLU e Inteligencia Conversacional (`app/nlu_engine.py`)
- Comprende lenguaje natural sin obligar al usuario a escribir solo números.
- Si el usuario dice *"Quiero saber cuándo retiro los uniformes"*, detecta el producto directamente.
- Parsea automáticamente bloques de datos: **Nombre, Cédula de Identidad (V-...), Teléfono, Fecha y Hora de Retiro**.

### 2. Panel Administrativo (`http://localhost:8000/admin`)
- **Agendamientos**: Lista de citas en tiempo real con estados (*Pendiente, Confirmada, Retirada, Cancelada*).
- **Exportación**: Descarga con un solo clic a **Excel (.xlsx)** y **CSV**.
- **Productos**: Permite agregar nuevos productos, cambiar precios, descripción, stock y fotos sin tocar código.
- **Simulador en Vivo**: Un chat idéntico a WhatsApp dentro del navegador para probar el flujo sin gastar mensajes.
- **Sincronización con Supabase**: Si configuras las credenciales en `.env`, sincroniza a la nube con un clic.

### 3. Cliente WhatsApp Baileys (`whatsapp_baileys/`)
- Conexión ligera por WebSockets (sin navegador Chrome pesado).
- Compatible con servidores gratuitos como Render.

---

## ☁️ Despliegue en Render

El repositorio ya contiene:
- `Procfile`
- `render.yaml`
- `requirements.txt`

1. Sube tu código a GitHub.
2. En Render, crea un nuevo **Web Service** conectado a tu repositorio.
3. Comando de inicio: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
