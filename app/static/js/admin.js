// Estado global de la aplicación
let orders = [];
let products = [];
let currentSelectedOrder = null;
let simulatorPhone = "+584128887766";

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  loadOrders();
  loadProducts();
  loadConfig();
  const container = document.getElementById("wa-messages-container");
  if (container) {
    initSimulatorWelcome();
  }
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
        products: "Catálogo de Suministros & Textiles Militares",
        connection: "Vincular WhatsApp con Baileys (QR)",
        settings: "Horarios, Mantenimiento y Parámetros"
      };
      const subMap = {
        orders: "Control de solicitudes militares, retiro presencial y pagos.",
        products: "Gestiona los artículos de intendencia, precios reglamentarios y fotos reales.",
        connection: "Escanea el código QR de Baileys para activar la atención automática.",
        settings: "Configura el horario laboral de 8:00 AM a 5:00 PM y modo auditoría."
      };
      const titleEl = document.getElementById("page-title");
      const subEl = document.getElementById("page-subtitle");
      if (titleEl) titleEl.innerText = titleMap[target] || "Panel de Intendencia";
      if (subEl) subEl.innerText = subMap[target] || "";
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
  setEl("kpi-confirmed", data.filter(o => ["CONFIRMADA", "POR RETIRAR"].includes((o.status || "").toUpperCase())).length);
  setEl("kpi-completed", data.filter(o => (o.status || "").toUpperCase() === "RETIRADA").length);
  setEl("kpi-offhours", data.filter(o => o.is_off_hours === 1 || o.status === "EN ESPERA POR MANTENIMIENTO").length);
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

    const cleanPhone = (item.phone || "").replace(/\D/g, "");
    const waLink = `https://wa.me/${cleanPhone}`;

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
          <a href="${waLink}" target="_blank" style="color: #0284c7; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; font-weight: 600;">
            <i class="bi bi-whatsapp" style="color: #16a34a;"></i> ${item.phone}
          </a>
        </td>
        <td style="max-width: 260px; font-weight: 500; color: #334155;">
          ${item.items_summary}
        </td>
        <td style="color: #166534; font-weight: 800; font-size: 0.95rem;">
          $${parseFloat(item.total_amount || 0).toFixed(2)} Ref
        </td>
        <td style="font-size: 0.75rem; color: #475569; font-weight: 600;">
          ${item.payment_method || 'EFECTIVO'}
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
            <button class="btn btn-icon btn-sm text-danger" onclick="deleteOrder(${item.id})" title="Eliminar Registro"><i class="bi bi-trash3"></i></button>
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

  document.getElementById("modal-ticket-title").innerText = `Ticket: ${order.ticket_code}`;
  document.getElementById("modal-order-timestamp").innerText = `Registrado el: ${order.created_at || 'Reciente'}`;
  document.getElementById("det-client-name").innerText = order.client_name;
  document.getElementById("det-cedula").innerText = order.cedula;
  document.getElementById("det-phone").innerText = order.phone;
  document.getElementById("det-total-items").innerText = order.total_items || 1;
  document.getElementById("det-payment-method").innerText = order.payment_method || 'EFECTIVO';
  document.getElementById("det-pickup-datetime").innerHTML = `<i class="bi bi-calendar3"></i> ${order.pickup_date} &nbsp;|&nbsp; <i class="bi bi-clock"></i> ${order.pickup_time}`;
  document.getElementById("det-total-amount").innerText = `$${parseFloat(order.total_amount || 0).toFixed(2)} Ref`;

  // Desglose de ítems con TALLA destacada
  const itemsContainer = document.getElementById("det-items-list");
  let itemsList = [];
  try {
    itemsList = typeof order.items_detail === "string" ? JSON.parse(order.items_detail) : (order.items_detail || []);
  } catch (e) {
    itemsList = [];
  }

  if (itemsList && itemsList.length > 0) {
    itemsContainer.innerHTML = itemsList.map(it => `
      <div class="breakdown-row" style="display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px dashed #e2e8f0;">
        <div>
          <strong style="color: #0f172a;">${it.qty}x</strong> <span style="font-weight: 600;">${it.name}</span>
          ${it.size ? `<span style="background: #e0f2fe; color: #0369a1; padding: 3px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 700; margin-left: 6px;"><i class="bi bi-rulers"></i> TALLA: ${it.size}</span>` : ''}
        </div>
        <div style="text-align: right;">
          <span style="color: #64748b; font-size: 0.82rem; margin-right: 8px;">${it.unit_price ? '$' + parseFloat(it.unit_price).toFixed(2) + ' c/u' : ''}</span>
          <strong style="color: #166534; font-size: 0.95rem;">$${parseFloat(it.subtotal || 0).toFixed(2)} Ref</strong>
        </div>
      </div>
    `).join("");
  } else {
    itemsContainer.innerHTML = `
      <div class="breakdown-row" style="display: flex; justify-content: space-between; align-items: center; padding: 10px 0;">
        <span style="font-weight: 600;">${order.items_summary}</span>
        <strong style="color: #166534; font-size: 0.95rem;">$${parseFloat(order.total_amount || 0).toFixed(2)} Ref</strong>
      </div>
    `;
  }

  // Tag fuera de horario / mantenimiento
  const offhoursTag = document.getElementById("det-offhours-tag");
  if (offhoursTag) {
    if (order.is_off_hours || order.status === "EN ESPERA POR MANTENIMIENTO") {
      offhoursTag.style.display = "block";
    } else {
      offhoursTag.style.display = "none";
    }
  }

  // Botón Retomar Atención si estaba en espera por mantenimiento
  const btnRetake = document.getElementById("btn-retake-order");
  if (btnRetake) {
    if (order.status === "EN ESPERA POR MANTENIMIENTO") {
      btnRetake.style.display = "inline-flex";
    } else {
      btnRetake.style.display = "none";
    }
  }

  // Estado del botón recordatorio
  const btnRem = document.getElementById("btn-send-reminder");
  if (btnRem) {
    if (order.reminder_sent) {
      btnRem.innerHTML = `<i class="bi bi-check-circle"></i> Recordatorio ya enviado`;
      btnRem.classList.remove("btn-warning");
      btnRem.classList.add("btn-secondary");
    } else {
      btnRem.innerHTML = `<i class="bi bi-whatsapp"></i> Enviar Recordatorio al Cliente`;
      btnRem.classList.add("btn-warning");
      btnRem.classList.remove("btn-secondary");
    }
  }

  document.getElementById("order-detail-modal").classList.add("show");
}

function closeOrderDetailModal() {
  document.getElementById("order-detail-modal").classList.remove("show");
  currentSelectedOrder = null;
}

async function sendOrderReminder() {
  if (!currentSelectedOrder) return;
  try {
    const res = await fetch(`/api/orders/${currentSelectedOrder.id}/send-reminder`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      closeOrderDetailModal();
      notifySuccess("Recordatorio Procesado", `Mensaje preparado para el cliente:\n\n${data.sent_text}`);
      loadOrders();
    } else {
      notifyError("Error", "No se pudo procesar el recordatorio.");
    }
  } catch (e) {
    notifyError("Error", "Error enviando recordatorio");
  }
}

async function retakeCurrentOrder() {
  if (!currentSelectedOrder) return;
  try {
    const res = await fetch(`/api/orders/${currentSelectedOrder.id}/retake`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      closeOrderDetailModal();
      notifySuccess("¡Atención Retomada!", "El pedido pasó a estado 'PENDIENTE POR ATENCIÓN' exitosamente.");
      loadOrders();
    } else {
      notifyError("Error", data.detail || "No se pudo retomar la atención.");
    }
  } catch (e) {
    notifyError("Error", "Error retomando atención");
  }
}

async function deleteCurrentOrder() {
  if (!currentSelectedOrder) return;
  const id = currentSelectedOrder.id;
  const ticket = currentSelectedOrder.ticket_code;
  const client = currentSelectedOrder.client_name;

  const confirmed = await confirmAction(
    `¿Eliminar Pedido ${ticket}?`,
    `¿Seguro que deseas eliminar permanentemente el registro de ${client}? Esta acción no se puede deshacer.`,
    "Sí, eliminar definitivamente",
    true
  );
  if (!confirmed) return;

  try {
    const res = await fetch(`/api/orders/${id}`, { method: "DELETE" });
    if (res.ok) {
      closeOrderDetailModal();
      notifySuccess("¡Eliminado!", `El pedido ${ticket} fue eliminado permanentemente.`);
      loadOrders();
    } else {
      const err = await res.json().catch(() => ({}));
      notifyError("Error", err.detail || "No se pudo eliminar el pedido del servidor");
    }
  } catch (e) {
    notifyError("Error", "Error al eliminar pedido");
  }
}

async function deleteOrder(id) {
  const confirmed = await confirmAction(
    "¿Eliminar Pedido?",
    "¿Seguro que deseas eliminar permanentemente este registro de la base de datos?",
    "Sí, eliminar",
    true
  );
  if (!confirmed) return;

  try {
    const res = await fetch(`/api/orders/${id}`, { method: "DELETE" });
    if (res.ok) {
      notifySuccess("¡Eliminado!", "El pedido ha sido eliminado permanentemente de la base de datos.");
      loadOrders();
    } else {
      const err = await res.json().catch(() => ({}));
      notifyError("Error", err.detail || "No se pudo eliminar el pedido");
    }
  } catch (e) {
    notifyError("Error", "Error al eliminar pedido");
  }
}

// ----------------- EDITAR PEDIDO -----------------
function openEditOrderModal() {
  if (!currentSelectedOrder) return;
  const o = currentSelectedOrder;
  document.getElementById("edit_order_id").value = o.id;
  document.getElementById("edit_client_name").value = o.client_name;
  document.getElementById("edit_cedula").value = o.cedula;
  document.getElementById("edit_phone").value = o.phone;
  document.getElementById("edit_items_summary").value = o.items_summary;
  document.getElementById("edit_total_amount").value = o.total_amount;
  document.getElementById("edit_payment_method").value = o.payment_method;
  document.getElementById("edit_pickup_date").value = o.pickup_date;
  document.getElementById("edit_pickup_time").value = o.pickup_time;
  document.getElementById("edit_status").value = o.status;

  closeOrderDetailModal();
  document.getElementById("order-edit-modal").classList.add("show");
}

function closeEditOrderModal() {
  document.getElementById("order-edit-modal").classList.remove("show");
}

async function saveEditedOrder(e) {
  e.preventDefault();
  const id = document.getElementById("edit_order_id").value;
  const payload = {
    client_name: document.getElementById("edit_client_name").value,
    cedula: document.getElementById("edit_cedula").value,
    phone: document.getElementById("edit_phone").value,
    items_summary: document.getElementById("edit_items_summary").value,
    total_amount: parseFloat(document.getElementById("edit_total_amount").value) || 0,
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
      loadOrders();
      notifySuccess("¡Actualizado!", "Pedido actualizado exitosamente.");
    } else {
      notifyError("Error", "No se pudo actualizar el pedido.");
    }
  } catch (err) {
    notifyError("Error", "Error al actualizar pedido");
  }
}

// ----------------- EXPORTACIONES -----------------
function exportData(type) {
  if (type === "excel") {
    window.location.href = "/api/export/excel";
  } else if (type === "csv") {
    window.location.href = "/api/export/csv";
  }
}

// ----------------- PRODUCTOS MILITARES -----------------
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

  if (!data || data.length === 0) {
    grid.innerHTML = `<div class="card" style="grid-column: 1/-1; padding: 40px; text-align: center;">No hay productos registrados en el catálogo militar.</div>`;
    return;
  }

  grid.innerHTML = data.map(p => `
    <div class="product-card" onclick="editProduct(${p.id})" style="cursor: pointer;">
      <div class="product-thumb">
        <img src="${p.image_url || '/static/images/placeholder.png'}" alt="${p.name}" onerror="this.src='/static/images/placeholder.png'">
        <span class="product-badge">${p.category || 'MILITAR'}</span>
      </div>
      <div class="product-info">
        <h4>${p.name}</h4>
        <div class="product-price">$${parseFloat(p.price || 0).toFixed(2)} Ref</div>
        <p class="product-desc">${p.description || 'Sin especificación técnica'}</p>
        <div style="margin: 6px 0;">
          ${p.requires_size ? '<span style="background: #166534; color: #fff; font-size: 0.72rem; padding: 2px 7px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px; font-weight: 600;"><i class="bi bi-rulers"></i> Requiere Talla (Ropa/Calzado)</span>' : '<span style="background: #f1f5f9; color: #64748b; font-size: 0.72rem; padding: 2px 7px; border-radius: 4px; display: inline-flex; align-items: center; gap: 4px;"><i class="bi bi-slash-circle"></i> Sin Talla (Parches/Barras)</span>'}
        </div>
        <div class="product-meta">
          <span><i class="bi bi-box-seam"></i> Stock: ${p.stock}</span>
          <div class="product-actions" onclick="event.stopPropagation()">
            <button class="btn btn-edit btn-sm" onclick="editProduct(${p.id})" title="Editar Producto">
              <i class="bi bi-pencil-square"></i> Editar
            </button>
            <button class="btn btn-danger btn-sm" onclick="deleteProduct(${p.id})" title="Eliminar Producto">
              <i class="bi bi-trash3"></i>
            </button>
          </div>
        </div>
      </div>
    </div>
  `).join("");
}

function openProductModal(isEdit = false) {
  document.getElementById("product-modal").classList.add("show");
  if (!isEdit) {
    document.getElementById("product-modal-title").innerText = "Agregar Producto Militar";
    document.getElementById("product-form").reset();
    document.getElementById("prod_id").value = "";
    document.getElementById("prod_requires_size").value = "0";
  }
}

function closeProductModal() {
  document.getElementById("product-modal").classList.remove("show");
}

function editProduct(id) {
  const p = products.find(x => x.id === id);
  if (!p) return;
  document.getElementById("product-modal-title").innerText = "Editar Producto Militar";
  document.getElementById("prod_id").value = p.id;
  document.getElementById("prod_name").value = p.name;
  document.getElementById("prod_price").value = p.price;
  document.getElementById("prod_category").value = p.category;
  document.getElementById("prod_requires_size").value = (p.requires_size !== undefined && p.requires_size !== null) ? p.requires_size : 0;
  document.getElementById("prod_image").value = p.image_url;
  document.getElementById("prod_desc").value = p.description;
  document.getElementById("prod_stock").value = p.stock;
  document.getElementById("prod_keywords").value = p.keywords || "";
  openProductModal(true);
}

async function saveProduct(e) {
  e.preventDefault();
  const id = document.getElementById("prod_id").value;
  const payload = {
    name: document.getElementById("prod_name").value.toUpperCase(),
    price: parseFloat(document.getElementById("prod_price").value) || 0.0,
    price_display: `$${parseFloat(document.getElementById("prod_price").value || 0).toFixed(2)} Ref`,
    category: document.getElementById("prod_category").value.toUpperCase(),
    requires_size: parseInt(document.getElementById("prod_requires_size").value) || 0,
    image_url: document.getElementById("prod_image").value || "/static/images/placeholder.png",
    description: document.getElementById("prod_desc").value,
    stock: parseInt(document.getElementById("prod_stock").value) || 0,
    keywords: document.getElementById("prod_keywords").value,
    is_active: 1,
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
      loadProducts();
      notifySuccess("¡Producto Guardado!", id ? "Producto militar actualizado con éxito." : "Nuevo producto registrado en el catálogo.");
    } else {
      notifyError("Error", "No se pudo guardar el producto.");
    }
  } catch (err) {
    notifyError("Error", "Error guardando producto");
  }
}

async function deleteProduct(id) {
  const confirmed = await confirmAction("¿Eliminar Producto?", "¿Seguro que deseas eliminar este producto del catálogo militar?", "Sí, eliminar", true);
  if (!confirmed) return;

  try {
    const res = await fetch(`/api/products/${id}`, { method: "DELETE" });
    if (res.ok) {
      notifySuccess("¡Eliminado!", "Producto eliminado del catálogo.");
      loadProducts();
    } else {
      notifyError("Error", "No se pudo eliminar el producto.");
    }
  } catch (err) {
    notifyError("Error", "Error al eliminar producto");
  }
}

async function uploadProductImage(input) {
  if (!input.files || input.files.length === 0) return;
  const file = input.files[0];
  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/upload-image", {
      method: "POST",
      body: formData
    });
    const data = await res.json();
    if (data.url) {
      document.getElementById("prod_image").value = data.url;
      notifySuccess("Imagen Subida", "La fotografía se ha cargado correctamente.");
    } else {
      notifyError("Error", "No se pudo procesar la imagen.");
    }
  } catch (err) {
    notifyError("Error", "Error al subir la imagen");
  }
}

// ----------------- CONFIGURACIÓN & MANTENIMIENTO -----------------
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    if (document.getElementById("set_business_start")) document.getElementById("set_business_start").value = cfg.business_hours_start || "08:00";
    if (document.getElementById("set_business_end")) document.getElementById("set_business_end").value = cfg.business_hours_end || "17:00";
    if (document.getElementById("set_off_hours_msg")) document.getElementById("set_off_hours_msg").value = cfg.off_hours_message || "";
    if (document.getElementById("set_maintenance_msg")) document.getElementById("set_maintenance_msg").value = cfg.maintenance_message || "";
    if (document.getElementById("set_advisor_name")) document.getElementById("set_advisor_name").value = cfg.advisor_name || "";
    if (document.getElementById("set_advisor_phone")) document.getElementById("set_advisor_phone").value = cfg.advisor_phone || "";
    if (document.getElementById("set_pickup_address")) document.getElementById("set_pickup_address").value = cfg.pickup_address || "";
    if (document.getElementById("set_pickup_hours")) document.getElementById("set_pickup_hours").value = cfg.pickup_hours || "";

    if (cfg.whatsapp_bot_number && document.getElementById("input-wa-number")) {
      document.getElementById("input-wa-number").value = cfg.whatsapp_bot_number;
    }

    const isMaint = cfg.maintenance_mode === "1";
    updateMaintenanceUI(isMaint);
  } catch (err) {
    console.error("Error al cargar configuraciones:", err);
  }
}

function updateMaintenanceUI(isMaint) {
  const label = document.getElementById("maint-label");
  const btn = document.getElementById("btn-toggle-maint");
  const banner = document.getElementById("maint-alert-bar");
  const statusTitle = document.getElementById("status-title");

  if (isMaint) {
    if (label) label.innerText = "Modo Mantenimiento: ON";
    if (btn) { btn.classList.add("btn-danger"); btn.classList.remove("btn-warning"); }
    if (banner) banner.style.display = "flex";
    if (statusTitle) { statusTitle.innerText = "AUDITORÍA / MANT."; statusTitle.style.color = "#fbbf24"; }
  } else {
    if (label) label.innerText = "Modo Mantenimiento: OFF";
    if (btn) { btn.classList.remove("btn-danger"); btn.classList.add("btn-warning"); }
    if (banner) banner.style.display = "none";
    if (statusTitle) { statusTitle.innerText = "BOT ACTIVO"; statusTitle.style.color = "#34d399"; }
  }
}

async function toggleMaintenance() {
  try {
    const res = await fetch("/api/config/toggle-maintenance", { method: "POST" });
    const data = await res.json();
    updateMaintenanceUI(data.maintenance_mode === "1");
    notifySuccess("Modo Mantenimiento", data.message);
  } catch (e) {
    notifyError("Error", "Error al cambiar modo mantenimiento");
  }
}

async function saveSettings(e) {
  e.preventDefault();
  const payload = {
    business_hours_start: document.getElementById("set_business_start").value,
    business_hours_end: document.getElementById("set_business_end").value,
    off_hours_message: document.getElementById("set_off_hours_msg").value,
    maintenance_message: document.getElementById("set_maintenance_msg").value,
    advisor_name: document.getElementById("set_advisor_name").value,
    advisor_phone: document.getElementById("set_advisor_phone").value,
    pickup_address: document.getElementById("set_pickup_address").value,
    pickup_hours: document.getElementById("set_pickup_hours").value
  };

  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      notifySuccess("¡Configuración Guardada!", "Parámetros y horarios actualizados exitosamente.");
      loadConfig();
    } else {
      notifyError("Error", "No se pudo guardar la configuración.");
    }
  } catch (err) {
    notifyError("Error", "Error al guardar configuraciones");
  }
}

// ----------------- BAILEYS QR STATUS -----------------
async function checkBaileysStatus() {
  try {
    const res = await fetch("/api/baileys/status");
    const data = await res.json();
    const qrImg = document.getElementById("qr-image");
    if (qrImg && data.has_qr && data.qr_url) {
      qrImg.src = data.qr_url + "&r=" + Date.now();
    }
  } catch (e) {}
}

setInterval(checkBaileysStatus, 3000);

function refreshQR() {
  checkBaileysStatus();
}

async function saveAndGenerateQR() {
  const input = document.getElementById("input-wa-number");
  const phone = input ? input.value.trim() : "";
  if (!phone) {
    notifyError("Campo Obligatorio", "Por favor introduce el número de WhatsApp asignado a la línea.");
    return;
  }

  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ whatsapp_bot_number: phone })
    });
    if (res.ok) {
      notifySuccess("Línea Asignada", `Número ${phone} guardado exitosamente. Generando código QR...`);
      refreshQR();
    } else {
      notifyError("Error", "No se pudo guardar el número de WhatsApp.");
    }
  } catch (e) {
    notifyError("Error", "Error al guardar el número de WhatsApp");
  }
}
