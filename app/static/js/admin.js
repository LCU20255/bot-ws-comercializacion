// Estado global de la aplicación SIS-COMER
let orders = [];
let products = [];
let currentSelectedOrder = null;
let topProductsChartInstance = null;
let orderStatusChartInstance = null;

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  loadBCVRate();
  loadOrders();
  loadProducts();
  loadConfig();
  loadLowStockAlerts();
  loadFinancialMetrics();
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
        inventory: "Inventario & Kardex Militar",
        metrics: "Análisis Comercial & Rendimiento de Ventas",
        products: "Catálogo de Suministros & Textiles Militares",
        connection: "Vincular WhatsApp con Baileys (QR)",
        settings: "Horarios, Mantenimiento y Parámetros"
      };
      const subMap = {
        orders: "Control de solicitudes militares, pagos previos OCR y entrega presencial.",
        inventory: "Auditoría de confección, entradas de taller y salidas por ventas en SIS-COMER.",
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
      if (target === "inventory") {
        loadInventoryKardex();
        loadLowStockAlerts();
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
          <div style="font-weight: 600; color: #1e293b;"><i class="bi bi-calendar3 text-muted"></i> ${item.pickup_date}</div>
          <small class="text-muted"><i class="bi bi-clock"></i> ${item.pickup_time}</small>
        </td>
        <td>
          <span class="status-badge ${badgeClass}">${status}</span>
        </td>
        <td onclick="event.stopPropagation()">
          <div style="display: flex; gap: 6px; align-items: center;">
            <select style="padding: 4px 6px; font-size: 0.72rem;" onchange="changeOrderStatus(${item.id}, this.value)">
              <option value="PENDIENTE POR ATENCIÓN" ${status === 'PENDIENTE POR ATENCIÓN' ? 'selected' : ''}>PENDIENTE POR ATENCIÓN</option>
              <option value="CONFIRMADA" ${status === 'CONFIRMADA' ? 'selected' : ''}>CONFIRMADA</option>
              <option value="POR RETIRAR" ${status === 'POR RETIRAR' ? 'selected' : ''}>POR RETIRAR</option>
              <option value="RETIRADA" ${status === 'RETIRADA' ? 'selected' : ''}>RETIRADA</option>
              <option value="EN ESPERA POR MANTENIMIENTO" ${status === 'EN ESPERA POR MANTENIMIENTO' ? 'selected' : ''}>EN ESPERA POR MANTENIMIENTO</option>
              <option value="CANCELADA" ${status === 'CANCELADA' ? 'selected' : ''}>CANCELADA</option>
            </select>
            <button class="btn btn-icon btn-sm" onclick="openOrderDetail(${item.id})" title="Ver Detalles"><i class="bi bi-eye"></i></button>
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
        <small class="text-muted" style="display: block; font-weight: 600;">ESTADO DE ATENCIÓN</small>
        <span class="status-badge confirmada">${order.status}</span>
      </div>
    </div>

    <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px;">
      <h4 style="font-size: 0.9rem; color: #0f172a; margin-bottom: 8px;"><i class="bi bi-box-seam"></i> Artículos Seleccionados:</h4>
      ${itemsHtml}
    </div>

    <!-- Desglose Financiero -->
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px;">
      <div>
        <small style="color: #166534; font-weight: 700; display: block;">TOTAL EN DIVISAS</small>
        <strong style="font-size: 1.25rem; color: #166534;">$${amountUsd.toFixed(2)} REF</strong>
      </div>
      <div>
        <small style="color: #166534; font-weight: 700; display: block;">EQUIVALENTE EN BOLÍVARES</small>
        <strong style="font-size: 1.25rem; color: #166534;">Bs. ${amountVes.toLocaleString('es-VE', {minimumFractionDigits: 2})}</strong>
      </div>
    </div>

    <!-- Panel de Auditoría Comprobante OCR -->
    <div class="ocr-receipt-box">
      <h4><i class="bi bi-receipt-cutoff"></i> Verificación y Comprobante Bancario (OCR)</h4>
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
        <strong>${order.receipt_date || '-'}</strong>
      </div>
      <div class="ocr-field-row">
        <span>Tasa BCV Aplicada en Pago:</span>
        <strong>Bs. ${order.bcv_rate_applied ? order.bcv_rate_applied.toFixed(4) : '-'} / $</strong>
      </div>
      ${order.ocr_raw_text ? `
        <div style="margin-top: 8px; font-size: 0.78rem; background: #ffffff; padding: 6px 10px; border-radius: 4px; border: 1px solid #e2e8f0; color: #64748b; font-family: monospace; white-space: pre-wrap; max-height: 80px; overflow-y: auto;">
          <strong>Texto extraído por OCR:</strong><br>${order.ocr_raw_text}
        </div>
      ` : ''}
    </div>

    <!-- Cita de Retiro -->
    <div style="margin-top: 14px; font-size: 0.9rem; color: #334155; display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #e2e8f0; padding-top: 12px;">
      <div>
        <i class="bi bi-calendar-event"></i> <strong>Retiro Programado:</strong> ${order.pickup_date} a las ${order.pickup_time}
      </div>
      <div style="display: flex; gap: 8px;">
        <button class="btn btn-sm btn-primary" onclick="openEditOrderModalDirect(${order.id})"><i class="bi bi-pencil"></i> Editar</button>
        <button class="btn btn-sm btn-secondary" onclick="closeOrderModal()">Cerrar</button>
      </div>
    </div>
  `;

  document.getElementById("order-detail-modal").classList.add("show");
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
      const badge = isIn ? '<span class="kardex-badge-in">ENTRADA TALLER</span>' : '<span class="kardex-badge-out">SALIDA VENTA</span>';
      return `
        <tr>
          <td><code style="color: #64748b;">#${m.id}</code></td>
          <td>${m.created_at || '-'}</td>
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
    } else {
      notifyError("Error", data.detail || "No se pudo ingresar el lote.");
    }
  } catch (err) {
    notifyError("Error", "Error de red ingresando lote.");
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
  document.getElementById("edit_pickup_date").value = order.pickup_date;
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

function openProductModal() {
  document.getElementById("product-modal-title").innerText = "Agregar Producto Militar";
  document.getElementById("product-form").reset();
  document.getElementById("prod_id").value = "";
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
  document.getElementById("prod_stock").value = p.stock;
  document.getElementById("prod_image").value = p.image_url || "";
  document.getElementById("prod_desc").value = p.description || "";
  document.getElementById("prod_keywords").value = p.keywords || "";

  document.getElementById("product-modal").classList.add("show");
}

async function saveProduct(e) {
  e.preventDefault();
  const id = document.getElementById("prod_id").value;
  const payload = {
    name: document.getElementById("prod_name").value.toUpperCase(),
    price: parseFloat(document.getElementById("prod_price").value),
    price_display: `$${parseFloat(document.getElementById("prod_price").value).toFixed(2)} Ref`,
    category: document.getElementById("prod_category").value.toUpperCase(),
    requires_size: parseInt(document.getElementById("prod_requires_size").value),
    stock: parseInt(document.getElementById("prod_stock").value),
    image_url: document.getElementById("prod_image").value || "/static/images/placeholder.png",
    description: document.getElementById("prod_desc").value,
    keywords: document.getElementById("prod_keywords").value,
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

    if (cfg.business_hours_start) document.getElementById("cfg-hour-start").value = cfg.business_hours_start;
    if (cfg.business_hours_end) document.getElementById("cfg-hour-end").value = cfg.business_hours_end;
    if (cfg.off_hours_message) document.getElementById("cfg-offhours-msg").value = cfg.off_hours_message;
    if (cfg.pickup_address) document.getElementById("cfg-pickup-address").value = cfg.pickup_address;
    if (cfg.advisor_name) document.getElementById("cfg-advisor-name").value = cfg.advisor_name;
    if (cfg.advisor_phone) document.getElementById("cfg-advisor-phone").value = cfg.advisor_phone;
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
    business_hours_start: document.getElementById("cfg-hour-start")?.value,
    business_hours_end: document.getElementById("cfg-hour-end")?.value,
    off_hours_message: document.getElementById("cfg-offhours-msg")?.value,
    pickup_address: document.getElementById("cfg-pickup-address")?.value,
    advisor_name: document.getElementById("cfg-advisor-name")?.value,
    advisor_phone: document.getElementById("cfg-advisor-phone")?.value
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
