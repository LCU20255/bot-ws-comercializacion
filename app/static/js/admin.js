// Estado global de la aplicación SIS-COMER
let orders = [];
let products = [];
let currentSelectedOrder = null;
let topProductsChartInstance = null;
let orderStatusChartInstance = null;

// Funciones utilitarias de formateo de fecha a DD/MM/AAAA
function formatDateDMY(dStr) {
  if (!dStr) return "-";
  const s = String(dStr).trim();
  if (!s || s === "-") return "-";
  // Si ya es DD/MM/YYYY
  if (/^\d{2}\/\d{2}\/\d{4}/.test(s)) return s;
  // Si es YYYY-MM-DD o ISO
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) {
    return `${m[3]}/${m[2]}/${m[1]}`;
  }
  return s;
}

function formatDateTimeDMY(dtStr) {
  if (!dtStr) return "-";
  const s = String(dtStr).trim();
  if (!s || s === "-") return "-";
  if (s.includes(" ")) {
    const [dPart, tPart] = s.split(" ");
    return `${formatDateDMY(dPart)} ${tPart}`;
  }
  return formatDateDMY(s);
}

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  loadBCVRate();
  loadOrders();
  loadProducts();
  loadConfig();
  loadLowStockAlerts();
  loadFinancialMetrics();

  // Actualización en tiempo real sin delay (cada 5 segundos)
  setInterval(() => {
    // Si no hay un modal abierto actualmente
    const anyModalOpen = document.querySelector(".modal.show");
    if (!anyModalOpen) {
      loadOrders(false);
      loadInventoryKardex();
      loadWaitlist();
      loadFinancialMetrics();
    }
  }, 5000);
});

// ----------------- SWEETALERT2 HELPERS -----------------
function notifySuccess(title, text = "") {
  if (window.Swal) {
    Swal.fire({
      icon: 'success',
      title: title,
      text: text,
      confirmButtonColor: '#166534',
      confirmButtonText: 'Aceptar',
      customClass: { popup: 'swal2-custom-popup' }
    });
  } else {
    alert(title + (text ? "\n\n" + text : ""));
  }
}

function notifyError(title, text = "") {
  if (window.Swal) {
    Swal.fire({
      icon: 'error',
      title: title,
      text: text,
      confirmButtonColor: '#dc2626',
      confirmButtonText: 'Cerrar',
      customClass: { popup: 'swal2-custom-popup' }
    });
  } else {
    alert(title + (text ? "\n\n" + text : ""));
  }
}

async function confirmAction(title, text, confirmBtn = "Sí, continuar", isDanger = false) {
  if (window.Swal) {
    const res = await Swal.fire({
      title: title,
      text: text,
      icon: isDanger ? 'warning' : 'question',
      showCancelButton: true,
      confirmButtonColor: isDanger ? '#dc2626' : '#166534',
      cancelButtonColor: '#64748b',
      confirmButtonText: confirmBtn,
      cancelButtonText: 'Cancelar',
      reverseButtons: true,
      customClass: { popup: 'swal2-custom-popup' }
    });
    return res.isConfirmed;
  }
  return confirm(text || title);
}

// ----------------- BCV TASA OFICIAL -----------------
async function loadBCVRate() {
  try {
    const res = await fetch("/api/bcv/today");
    const data = await res.json();
    const rateVal = parseFloat(data.rate || 0).toLocaleString('es-VE', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
    const topTicker = document.getElementById("topbar-bcv-rate");
    const metricBCV = document.getElementById("metric-bcv-rate");
    if (topTicker) topTicker.innerText = `Bs. ${rateVal} / $`;
    if (metricBCV) metricBCV.innerText = `Bs. ${rateVal}`;
  } catch (err) {
    console.error("Error al obtener tasa BCV:", err);
  }
}

// ----------------- TABS SYSTEM -----------------
function initTabs() {
  const tabs = document.querySelectorAll(".nav-item");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      const target = tab.getAttribute("data-tab");
      document.querySelectorAll(".tab-view").forEach(v => v.classList.remove("active"));
      const view = document.getElementById(`tab-${target}`);
      if (view) view.classList.add("active");

      const titleMap = {
        orders: "Pedidos & Agendamientos de Retiro",
        report: "Cierre de Caja & Reporte Diario de Ventas",
        inventory: "Control de Inventario & Kardex",
        metrics: "Análisis Comercial & Rendimiento de Ventas",
        products: "Catálogo de Suministros & Textiles Militares",
        connection: "Vincular WhatsApp con Baileys (QR)",
        settings: "Horarios, Mantenimiento y Parámetros"
      };
      const subMap = {
        orders: "Control de solicitudes militares, pagos previos OCR y entrega presencial.",
        report: "Consolidado oficial de ventas, desglose de uniformes por modelo/talla y detalle específico de operaciones.",
        inventory: "Auditoría y control de movimientos de inventario en SIS-COMER.",
        metrics: "Facturación consolidada en Divisas ($) y Bolívares (Bs) a tasa BCV oficial.",
        products: "Gestiona los artículos de intendencia, precios, tallas, fotos y stock en tiempo real.",
        connection: "Escanea el código QR de Baileys para activar la atención automática.",
        settings: "Configura el horario laboral de 8:00 AM a 5:00 PM y modo auditoría."
      };
      const titleEl = document.getElementById("page-title");
      const subEl = document.getElementById("page-subtitle");
      if (titleEl) titleEl.innerText = titleMap[target] || "Panel SIS-COMER";
      if (subEl) subEl.innerText = subMap[target] || "";

      // Lazy loads específicos de cada tab
      if (target === "report") {
        loadDailyReport();
      } else if (target === "inventory") {
        loadInventoryKardex();
        loadLowStockAlerts();
        loadWaitlist();
      } else if (target === "metrics") {
        loadFinancialMetrics();
      } else if (target === "connection") {
        checkBaileysStatus();
      }
    });
  });
}

// ----------------- ORDERS / SOLICITUDES -----------------
async function loadOrders() {
  try {
    const res = await fetch("/api/orders");
    orders = await res.json();
    renderOrders(orders);
    updateKPIs(orders);
    const badge = document.getElementById("badge-orders-count");
    if (badge) badge.innerText = orders.length;
  } catch (err) {
    console.error("Error al cargar pedidos:", err);
  }
}

function updateKPIs(data) {
  const setEl = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.innerText = val;
  };
  setEl("kpi-total", data.length);
  setEl("kpi-pending", data.filter(o => (o.status || "").toUpperCase().includes("PENDIENTE")).length);

  // Totales de facturación en $ y Bs
  const validOrders = data.filter(o => (o.status || "").toUpperCase() !== "CANCELADA");
  const totalUsd = validOrders.reduce((sum, o) => sum + (parseFloat(o.amount_usd || o.total_amount || 0)), 0);
  const totalVes = validOrders.reduce((sum, o) => sum + (parseFloat(o.amount_ves || 0)), 0);

  const usdEl = document.getElementById("kpi-sales-usd");
  const vesEl = document.getElementById("kpi-sales-ves");
  if (usdEl) usdEl.innerText = `$${totalUsd.toFixed(2)} Ref`;
  if (vesEl) vesEl.innerText = `Bs. ${totalVes.toLocaleString('es-VE', {minimumFractionDigits: 2})}`;
}

function renderOrders(data) {
  const tbody = document.getElementById("orders-tbody");
  if (!tbody) return;

  if (!data || data.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="text-center text-muted" style="padding: 35px;">No hay pedidos registrados todavía.</td></tr>`;
    return;
  }

  tbody.innerHTML = data.map(item => {
    const status = (item.status || "PENDIENTE POR ATENCIÓN").toUpperCase();
    let badgeClass = "pendiente";
    if (status === "CONFIRMADA" || status === "POR RETIRAR") badgeClass = "confirmada";
    else if (status === "RETIRADA") badgeClass = "retirada";
    else if (status === "CANCELADA") badgeClass = "cancelada";
    else if (status === "EN ESPERA POR MANTENIMIENTO") badgeClass = "mantenimiento";

    const isLid = item.phone && (item.phone.includes("@lid") || item.phone.includes("@") || item.phone.length > 15);
    const cleanPhone = (item.phone || "").replace(/\D/g, "");
    const waLink = `https://wa.me/${cleanPhone}`;

    const amountUsd = parseFloat(item.amount_usd || item.total_amount || 0);
    const amountVes = parseFloat(item.amount_ves || 0);

    return `
      <tr onclick="openOrderDetail(${item.id})" class="order-row" style="cursor: pointer;">
        <td>
          <span class="ticket-tag">${item.ticket_code}</span>
          ${item.created_at ? `<br><small class="text-muted" style="font-size: 0.72rem; white-space: nowrap;"><i class="bi bi-clock-history"></i> ${formatDateTimeDMY(item.created_at)}</small>` : ''}
        </td>
        <td>
          <strong style="color: #0f172a; font-size: 0.9rem;">${item.client_name}</strong>
          ${item.is_off_hours ? '<br><small style="color: #b45309; font-weight: 600;"><i class="bi bi-moon-stars"></i> Fuera de horario</small>' : ''}
          ${item.status === 'EN ESPERA POR MANTENIMIENTO' ? '<br><small style="color: #d97706; font-weight: 600;"><i class="bi bi-tools"></i> En mantenimiento</small>' : ''}
        </td>
        <td>
          <code style="background: #f1f5f9; padding: 2px 6px; border-radius: 4px; color: #1e293b; font-weight: 600;">${item.cedula}</code>
        </td>
        <td onclick="event.stopPropagation()">
          ${isLid ? `
            <div style="display: flex; flex-direction: column; gap: 4px; align-items: flex-start;">
              <span class="status-badge" style="background: #fef2f2; color: #b91c1c; font-size: 0.72rem; padding: 3px 8px; border: 1px solid #fecaca; display: inline-flex; align-items: center; gap: 4px;" title="ID de privacidad de WhatsApp (@lid)">
                <i class="bi bi-shield-lock-fill"></i> Privacidad (@lid)
              </span>
              <button class="btn btn-sm btn-link" style="padding: 0; font-size: 0.75rem; color: #0284c7; text-decoration: underline; border: none; background: none; cursor: pointer;" onclick="openEditOrderModalDirect(${item.id})">
                <i class="bi bi-pencil-square"></i> Asignar Teléfono
              </button>
            </div>
          ` : `
            <a href="${waLink}" target="_blank" style="color: #0284c7; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; font-weight: 600;">
              <i class="bi bi-whatsapp" style="color: #16a34a;"></i> ${item.phone}
            </a>
          `}
        </td>
        <td style="max-width: 250px; font-weight: 500; color: #334155;">
          ${item.items_summary}
        </td>
        <td style="color: #166534; font-weight: 800; font-size: 0.95rem;">
          $${amountUsd.toFixed(2)} Ref
          ${amountVes > 0 ? `<br><small style="color: #64748b; font-size: 0.78rem; font-weight: 600;">Bs. ${amountVes.toLocaleString('es-VE', {minimumFractionDigits: 2})}</small>` : ''}
        </td>
        <td style="font-size: 0.78rem; color: #475569;">
          <div style="font-weight: 700; color: #1e293b;">${item.receipt_bank || item.payment_method || 'PAGO MÓVIL'}</div>
          ${item.receipt_ref ? `<span class="badge" style="background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; font-family: monospace; font-size: 0.75rem;">Ref: ${item.receipt_ref}</span>` : '<span class="text-muted" style="font-size: 0.72rem;">Sin Ref OCR</span>'}
        </td>
        <td>
          <div style="font-weight: 600; color: #1e293b;"><i class="bi bi-calendar3 text-muted"></i> ${formatDateDMY(item.pickup_date)}</div>
          <small class="text-muted"><i class="bi bi-clock"></i> ${item.pickup_time}</small>
        </td>
        <td>
          <span class="status-badge ${badgeClass}">${status}</span>
        </td>
        <td onclick="event.stopPropagation()">
          <div style="display: flex; gap: 6px; align-items: center;">
            <select style="padding: 4px 6px; font-size: 0.72rem;" onchange="changeOrderStatus(${item.id}, this.value)">
              <option value="PENDIENTE POR CONFIRMAR PAGO" ${status === 'PENDIENTE POR CONFIRMAR PAGO' ? 'selected' : ''}>PENDIENTE POR CONFIRMAR PAGO</option>
              <option value="PENDIENTE POR ATENCIÓN" ${status === 'PENDIENTE POR ATENCIÓN' ? 'selected' : ''}>PENDIENTE POR ATENCIÓN</option>
              <option value="CONFIRMADA" ${status === 'CONFIRMADA' ? 'selected' : ''}>CONFIRMADA</option>
              <option value="POR RETIRAR" ${status === 'POR RETIRAR' ? 'selected' : ''}>POR RETIRAR</option>
              <option value="RETIRADA" ${status === 'RETIRADA' ? 'selected' : ''}>RETIRADA</option>
              <option value="EN ESPERA POR MANTENIMIENTO" ${status === 'EN ESPERA POR MANTENIMIENTO' ? 'selected' : ''}>EN ESPERA POR MANTENIMIENTO</option>
              <option value="CANCELADA" ${status === 'CANCELADA' ? 'selected' : ''}>CANCELADA</option>
            </select>
            <button class="btn btn-icon btn-sm" onclick="openOrderDetail(${item.id})" title="Ver Detalles"><i class="bi bi-eye"></i></button>
            <a href="/invoice/${item.id}" target="_blank" class="btn btn-icon btn-sm" style="color: #c5a059;" title="Ver Factura / Recibo Oficial CIT"><i class="bi bi-receipt"></i></a>
            <button class="btn btn-icon btn-sm text-danger" onclick="deleteOrder(${item.id})" title="Eliminar"><i class="bi bi-trash3"></i></button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

function filterOrders() {
  const query = document.getElementById("search-order").value.toLowerCase();
  const status = document.getElementById("filter-status").value;

  const filtered = orders.filter(o => {
    const matchesQuery = (o.client_name || "").toLowerCase().includes(query) ||
                         (o.cedula || "").toLowerCase().includes(query) ||
                         (o.ticket_code || "").toLowerCase().includes(query) ||
                         (o.items_summary || "").toLowerCase().includes(query);
    const matchesStatus = (status === "ALL") || (o.status === status);
    return matchesQuery && matchesStatus;
  });

  renderOrders(filtered);
}

async function changeOrderStatus(id, newStatus) {
  try {
    const res = await fetch(`/api/orders/${id}/status`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus })
    });
    if (res.ok) {
      loadOrders();
    } else {
      notifyError("Error", "No se pudo actualizar el estado.");
    }
  } catch (err) {
    notifyError("Error", "Error al actualizar estado");
  }
}

// ----------------- MODAL DETALLE DE PEDIDO -----------------
function openOrderDetail(orderId) {
  const order = orders.find(o => o.id === orderId);
  if (!order) return;
  currentSelectedOrder = order;

  const titleEl = document.getElementById("order-modal-title");
  if (titleEl) titleEl.innerText = `Ticket Oficial: ${order.ticket_code}`;

  const contentEl = document.getElementById("order-modal-content");
  if (!contentEl) return;

  let itemsList = [];
  try {
    itemsList = typeof order.items_detail === "string" ? JSON.parse(order.items_detail) : (order.items_detail || []);
  } catch (e) {
    itemsList = [];
  }

  const itemsHtml = (itemsList && itemsList.length > 0) ? itemsList.map(it => `
    <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px dashed #e2e8f0;">
      <div>
        <strong style="color: #0f172a;">${it.qty}x</strong> <span>${it.name}</span>
        ${it.size ? `<span style="background: #e0f2fe; color: #0369a1; padding: 2px 7px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; margin-left: 6px;"><i class="bi bi-rulers"></i> TALLA: ${it.size}</span>` : ''}
      </div>
      <div>
        <strong style="color: #166534;">$${parseFloat(it.subtotal || 0).toFixed(2)} Ref</strong>
      </div>
    </div>
  `).join("") : `<div style="padding: 8px 0;">${order.items_summary}</div>`;

  const amountUsd = parseFloat(order.amount_usd || order.total_amount || 0);
  const amountVes = parseFloat(order.amount_ves || 0);

  let modalBadgeClass = "pendiente";
  const stUpper = (order.status || "").toUpperCase();
  if (stUpper === "CONFIRMADA" || stUpper === "POR RETIRAR") modalBadgeClass = "confirmada";
  else if (stUpper === "RETIRADA") modalBadgeClass = "retirada";
  else if (stUpper === "CANCELADA") modalBadgeClass = "cancelada";
  else if (stUpper === "EN ESPERA POR MANTENIMIENTO") modalBadgeClass = "mantenimiento";

  const ivaAmount = parseFloat(order.iva_amount || 0);
  const subtotalUsd = (ivaAmount > 0) ? (amountUsd - ivaAmount) : amountUsd;
  const bcvRate = parseFloat(order.bcv_rate_applied || 0);
  const ivaVes = (bcvRate > 0) ? (ivaAmount * bcvRate) : 0;
  const subtotalVes = (bcvRate > 0) ? (subtotalUsd * bcvRate) : (amountVes - ivaVes);

  contentEl.innerHTML = `
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px;">
      <div>
        <small class="text-muted" style="display: block; font-weight: 600;">CLIENTE REGISTRADO</small>
        <strong style="font-size: 1.05rem; color: #0f172a;">${order.client_name}</strong>
      </div>
      <div>
        <small class="text-muted" style="display: block; font-weight: 600;">CÉDULA DE IDENTIDAD</small>
        <code style="font-size: 1rem; color: #0f172a;">${order.cedula}</code>
      </div>
      <div>
        <small class="text-muted" style="display: block; font-weight: 600;">TELÉFONO DE CONTACTO</small>
        <span style="font-size: 0.95rem; font-weight: 700; color: #0284c7;"><i class="bi bi-telephone-fill"></i> ${order.phone}</span>
      </div>
      <div>
        <small class="text-muted" style="display: block; font-weight: 600;">FECHA DE REGISTRO</small>
        <span style="font-size: 0.88rem; color: #334155; font-weight: 600;"><i class="bi bi-clock-history"></i> ${formatDateTimeDMY(order.created_at)}</span>
      </div>
      <div>
        <small class="text-muted" style="display: block; font-weight: 600;">ESTADO DE ATENCIÓN</small>
        <span class="status-badge ${modalBadgeClass}">${order.status}</span>
      </div>
    </div>

    <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px;">
      <h4 style="font-size: 0.9rem; color: #0f172a; margin-bottom: 8px;"><i class="bi bi-box-seam"></i> Artículos Seleccionados:</h4>
      ${itemsHtml}
    </div>

    <!-- Desglose Financiero -->
    <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px;">
      ${ivaAmount > 0 ? `
        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: #166534; margin-bottom: 4px;">
          <span>Subtotal Neto:</span>
          <strong>$${subtotalUsd.toFixed(2)} Ref (Bs. ${subtotalVes.toLocaleString('es-VE', {minimumFractionDigits: 2})})</strong>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: #166534; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px dashed #86efac;">
          <span>IVA (16%):</span>
          <strong>$${ivaAmount.toFixed(2)} Ref (Bs. ${ivaVes.toLocaleString('es-VE', {minimumFractionDigits: 2})})</strong>
        </div>
      ` : ''}
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
        <div>
          <small style="color: #166534; font-weight: 700; display: block;">TOTAL EN DIVISAS</small>
          <strong style="font-size: 1.25rem; color: #166534;">$${amountUsd.toFixed(2)} REF</strong>
        </div>
        <div>
          <small style="color: #166534; font-weight: 700; display: block;">EQUIVALENTE EN BOLÍVARES</small>
          <strong style="font-size: 1.25rem; color: #166534;">Bs. ${amountVes.toLocaleString('es-VE', {minimumFractionDigits: 2})}</strong>
        </div>
      </div>
    </div>

    <!-- Panel de Auditoría Comprobante OCR & Datos Manuales -->
    <div class="ocr-receipt-box">
      <h4><i class="bi bi-receipt-cutoff"></i> Verificación y Comprobante Bancario</h4>
      <div class="ocr-field-row">
        <span>Banco Emisor:</span>
        <strong>${order.receipt_bank || 'NO REGISTRADO'}</strong>
      </div>
      <div class="ocr-field-row">
        <span>Nro. de Referencia:</span>
        <code style="font-weight: 700; color: #0284c7;">${order.receipt_ref || 'S/REF'}</code>
      </div>
      <div class="ocr-field-row">
        <span>Fecha de Pago Declarada:</span>
        <strong>${formatDateDMY(order.receipt_date)}</strong>
      </div>
      <div class="ocr-field-row">
        <span>Tasa BCV Aplicada en Pago:</span>
        <strong>Bs. ${order.bcv_rate_applied ? order.bcv_rate_applied.toFixed(4) : '-'} / $</strong>
      </div>
      ${order.manual_payment_data ? `
        <div style="margin-top: 8px; padding: 8px 10px; background: #fefce8; border: 1px solid #fef08a; border-radius: 6px; font-size: 0.8rem; color: #854d0e;">
          <strong><i class="bi bi-pencil-square"></i> Datos reportados por el cliente (WhatsApp):</strong><br>
          ${order.manual_payment_data}
        </div>
      ` : ''}
      ${order.ocr_raw_text ? `
        <div style="margin-top: 8px; font-size: 0.78rem; background: #ffffff; padding: 6px 10px; border-radius: 4px; border: 1px solid #e2e8f0; color: #64748b; font-family: monospace; white-space: pre-wrap; max-height: 80px; overflow-y: auto;">
          <strong>Texto extraído por OCR:</strong><br>${order.ocr_raw_text}
        </div>
      ` : ''}
    </div>

    <!-- Historial de Estados (Auditoría) -->
    <div style="margin-top: 14px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px;">
      <h4 style="font-size: 0.88rem; color: #0f172a; margin-bottom: 8px;"><i class="bi bi-clock-history"></i> Historial de Cambios de Estado:</h4>
      <div id="order-history-timeline-${order.id}" style="font-size: 0.8rem; color: #64748b;">
        Cargando historial de auditoría...
      </div>
    </div>

    <!-- Cita de Retiro -->
    <div style="margin-top: 14px; font-size: 0.9rem; color: #334155; display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #e2e8f0; padding-top: 12px;">
      <div>
        <i class="bi bi-calendar-event"></i> <strong>Retiro Programado:</strong> ${formatDateDMY(order.pickup_date)} a las ${order.pickup_time}
      </div>
      <div style="display: flex; gap: 8px;">
        <a href="/invoice/${order.id}" target="_blank" class="btn btn-sm btn-dark" style="background: #0f233a; border-color: #c5a059; color: #e6ca85;"><i class="bi bi-receipt"></i> Ver Factura / Recibo</a>
        <button class="btn btn-sm btn-primary" onclick="openEditOrderModalDirect(${order.id})"><i class="bi bi-pencil"></i> Editar</button>
        <button class="btn btn-sm btn-secondary" onclick="closeOrderModal()">Cerrar</button>
      </div>
    </div>
  `;

  document.getElementById("order-detail-modal").classList.add("show");
  fetchOrderHistory(order.id);
}

async function fetchOrderHistory(orderId) {
  const container = document.getElementById(`order-history-timeline-${orderId}`);
  if (!container) return;
  try {
    const res = await fetch(`/api/orders/${orderId}/history`);
    if (!res.ok) {
      container.innerHTML = `<span class="text-muted">No se pudo cargar el historial.</span>`;
      return;
    }
    const history = await res.json();
    if (!history || history.length === 0) {
      container.innerHTML = `<span class="text-muted">Sin cambios de estado adicionales registrados.</span>`;
      return;
    }
    container.innerHTML = history.map(h => `
      <div style="padding: 6px 0; border-bottom: 1px dashed #f1f5f9; display: flex; justify-content: space-between; align-items: center;">
        <div>
          <span style="font-weight: 700; color: #1e293b;">${h.previous_status || 'INICIAL'} &rarr; ${h.new_status}</span>
          ${h.notes ? `<br><small style="color: #64748b;">${h.notes}</small>` : ''}
        </div>
        <div style="text-align: right; font-size: 0.72rem; color: #94a3b8;">
          <span style="font-weight: 600; color: #0284c7;">${h.changed_by || 'SISTEMA'}</span><br>
          ${formatDateTimeDMY(h.created_at)}
        </div>
      </div>
    `).join("");
  } catch (e) {
    container.innerHTML = `<span class="text-muted">Error cargando historial.</span>`;
  }
}

function closeOrderModal() {
  const modal = document.getElementById("order-detail-modal");
  if (modal) modal.classList.remove("show");
  currentSelectedOrder = null;
}

// ----------------- INVENTARIO & KARDEX -----------------
async function loadInventoryKardex() {
  try {
    const res = await fetch("/api/inventory/movements");
    const movements = await res.json();
    const tbody = document.getElementById("kardex-tbody");
    if (!tbody) return;
    if (!movements || movements.length === 0) {
      tbody.innerHTML = `<tr><td colspan="9" class="text-center text-muted" style="padding: 30px;">No hay movimientos registrados en el Kardex.</td></tr>`;
      return;
    }
    tbody.innerHTML = movements.map(m => {
      const isIn = m.movement_type === "ENTRADA_TALLER";
      const badge = isIn ? '<span class="kardex-badge-in">ENTRADA INVENTARIO</span>' : '<span class="kardex-badge-out">SALIDA VENTA</span>';
      return `
        <tr>
          <td><code style="color: #64748b;">#${m.id}</code></td>
          <td>${formatDateTimeDMY(m.created_at)}</td>
          <td><strong>${m.product_name || 'PRODUCTO #' + m.product_id}</strong></td>
          <td>${badge}</td>
          <td style="font-weight: 700; color: ${isIn ? '#166534' : '#b45309'};">${m.quantity > 0 ? '+' : ''}${m.quantity}</td>
          <td>${m.previous_stock}</td>
          <td><strong>${m.new_stock}</strong></td>
          <td>${m.client_name ? m.client_name : (m.created_by || 'SISTEMA')}</td>
          <td><small class="text-muted">${m.notes || '-'}</small></td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    console.error("Error cargando kardex:", err);
  }
}

async function loadLowStockAlerts() {
  try {
    const res = await fetch("/api/inventory/low-stock");
    const lowStockProds = await res.json();
    const badge = document.getElementById("badge-low-stock");
    const kpiLow = document.getElementById("kpi-low-stock");
    const alertBox = document.getElementById("low-stock-alert-box");
    const badgeContainer = document.getElementById("low-stock-list");
    
    if (kpiLow) kpiLow.innerText = lowStockProds.length;
    if (badge) {
      if (lowStockProds.length > 0) {
        badge.innerText = lowStockProds.length;
        badge.style.display = "inline-block";
      } else {
        badge.style.display = "none";
      }
    }
    if (alertBox && badgeContainer) {
      if (lowStockProds.length > 0) {
        alertBox.style.display = "flex";
        badgeContainer.innerHTML = lowStockProds.map(p => 
          `<span class="badge-stock-danger"><i class="bi bi-tag-fill"></i> ${p.name}: ${p.stock} Uds restantes</span>`
        ).join("");
      } else {
        alertBox.style.display = "none";
      }
    }
  } catch (err) {
    console.error("Error cargando alertas de stock:", err);
  }
}

function openBatchStockModal() {
  const sel = document.getElementById("batch_product_id");
  if (sel) {
    sel.innerHTML = '<option value="">Seleccione un producto...</option>' + 
      products.map(p => `<option value="${p.id}">${p.name} (Stock actual: ${p.stock})</option>`).join("");
  }
  document.getElementById("batch-stock-modal").classList.add("show");
}

function closeBatchStockModal() {
  document.getElementById("batch-stock-modal").classList.remove("show");
  document.getElementById("batch-stock-form").reset();
}

async function submitBatchStock(e) {
  e.preventDefault();
  const pid = parseInt(document.getElementById("batch_product_id").value);
  const qty = parseInt(document.getElementById("batch_quantity").value);
  const notes = document.getElementById("batch_notes").value;
  const createdBy = document.getElementById("batch_created_by").value;
  
  if (!pid || !qty || qty <= 0) {
    notifyError("Error", "Seleccione un producto y cantidad válida mayor a 0.");
    return;
  }
  
  try {
    const res = await fetch("/api/inventory/batch-add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: pid, quantity: qty, notes: notes, created_by: createdBy })
    });
    const data = await res.json();
    if (res.ok) {
      closeBatchStockModal();
      notifySuccess("Lote Ingresado", data.message || `Se ingresaron ${qty} unidades al inventario.`);
      loadProducts();
      loadInventoryKardex();
      loadLowStockAlerts();
      loadWaitlist();
    } else {
      notifyError("Error", data.detail || "No se pudo ingresar el lote.");
    }
  } catch (err) {
    notifyError("Error", "Error de red ingresando lote.");
  }
}

// ----------------- LISTA DE ESPERA (WAITLIST) -----------------
async function loadWaitlist() {
  const tbody = document.getElementById("waitlist-tbody");
  if (!tbody) return;
  try {
    const res = await fetch("/api/waitlist");
    const list = await res.json();
    if (!list || list.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted" style="padding: 30px;">No hay clientes en lista de espera actualmente.</td></tr>`;
      return;
    }
    tbody.innerHTML = list.map(item => {
      const status = (item.status || "PENDIENTE").toUpperCase();
      let badgeStyle = "background: #fef3c7; color: #b45309; border: 1px solid #fde68a;";
      if (status === "EN CONTACTO") badgeStyle = "background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd;";
      else if (status === "NOTIFICADO") badgeStyle = "background: #dcfce7; color: #15803d; border: 1px solid #86efac;";
      else if (status === "CANCELADO") badgeStyle = "background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5;";
      else if (status === "CONVERTIDO EN PEDIDO") badgeStyle = "background: #f3e8ff; color: #7e22ce; border: 1px solid #d8b4fe;";

      const cleanPhone = (item.phone || "").replace(/\D/g, "");
      const waLink = `https://wa.me/${cleanPhone}`;

      return `
        <tr>
          <td><code style="color: #64748b; font-weight: 700;">#${item.id}</code></td>
          <td><small style="color: #475569; font-weight: 500;"><i class="bi bi-clock-history text-muted"></i> ${formatDateTimeDMY(item.created_at)}</small></td>
          <td><strong>${item.client_name || 'CLIENTE'}</strong></td>
          <td>
            <a href="${waLink}" target="_blank" style="color: #0284c7; text-decoration: none; font-weight: 600; display: inline-flex; align-items: center; gap: 4px;">
              <i class="bi bi-whatsapp" style="color: #16a34a;"></i> ${item.phone}
            </a>
          </td>
          <td><span style="font-weight: 700; color: #0f172a;">${item.product_name}</span></td>
          <td>
            <select style="padding: 4px 8px; font-size: 0.75rem; font-weight: 700; border-radius: 6px; cursor: pointer; ${badgeStyle}" onchange="changeWaitlistStatus(${item.id}, this.value)">
              <option value="PENDIENTE" ${status === 'PENDIENTE' ? 'selected' : ''}>PENDIENTE</option>
              <option value="EN CONTACTO" ${status === 'EN CONTACTO' ? 'selected' : ''}>EN CONTACTO</option>
              <option value="NOTIFICADO" ${status === 'NOTIFICADO' ? 'selected' : ''}>NOTIFICADO</option>
              <option value="CANCELADO" ${status === 'CANCELADO' ? 'selected' : ''}>CANCELADO</option>
              <option value="CONVERTIDO EN PEDIDO" ${status === 'CONVERTIDO EN PEDIDO' ? 'selected' : ''}>CONVERTIDO EN PEDIDO</option>
            </select>
          </td>
          <td>
            <div style="display: flex; gap: 6px; align-items: center;">
              ${status !== 'CONVERTIDO EN PEDIDO' ? `
                <button class="btn btn-sm btn-primary" onclick="openConvertWaitlistModal(${item.id})" style="font-size: 0.75rem; padding: 4px 9px; white-space: nowrap; background: #166534; border-color: #14532d;">
                  <i class="bi bi-box-arrow-in-right"></i> Convertir a Pedido
                </button>
              ` : `
                <span class="badge" style="background: #f3e8ff; color: #7e22ce; font-size: 0.72rem; padding: 4px 8px; border-radius: 4px; font-weight: 700;"><i class="bi bi-check2-all"></i> Pedido Creado</span>
              `}
              <button class="btn btn-icon btn-sm" onclick="notifyWaitlistEntry(${item.id})" title="Enviar WhatsApp Notificación"><i class="bi bi-send-fill" style="color: #0284c7;"></i></button>
              <button class="btn btn-icon btn-sm text-danger" onclick="deleteWaitlistEntry(${item.id})" title="Eliminar"><i class="bi bi-trash3"></i></button>
            </div>
          </td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    console.error("Error cargando lista de espera:", err);
  }
}

async function changeWaitlistStatus(id, newStatus) {
  try {
    const res = await fetch(`/api/waitlist/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: newStatus })
    });
    if (res.ok) {
      notifySuccess("Estado Actualizado", `Lista de espera #${id} cambiada a ${newStatus}`);
      loadWaitlist();
    } else {
      notifyError("Error", "No se pudo actualizar el estado.");
    }
  } catch (err) {
    notifyError("Error", "Error de red al actualizar estado.");
  }
}

async function deleteWaitlistEntry(id) {
  const ok = await confirmAction("¿Eliminar registro?", "Se quitará a este cliente de la lista de espera.", "Eliminar", true);
  if (!ok) return;
  try {
    const res = await fetch(`/api/waitlist/${id}`, { method: "DELETE" });
    if (res.ok) {
      notifySuccess("Eliminado", "Registro quitado de la lista de espera.");
      loadWaitlist();
    } else {
      notifyError("Error", "No se pudo eliminar.");
    }
  } catch (err) {
    notifyError("Error", "Error de red al eliminar.");
  }
}

async function notifyWaitlistEntry(id) {
  try {
    const res = await fetch(`/api/waitlist/${id}/notify`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      notifySuccess("Notificación Enviada", data.message);
      loadWaitlist();
    } else {
      notifyError("Error", data.detail || "No se pudo enviar la notificación");
    }
  } catch (err) {
    notifyError("Error", "Error de red al notificar al cliente");
  }
}

let currentWaitlistConvertItem = null;

async function openConvertWaitlistModal(id) {
  try {
    const res = await fetch("/api/waitlist");
    const list = await res.json();
    const item = list.find(w => w.id === id);
    if (!item) return;

    currentWaitlistConvertItem = item;
    document.getElementById("conv_waitlist_id").value = item.id;
    document.getElementById("conv_client_name").value = item.client_name || "";
    document.getElementById("conv_phone").value = item.phone || "";
    document.getElementById("conv_cedula").value = "V-";
    document.getElementById("conv_product_name").value = item.product_name;
    document.getElementById("conv_product_id").value = item.product_id || "";
    document.getElementById("conv_qty").value = 1;

    let unitPrice = 0;
    if (products && products.length > 0) {
      const p = products.find(prod => prod.id === item.product_id || prod.name.toUpperCase() === item.product_name.toUpperCase());
      if (p) unitPrice = parseFloat(p.price || 0);
    }
    document.getElementById("conv_unit_price").value = unitPrice;
    document.getElementById("conv_amount_usd").value = unitPrice > 0 ? unitPrice.toFixed(2) : "0.00";

    const nowD = new Date();
    const dDay = String(nowD.getDate()).padStart(2, '0');
    const dMonth = String(nowD.getMonth() + 1).padStart(2, '0');
    const dYear = nowD.getFullYear();
    const todayStrDMY = `${dDay}/${dMonth}/${dYear}`;
    document.getElementById("conv_pickup_date").value = todayStrDMY;
    document.getElementById("conv_pickup_time").value = "09:00 AM";

    document.getElementById("convert-waitlist-modal").classList.add("show");
  } catch (err) {
    notifyError("Error", "No se pudo abrir el convertidor.");
  }
}

function closeConvertWaitlistModal() {
  const modal = document.getElementById("convert-waitlist-modal");
  if (modal) modal.classList.remove("show");
  currentWaitlistConvertItem = null;
}

function calcConvertTotal() {
  const qty = parseInt(document.getElementById("conv_qty").value) || 1;
  const unitPrice = parseFloat(document.getElementById("conv_unit_price").value) || 0;
  if (unitPrice > 0) {
    document.getElementById("conv_amount_usd").value = (qty * unitPrice).toFixed(2);
  }
}

async function submitConvertWaitlist(e) {
  e.preventDefault();
  const waitlistId = parseInt(document.getElementById("conv_waitlist_id").value);
  const payload = {
    client_name: document.getElementById("conv_client_name").value.trim().toUpperCase(),
    cedula: document.getElementById("conv_cedula").value.trim().toUpperCase(),
    phone: document.getElementById("conv_phone").value.trim(),
    qty: parseInt(document.getElementById("conv_qty").value) || 1,
    total_amount: parseFloat(document.getElementById("conv_amount_usd").value) || 0,
    payment_method: document.getElementById("conv_payment_method").value,
    pickup_date: document.getElementById("conv_pickup_date").value,
    pickup_time: document.getElementById("conv_pickup_time").value,
    status: "PENDIENTE POR ATENCIÓN"
  };

  try {
    const res = await fetch(`/api/waitlist/${waitlistId}/convert-to-order`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (res.ok) {
      closeConvertWaitlistModal();
      notifySuccess("¡Convertido a Pedido!", data.message || "Pedido creado exitosamente.");
      loadWaitlist();
      loadOrders();
      loadInventoryKardex();
    } else {
      notifyError("Error", data.detail || "No se pudo convertir.");
    }
  } catch (err) {
    notifyError("Error", "Error de red al convertir a pedido.");
  }
}

async function resetSystemToVirgin() {
  const ok = await confirmAction(
    "¿Resetear TODO a Estado Virgen?",
    "Esta acción BORRARÁ todos los pedidos, citas, movimientos de Kardex, lista de espera, catálogo y clientes de prueba. El sistema quedará 100% virgen para su primer uso real.",
    "Sí, Resetear Todo",
    true
  );
  if (!ok) return;

  try {
    const res = await fetch("/api/system/reset-virgin", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      notifySuccess("Sistema Virgen", "Todas las tablas han sido reseteadas a estado virgen.");
      loadOrders();
      loadProducts();
      loadInventoryKardex();
      loadWaitlist();
      loadFinancialMetrics();
      loadLowStockAlerts();
    } else {
      notifyError("Error", data.detail || "No se pudo resetear el sistema.");
    }
  } catch (err) {
    notifyError("Error", "Error de comunicación con el servidor.");
  }
}


// ----------------- MÉTRICAS & CHART.JS -----------------
async function loadFinancialMetrics() {
  try {
    const res = await fetch("/api/metrics/financial");
    const m = await res.json();

    const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
    setEl("metric-total-usd", `$${(m.total_usd || 0).toFixed(2)} REF`);
    setEl("metric-total-ves", `Bs. ${(m.total_ves || 0).toLocaleString('es-VE', {minimumFractionDigits: 2})}`);
    setEl("metric-today-usd", `$${(m.today_usd || 0).toFixed(2)} REF`);
    setEl("metric-today-ves", `Bs. ${(m.today_ves || 0).toLocaleString('es-VE', {minimumFractionDigits: 2})}`);
    setEl("metric-total-orders", m.total_orders || 0);
    setEl("metric-today-orders", `Hoy: ${m.today_orders || 0} pedidos`);

    // Render Chart 1: Top Products Bar Chart
    const ctxTop = document.getElementById("chartTopProducts");
    if (ctxTop && window.Chart) {
      if (topProductsChartInstance) topProductsChartInstance.destroy();
      const labels = (m.top_products || []).map(p => p.name);
      const dataVals = (m.top_products || []).map(p => p.units_sold);
      topProductsChartInstance = new Chart(ctxTop, {
        type: 'bar',
        data: {
          labels: labels.length ? labels : ['Sin ventas registradas'],
          datasets: [{
            label: 'Unidades Vendidas',
            data: dataVals.length ? dataVals : [0],
            backgroundColor: 'rgba(22, 101, 52, 0.85)',
            borderColor: '#166534',
            borderWidth: 1,
            borderRadius: 6
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
        }
      });
    }

    // Render Chart 2: Order Status Breakdown Doughnut Chart
    const ctxStatus = document.getElementById("chartOrderStatus");
    if (ctxStatus && window.Chart) {
      if (orderStatusChartInstance) orderStatusChartInstance.destroy();
      const statusObj = m.status_breakdown || {};
      const statusLabels = Object.keys(statusObj);
      const statusCounts = Object.values(statusObj);
      orderStatusChartInstance = new Chart(ctxStatus, {
        type: 'doughnut',
        data: {
          labels: statusLabels.length ? statusLabels : ['Sin pedidos'],
          datasets: [{
            data: statusCounts.length ? statusCounts : [1],
            backgroundColor: ['#166534', '#eab308', '#0284c7', '#8b5cf6', '#dc2626', '#64748b']
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom' } }
        }
      });
    }
  } catch (err) {
    console.error("Error al cargar métricas:", err);
  }
}

// ----------------- EDIT ORDER MODAL -----------------
function openEditOrderModalDirect(orderId) {
  closeOrderModal();
  const order = orders.find(o => o.id === orderId);
  if (!order) return;

  document.getElementById("edit_order_id").value = order.id;
  document.getElementById("edit_client_name").value = order.client_name;
  document.getElementById("edit_cedula").value = order.cedula;
  document.getElementById("edit_phone").value = order.phone && !order.phone.includes("@lid") ? order.phone : "";
  document.getElementById("edit_items_summary").value = order.items_summary;
  document.getElementById("edit_total_amount").value = order.amount_usd || order.total_amount;
  document.getElementById("edit_payment_method").value = order.payment_method;
  document.getElementById("edit_pickup_date").value = formatDateDMY(order.pickup_date);
  document.getElementById("edit_pickup_time").value = order.pickup_time;
  document.getElementById("edit_status").value = order.status;

  document.getElementById("edit-order-modal").classList.add("show");
}

function closeEditOrderModal() {
  document.getElementById("edit-order-modal").classList.remove("show");
}

async function saveEditOrder(e) {
  e.preventDefault();
  const id = document.getElementById("edit_order_id").value;
  const payload = {
    client_name: document.getElementById("edit_client_name").value,
    cedula: document.getElementById("edit_cedula").value,
    phone: document.getElementById("edit_phone").value,
    items_summary: document.getElementById("edit_items_summary").value,
    total_amount: parseFloat(document.getElementById("edit_total_amount").value),
    payment_method: document.getElementById("edit_payment_method").value,
    pickup_date: document.getElementById("edit_pickup_date").value,
    pickup_time: document.getElementById("edit_pickup_time").value,
    status: document.getElementById("edit_status").value
  };

  try {
    const res = await fetch(`/api/orders/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      closeEditOrderModal();
      notifySuccess("Guardado", "Pedido actualizado con éxito.");
      loadOrders();
    } else {
      notifyError("Error", "No se pudo actualizar el pedido.");
    }
  } catch (err) {
    notifyError("Error", "Error de conexión.");
  }
}

async function deleteOrder(id) {
  const ok = await confirmAction("¿Eliminar registro?", "Esta acción eliminará el pedido de la base de datos.", "Eliminar", true);
  if (!ok) return;

  try {
    const res = await fetch(`/api/orders/${id}`, { method: "DELETE" });
    if (res.ok) {
      notifySuccess("Eliminado", "Pedido borrado.");
      loadOrders();
    }
  } catch (err) {
    notifyError("Error", "No se pudo eliminar.");
  }
}

// ----------------- PRODUCTS / CATÁLOGO -----------------
async function loadProducts() {
  try {
    const res = await fetch("/api/products");
    products = await res.json();
    renderProducts(products);
  } catch (err) {
    console.error("Error al cargar productos:", err);
  }
}

function renderProducts(data) {
  const grid = document.getElementById("products-grid");
  if (!grid) return;

  grid.innerHTML = data.map(p => {
    const isCritical = p.stock <= 20;
    return `
      <div class="product-card" style="border: ${isCritical ? '2px solid #ef4444' : '1px solid #e2e8f0'};">
        <div class="product-img-box">
          <img src="${p.image_url || '/static/images/placeholder.png'}" alt="${p.name}" onerror="this.src='/static/images/placeholder.png'">
          ${p.requires_size ? '<span class="tag-size"><i class="bi bi-rulers"></i> Con Talla</span>' : ''}
          ${isCritical ? '<span class="tag-critical" style="position: absolute; bottom: 8px; left: 8px; background: #dc2626; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.72rem; font-weight: 800;">RECONFECCIÓN</span>' : ''}
        </div>
        <div class="product-info">
          <div style="font-size: 0.72rem; font-weight: 700; color: #166534; text-transform: uppercase;">${p.category || 'MILITAR'}</div>
          <h4>${p.name}</h4>
          <p class="product-desc">${p.description || 'Sin especificación reglamentaria.'}</p>
          <div class="product-footer">
            <div>
              <span class="price-val">$${parseFloat(p.price || 0).toFixed(2)} Ref</span>
              <small class="stock-val" style="display: block; color: ${isCritical ? '#dc2626; font-weight: 700;' : '#64748b;'}">
                ${isCritical ? '⚠️ ' : ''}Stock: ${p.stock}
              </small>
            </div>
            <div style="display: flex; gap: 4px;">
              <button class="btn btn-sm btn-icon" onclick="editProduct(${p.id})"><i class="bi bi-pencil-square"></i></button>
              <button class="btn btn-sm btn-icon text-danger" onclick="deleteProduct(${p.id})"><i class="bi bi-trash3"></i></button>
            </div>
          </div>
        </div>
      </div>
    `;
  }).join("");
}

function toggleSizeOptions(val) {
  const grp = document.getElementById("prod_sizes_group");
  if (grp) {
    grp.style.display = (val === "1" || val === 1) ? "block" : "none";
  }
}

function openProductModal() {
  document.getElementById("product-modal-title").innerText = "Agregar Producto Militar";
  document.getElementById("product-form").reset();
  document.getElementById("prod_id").value = "";
  toggleSizeOptions(0);
  if (document.getElementById("prod_fabric")) document.getElementById("prod_fabric").value = "";
  if (document.getElementById("prod_thickness")) document.getElementById("prod_thickness").value = "";
  if (document.getElementById("prod_buttons")) document.getElementById("prod_buttons").value = "";
  if (document.getElementById("prod_durability")) document.getElementById("prod_durability").value = "";
  document.getElementById("product-modal").classList.add("show");
}

function closeProductModal() {
  document.getElementById("product-modal").classList.remove("show");
}

function editProduct(id) {
  const p = products.find(item => item.id === id);
  if (!p) return;

  document.getElementById("product-modal-title").innerText = "Editar Producto Militar";
  document.getElementById("prod_id").value = p.id;
  document.getElementById("prod_name").value = p.name;
  document.getElementById("prod_price").value = p.price;
  document.getElementById("prod_category").value = p.category;
  document.getElementById("prod_requires_size").value = p.requires_size ? "1" : "0";
  toggleSizeOptions(p.requires_size ? 1 : 0);
  const sizeInput = document.getElementById("prod_available_sizes");
  if (sizeInput) sizeInput.value = p.available_sizes || "";
  document.getElementById("prod_stock").value = p.stock;
  document.getElementById("prod_image").value = p.image_url || "";
  document.getElementById("prod_desc").value = p.description || "";
  
  if (document.getElementById("prod_fabric")) document.getElementById("prod_fabric").value = p.fabric || "";
  if (document.getElementById("prod_thickness")) document.getElementById("prod_thickness").value = p.thickness_weight || "";
  if (document.getElementById("prod_buttons")) document.getElementById("prod_buttons").value = p.buttons_closures || "";
  if (document.getElementById("prod_durability")) document.getElementById("prod_durability").value = p.durability || "";

  const kwEl = document.getElementById("prod_keywords");
  if (kwEl) kwEl.value = p.keywords || "";

  document.getElementById("product-modal").classList.add("show");
}

async function saveProduct(e) {
  e.preventDefault();
  const id = document.getElementById("prod_id").value;
  const kwEl = document.getElementById("prod_keywords");
  const sizeInput = document.getElementById("prod_available_sizes");
  const payload = {
    name: document.getElementById("prod_name").value.toUpperCase(),
    price: parseFloat(document.getElementById("prod_price").value),
    price_display: `$${parseFloat(document.getElementById("prod_price").value).toFixed(2)} Ref`,
    category: document.getElementById("prod_category").value.toUpperCase(),
    requires_size: parseInt(document.getElementById("prod_requires_size").value),
    available_sizes: sizeInput ? sizeInput.value.toUpperCase() : "",
    stock: parseInt(document.getElementById("prod_stock").value),
    image_url: document.getElementById("prod_image").value || "/static/images/placeholder.png",
    description: document.getElementById("prod_desc").value,
    fabric: document.getElementById("prod_fabric") ? document.getElementById("prod_fabric").value : "",
    thickness_weight: document.getElementById("prod_thickness") ? document.getElementById("prod_thickness").value : "",
    buttons_closures: document.getElementById("prod_buttons") ? document.getElementById("prod_buttons").value : "",
    durability: document.getElementById("prod_durability") ? document.getElementById("prod_durability").value : "",
    keywords: kwEl ? kwEl.value : "",
    updated_by: "ADMIN"
  };

  try {
    const url = id ? `/api/products/${id}` : "/api/products";
    const method = id ? "PUT" : "POST";
    const res = await fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      closeProductModal();
      notifySuccess("Guardado", "Producto registrado correctamente.");
      loadProducts();
      loadLowStockAlerts();
    } else {
      notifyError("Error", "No se pudo guardar el producto.");
    }
  } catch (err) {
    notifyError("Error", "Error de red al guardar producto.");
  }
}

async function deleteProduct(id) {
  const ok = await confirmAction("¿Eliminar producto?", "Esta acción quitará el producto del catálogo.", "Eliminar", true);
  if (!ok) return;

  try {
    const res = await fetch(`/api/products/${id}`, { method: "DELETE" });
    if (res.ok) {
      notifySuccess("Eliminado", "Producto removido.");
      loadProducts();
      loadLowStockAlerts();
    }
  } catch (err) {
    notifyError("Error", "No se pudo eliminar.");
  }
}

async function uploadProductImage(input) {
  if (!input.files || !input.files[0]) return;
  const formData = new FormData();
  formData.append("file", input.files[0]);

  try {
    const res = await fetch("/api/upload-image", { method: "POST", body: formData });
    const data = await res.json();
    if (data.status === "ok") {
      document.getElementById("prod_image").value = data.url;
      notifySuccess("Foto subida", "La imagen se cargó correctamente.");
    }
  } catch (e) {
    notifyError("Error al subir foto");
  }
}

// ----------------- WHATSAPP BAILEYS CONNECTION -----------------
let baileysPollingInterval = null;

function checkBaileysStatus() {
  if (baileysPollingInterval) clearInterval(baileysPollingInterval);
  fetchBaileysStatus();
  baileysPollingInterval = setInterval(fetchBaileysStatus, 3000);
}

async function fetchBaileysStatus() {
  try {
    const res = await fetch("/api/baileys/status");
    const data = await res.json();

    const badge = document.getElementById("conn-badge");
    const boxConn = document.getElementById("box-connected");
    const boxUnconn = document.getElementById("box-unconnected");
    const connPhone = document.getElementById("conn-phone");
    const qrImg = document.getElementById("qr-image");
    const spinner = document.getElementById("qr-loading-spinner");

    if (data.connected) {
      if (badge) {
        badge.innerText = "Línea Conectada";
        badge.className = "badge-status connected";
      }
      if (boxConn) boxConn.style.display = "block";
      if (boxUnconn) boxUnconn.style.display = "none";
      if (connPhone) connPhone.innerText = data.phone ? `+${data.phone}` : "Línea Activa";
    } else {
      if (badge) {
        badge.innerText = "Esperando Escaneo";
        badge.className = "badge-status pending";
      }
      if (boxConn) boxConn.style.display = "none";
      if (boxUnconn) boxUnconn.style.display = "block";

      if (data.has_qr && data.qr_url) {
        if (spinner) spinner.style.display = "none";
        if (qrImg) {
          qrImg.style.display = "block";
          qrImg.src = data.qr_url;
        }
      } else {
        if (spinner) spinner.style.display = "block";
        if (qrImg) qrImg.style.display = "none";
      }
    }
  } catch (err) {
    console.error("Error al consultar estado de Baileys:", err);
  }
}

async function restartBaileys(cleanAuth = false) {
  try {
    await fetch(`/api/baileys/restart?clean_auth=${cleanAuth}`, { method: "POST" });
    notifySuccess("Reiniciando Baileys", "Generando nuevo código QR...");
    checkBaileysStatus();
  } catch (e) {
    notifyError("Error reiniciando Baileys");
  }
}

async function unlinkWhatsApp() {
  const ok = await confirmAction("¿Desvincular línea?", "Se cerrará la sesión actual de WhatsApp para permitir escanear un nuevo número.", "Desvincular", true);
  if (!ok) return;
  restartBaileys(true);
}

function handleQRError() {
  const spinner = document.getElementById("qr-loading-spinner");
  const qrImg = document.getElementById("qr-image");
  if (spinner) spinner.style.display = "block";
  if (qrImg) qrImg.style.display = "none";
}

// ----------------- CONFIGURACIÓN & MANTENIMIENTO -----------------
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();

    const isMaint = cfg.maintenance_mode === "1";
    updateMaintUI(isMaint);

    if (cfg.maintenance_message && document.getElementById("cfg-maint-msg")) {
      document.getElementById("cfg-maint-msg").value = cfg.maintenance_message;
    }
    if (cfg.business_hours_start) document.getElementById("cfg-hour-start").value = cfg.business_hours_start;
    if (cfg.business_hours_end) document.getElementById("cfg-hour-end").value = cfg.business_hours_end;
    if (cfg.off_hours_message) document.getElementById("cfg-offhours-msg").value = cfg.off_hours_message;
    if (cfg.pickup_address) document.getElementById("cfg-pickup-address").value = cfg.pickup_address;
    if (cfg.advisor_name) document.getElementById("cfg-advisor-name").value = cfg.advisor_name;
    if (cfg.advisor_phone) document.getElementById("cfg-advisor-phone").value = cfg.advisor_phone;
    if (cfg.pagomovil_bank && document.getElementById("cfg-pm-bank")) document.getElementById("cfg-pm-bank").value = cfg.pagomovil_bank;
    if (cfg.pagomovil_phone && document.getElementById("cfg-pm-phone")) document.getElementById("cfg-pm-phone").value = cfg.pagomovil_phone;
    if (cfg.pagomovil_id && document.getElementById("cfg-pm-id")) document.getElementById("cfg-pm-id").value = cfg.pagomovil_id;
    if (cfg.transfer_bank && document.getElementById("cfg-tr-bank")) document.getElementById("cfg-tr-bank").value = cfg.transfer_bank;
    if (cfg.transfer_holder && document.getElementById("cfg-tr-holder")) document.getElementById("cfg-tr-holder").value = cfg.transfer_holder;
    if (cfg.transfer_account && document.getElementById("cfg-tr-account")) document.getElementById("cfg-tr-account").value = cfg.transfer_account;
    if (document.getElementById("cfg-apply-iva")) {
      document.getElementById("cfg-apply-iva").checked = (cfg.apply_iva === "1");
    }
    if (cfg.iva_rate && document.getElementById("cfg-iva-rate")) {
      document.getElementById("cfg-iva-rate").value = cfg.iva_rate;
    }
  } catch (err) {
    console.error("Error cargando configuración:", err);
  }
}

function updateMaintUI(isMaint) {
  const label = document.getElementById("maint-label");
  const btn = document.getElementById("btn-toggle-maint");
  const alertBar = document.getElementById("maint-alert-bar");

  if (isMaint) {
    if (label) label.innerText = "Mantenimiento: ON";
    if (btn) btn.className = "btn btn-danger";
    if (alertBar) alertBar.style.display = "flex";
  } else {
    if (label) label.innerText = "Mantenimiento: OFF";
    if (btn) btn.className = "btn btn-warning";
    if (alertBar) alertBar.style.display = "none";
  }
}

async function toggleMaintenance() {
  try {
    const res = await fetch("/api/config/toggle-maintenance", { method: "POST" });
    const data = await res.json();
    updateMaintUI(data.maintenance_mode === "1");
    notifySuccess(data.message);
  } catch (e) {
    notifyError("Error cambiando modo de mantenimiento");
  }
}

async function saveConfig(e) {
  e.preventDefault();
  const payload = {
    maintenance_message: document.getElementById("cfg-maint-msg")?.value,
    business_hours_start: document.getElementById("cfg-hour-start")?.value,
    business_hours_end: document.getElementById("cfg-hour-end")?.value,
    off_hours_message: document.getElementById("cfg-offhours-msg")?.value,
    pickup_address: document.getElementById("cfg-pickup-address")?.value,
    advisor_name: document.getElementById("cfg-advisor-name")?.value,
    advisor_phone: document.getElementById("cfg-advisor-phone")?.value,
    pagomovil_bank: document.getElementById("cfg-pm-bank")?.value,
    pagomovil_phone: document.getElementById("cfg-pm-phone")?.value,
    pagomovil_id: document.getElementById("cfg-pm-id")?.value,
    transfer_bank: document.getElementById("cfg-tr-bank")?.value,
    transfer_holder: document.getElementById("cfg-tr-holder")?.value,
    transfer_account: document.getElementById("cfg-tr-account")?.value,
    apply_iva: document.getElementById("cfg-apply-iva") ? (document.getElementById("cfg-apply-iva").checked ? "1" : "0") : undefined,
    iva_rate: document.getElementById("cfg-iva-rate")?.value
  };

  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      notifySuccess("Guardado", "Parámetros actualizados con éxito.");
    }
  } catch (err) {
    notifyError("Error al guardar ajustes");
  }
}

function exportData(format) {
  window.open(`/api/export/${format}`, '_blank');
}

// ----------------- CIERRE DE CAJA & REPORTE DIARIO -----------------
let currentDailyReportData = null;

function switchToReportTab() {
  const reportTabBtn = document.querySelector('.nav-item[data-tab="report"]');
  if (reportTabBtn) {
    reportTabBtn.click();
  }
}

function getTodayISODate() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function setReportToday() {
  const dateInput = document.getElementById("report-date-input");
  if (dateInput) {
    dateInput.value = getTodayISODate();
    loadDailyReport(dateInput.value);
  }
}

function onReportDateChange() {
  const dateInput = document.getElementById("report-date-input");
  if (dateInput && dateInput.value) {
    loadDailyReport(dateInput.value);
  }
}

async function loadDailyReport(targetDate = null) {
  const dateInput = document.getElementById("report-date-input");
  if (!targetDate) {
    if (dateInput && dateInput.value) {
      targetDate = dateInput.value;
    } else {
      targetDate = getTodayISODate();
      if (dateInput) dateInput.value = targetDate;
    }
  } else if (dateInput && !dateInput.value) {
    dateInput.value = targetDate;
  }

  try {
    const res = await fetch(`/api/reports/daily?date=${targetDate}`);
    if (!res.ok) {
      throw new Error(`Error en servidor: ${res.status}`);
    }
    const data = await res.json();
    currentDailyReportData = data;
    renderDailyReport(data);
  } catch (err) {
    console.error("Error al cargar reporte diario:", err);
    notifyError("Error cargando reporte diario", err.message);
  }
}

function renderDailyReport(rep) {
  if (!rep) return;

  // Formateador numérico en español venezolano
  const fmtUsd = (num) => `$${parseFloat(num || 0).toLocaleString('es-VE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} REF`;
  const fmtVes = (num) => `Bs. ${parseFloat(num || 0).toLocaleString('es-VE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  // 1. KPIs Principales
  const kpiUsd = document.getElementById("rep-kpi-usd");
  const kpiVes = document.getElementById("rep-kpi-ves");
  const kpiOrders = document.getElementById("rep-kpi-orders");
  const kpiGarments = document.getElementById("rep-kpi-garments");
  const metaBcv = document.getElementById("rep-meta-bcv");

  if (kpiUsd) kpiUsd.innerText = fmtUsd(rep.total_usd);
  if (kpiVes) kpiVes.innerText = fmtVes(rep.total_ves);
  if (kpiOrders) kpiOrders.innerText = `${rep.total_orders || 0} pedidos`;
  if (kpiGarments) kpiGarments.innerText = `${rep.total_garments_sold || 0} uds`;
  if (metaBcv) metaBcv.innerText = `Bs. ${parseFloat(rep.bcv_rate || 0).toLocaleString('es-VE', { minimumFractionDigits: 2, maximumFractionDigits: 4 })} / $`;

  // 2. Chips de Formas de Pago
  const payContainer = document.getElementById("rep-meta-payments");
  if (payContainer) {
    const pms = rep.payment_methods_summary || {};
    const pmEntries = Object.entries(pms);
    if (pmEntries.length === 0) {
      payContainer.innerHTML = '<span class="payment-chip text-muted">Sin cobros en la fecha seleccionada</span>';
    } else {
      payContainer.innerHTML = pmEntries.map(([method, amount]) => {
        const vesAmt = amount * (rep.bcv_rate || 1);
        let icon = "bi-cash-coin";
        if (method.includes("PAGO MÓVIL") || method.includes("MOVIL")) icon = "bi-phone";
        else if (method.includes("TRANS")) icon = "bi-bank";
        else if (method.includes("EFECT") || method.includes("DIVISA")) icon = "bi-cash-stack";
        else if (method.includes("PUNTO")) icon = "bi-credit-card";

        return `
          <div class="payment-chip">
            <i class="bi ${icon}"></i>
            <strong>${method}:</strong>
            <span>${fmtUsd(amount)} <small>(${fmtVes(vesAmt)})</small></span>
          </div>
        `;
      }).join("");
    }
  }

  // 3. SECCIÓN 1: Desglose de Uniformes y Prendas
  const uniformsTbody = document.getElementById("rep-uniforms-tbody");
  const uniformsTfoot = document.getElementById("rep-uniforms-tfoot");
  const uniformsBadge = document.getElementById("rep-uniforms-count-badge");
  const uniforms = rep.uniforms_summary || [];

  if (uniformsBadge) uniformsBadge.innerText = `${uniforms.length} productos`;

  if (uniformsTbody) {
    if (uniforms.length === 0) {
      uniformsTbody.innerHTML = `
        <tr>
          <td colspan="6" class="text-center text-muted" style="padding: 35px;">
            <i class="bi bi-inbox" style="font-size: 2rem; display: block; margin-bottom: 8px; color: #94a3b8;"></i>
            No se registraron ventas de uniformes o artículos para el día ${rep.date_dmy || rep.date}.
          </td>
        </tr>
      `;
      if (uniformsTfoot) uniformsTfoot.style.display = "none";
    } else {
      uniformsTbody.innerHTML = uniforms.map(u => {
        let catBadgeClass = "badge-category cat-uniform";
        if (u.category === "CALZADO") catBadgeClass = "badge-category cat-calzado";
        else if (u.category === "ACCESORIO") catBadgeClass = "badge-category cat-accesorio";

        // Desglose de tallas
        const sizesHtml = (u.sizes_detail && u.sizes_detail.length > 0)
          ? u.sizes_detail.map(sz => `<span class="size-pill">${sz}</span>`).join(" ")
          : '<span class="size-pill">Talla Estándar</span>';

        return `
          <tr>
            <td>
              <strong style="color: #0f172a; font-size: 0.95rem;">${u.name}</strong>
            </td>
            <td>
              <span class="${catBadgeClass}">${u.category}</span>
            </td>
            <td>
              <div class="sizes-pill-list">${sizesHtml}</div>
            </td>
            <td style="text-align: center;">
              <span class="qty-badge">${u.total_qty}</span>
            </td>
            <td style="text-align: right; font-weight: 700; color: #166534;">
              ${fmtUsd(u.total_usd)}
            </td>
            <td style="text-align: right; font-weight: 700; color: #0369a1;">
              ${fmtVes(u.total_ves)}
            </td>
          </tr>
        `;
      }).join("");

      if (uniformsTfoot) {
        uniformsTfoot.style.display = "table-footer-group";
        const tfootQty = document.getElementById("rep-tfoot-total-qty");
        const tfootUsd = document.getElementById("rep-tfoot-total-usd");
        const tfootVes = document.getElementById("rep-tfoot-total-ves");
        if (tfootQty) tfootQty.innerText = `${rep.total_garments_sold} uds`;
        if (tfootUsd) tfootUsd.innerText = fmtUsd(rep.total_usd);
        if (tfootVes) tfootVes.innerText = fmtVes(rep.total_ves);
      }
    }
  }

  // 4. SECCIÓN 2: Registro Detallado de Transacciones ("todo así especificadito")
  const ordersTbody = document.getElementById("rep-orders-tbody");
  const ordersBadge = document.getElementById("rep-orders-count-badge");
  const detailedOrders = rep.detailed_orders || [];

  if (ordersBadge) ordersBadge.innerText = `${detailedOrders.length} pedidos`;

  if (ordersTbody) {
    if (detailedOrders.length === 0) {
      ordersTbody.innerHTML = `
        <tr>
          <td colspan="8" class="text-center text-muted" style="padding: 35px;">
            <i class="bi bi-receipt" style="font-size: 2rem; display: block; margin-bottom: 8px; color: #94a3b8;"></i>
            No hay órdenes registradas para la fecha seleccionada.
          </td>
        </tr>
      `;
    } else {
      ordersTbody.innerHTML = detailedOrders.map(o => {
        // Items desglosados en viñetas limpias
        let itemsHtml = "";
        if (o.items && o.items.length > 0) {
          itemsHtml = o.items.map(it => `
            <div class="item-detail-row">
              <span class="item-qty-tag">${it.qty}x</span>
              <strong>${it.name}</strong>
              <span class="item-size-tag">(${it.size})</span>
              <span class="item-subtotal-tag">@ ${fmtUsd(it.unit_price)}</span>
            </div>
          `).join("");
        } else {
          itemsHtml = `<span>${o.items_summary || "Artículos no especificados"}</span>`;
        }

        const timeStr = o.created_time ? `<span class="time-tag"><i class="bi bi-clock"></i> ${o.created_time}</span>` : '-';
        const bankInfo = (o.receipt_bank && o.receipt_bank !== 'N/A') ? o.receipt_bank : (o.payment_method || 'EFECTIVO');
        const refInfo = (o.receipt_ref && o.receipt_ref !== 'N/A') ? `<div class="ref-tag">Ref: ${o.receipt_ref}</div>` : '';

        return `
          <tr>
            <td>
              <span class="ticket-tag" onclick="copyText('${o.ticket_code}')" title="Clic para copiar ticket">
                ${o.ticket_code} <i class="bi bi-copy"></i>
              </span>
            </td>
            <td>${timeStr}</td>
            <td>
              <strong>${o.client_name}</strong>
              <div class="cedula-sub"><i class="bi bi-person-vcard"></i> ${o.cedula}</div>
            </td>
            <td>
              <a href="https://wa.me/${o.phone.replace(/[^0-9]/g, '')}" target="_blank" class="phone-link">
                <i class="bi bi-whatsapp"></i> ${o.phone}
              </a>
            </td>
            <td>
              <div class="order-items-breakdown">${itemsHtml}</div>
            </td>
            <td>
              <div class="payment-info-box">
                <strong>${bankInfo}</strong>
                ${refInfo}
              </div>
            </td>
            <td style="text-align: right; font-weight: 700; color: #166534;">
              ${fmtUsd(o.amount_usd)}
            </td>
            <td style="text-align: right; font-weight: 700; color: #0369a1;">
              ${fmtVes(o.amount_ves)}
            </td>
          </tr>
        `;
      }).join("");
    }
  }

  // 5. SECCIÓN 3: Formato WhatsApp
  const waPreview = document.getElementById("rep-whatsapp-preview");
  if (waPreview) {
    waPreview.innerText = rep.whatsapp_text || "Sin texto generado";
  }
}

async function copyDailyReportWhatsApp() {
  if (!currentDailyReportData || !currentDailyReportData.whatsapp_text) {
    notifyError("Sin datos", "No hay un reporte cargado actualmente para copiar.");
    return;
  }
  try {
    await navigator.clipboard.writeText(currentDailyReportData.whatsapp_text);
    notifySuccess("¡Copiado con Éxito!", "El resumen de cierre diario de SIS-COMER se ha copiado al portapapeles. Puede pegarlo directamente en WhatsApp.");
  } catch (err) {
    // Respaldo manual
    const waEl = document.getElementById("rep-whatsapp-preview");
    if (waEl) {
      const range = document.createRange();
      range.selectNodeContents(waEl);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      try {
        document.execCommand('copy');
        notifySuccess("¡Copiado!", "Texto copiado al portapapeles.");
      } catch (e2) {
        notifyError("Error al copiar", "Por favor seleccione el texto manualmente y cópielo.");
      }
    }
  }
}

function exportDailyExcel() {
  const dateInput = document.getElementById("report-date-input");
  const targetDate = (dateInput && dateInput.value) ? dateInput.value : getTodayISODate();
  window.open(`/api/export/daily-report-excel?date=${targetDate}`, '_blank');
}

function printDailyReport() {
  window.print();
}

function copyText(str) {
  if (!str) return;
  navigator.clipboard.writeText(str).then(() => {
    if (window.Swal) {
      Swal.fire({
        toast: true,
        position: 'top-end',
        icon: 'success',
        title: `Copiado: ${str}`,
        showConfirmButton: false,
        timer: 1800
      });
    }
  });
}

// ----------------- BORRÓN Y CUENTA NUEVA (DATOS OPERATIVOS) -----------------
async function confirmResetOperationalData() {
  if (!window.Swal) {
    if (!confirm("⚠️ ATENCIÓN: Esta acción es irreversible. Se eliminarán pedidos, movimientos Kardex y clientes de prueba. El Catálogo Militar quedará intacto. ¿Continuar?")) return;
    const typed = prompt("Escriba 'REINICIAR' para confirmar:");
    if (typed !== "REINICIAR") {
      alert("Operación cancelada. No coincidió la palabra.");
      return;
    }
    executeResetOperationalData();
    return;
  }

  // Paso 1: Advertencia inicial
  const step1 = await Swal.fire({
    title: '⚠️ ¿REINICIAR DATOS DE PRUEBA?',
    html: `
      <div style="text-align: left; font-size: 0.9rem; color: #334155;">
        <p><strong>Esta acción es irreversible y eliminará:</strong></p>
        <ul style="padding-left: 20px; color: #dc2626; margin-bottom: 12px;">
          <li>Todos los pedidos y citas registrados (Orders).</li>
          <li>Todo el historial de auditoría de estados.</li>
          <li>Todo el historial de movimientos de inventario Kardex.</li>
          <li>Todos los clientes de prueba registrados.</li>
          <li>La lista de espera de productos.</li>
        </ul>
        <p style="color: #166534; font-weight: 700;">
          <i class="bi bi-shield-check"></i> El Catálogo Militar (Productos y Precios), Tasas BCV y Configuraciones se conservarán 100% INTACTOS.
        </p>
      </div>
    `,
    icon: 'warning',
    showCancelButton: true,
    confirmButtonColor: '#dc2626',
    cancelButtonColor: '#64748b',
    confirmButtonText: 'Sí, continuar al paso final',
    cancelButtonText: 'Cancelar',
    reverseButtons: true,
    customClass: { popup: 'swal2-custom-popup' }
  });

  if (!step1.isConfirmed) return;

  // Paso 2: Exigir escribir "REINICIAR"
  const step2 = await Swal.fire({
    title: 'Confirmación de Seguridad',
    text: 'Escriba la palabra REINICIAR en mayúsculas para proceder con el borrón y cuenta nueva:',
    input: 'text',
    inputPlaceholder: 'REINICIAR',
    icon: 'question',
    showCancelButton: true,
    confirmButtonColor: '#dc2626',
    cancelButtonColor: '#64748b',
    confirmButtonText: 'Confirmar y Reiniciar',
    cancelButtonText: 'Cancelar',
    reverseButtons: true,
    customClass: { popup: 'swal2-custom-popup' },
    inputValidator: (val) => {
      if (!val || val.trim() !== "REINICIAR") {
        return 'Debe escribir exactamente la palabra "REINICIAR"';
      }
    }
  });

  if (step2.isConfirmed) {
    executeResetOperationalData();
  }
}

async function executeResetOperationalData() {
  try {
    const res = await fetch("/api/admin/reset-operational-data", { method: "POST" });
    const data = await res.json();
    if (res.ok && data.status === "success") {
      notifySuccess(
        "Borrón y Cuenta Nueva Completado",
        `Se eliminaron ${data.deleted_orders} pedidos, ${data.deleted_kardex_movements} movimientos Kardex y ${data.deleted_clients} clientes. El Catálogo Militar se mantuvo 100% intacto.`
      );
      // Recargar todas las vistas y métricas
      loadOrders();
      loadInventoryKardex();
      loadWaitlist();
      loadFinancialMetrics();
      loadDailyReport();
      loadLowStockAlerts();
    } else {
      notifyError("Error al reiniciar datos operativos", data.message || "Error desconocido.");
    }
  } catch (err) {
    notifyError("Error de conexión al reiniciar", err.message);
  }
}

