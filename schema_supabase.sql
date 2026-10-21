-- ==============================================================================
-- SISTEMA SIS-COMER (COMPLEJO INDUSTRIAL TIUNA - Gerencia de Comercialización)
-- ESTRUCTURA COMPLETA DE BASE DE DATOS PARA SUPABASE (POSTGRESQL)
-- ==============================================================================
-- Instrucciones de uso en Supabase:
-- 1. Inicia sesión en tu proyecto de Supabase (https://supabase.com/dashboard).
-- 2. Ve al menú lateral izquierdo y abre el "SQL Editor".
-- 3. Crea una "New query", pega todo el contenido de este archivo y presiona "Run".
-- 4. Las tablas, índices, llaves foráneas y datos iniciales quedarán listos.
-- ==============================================================================

-- 1. TABLA: products (Catálogo de Productos Militares y Textiles)
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE,
    description TEXT,
    price NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    price_display VARCHAR(100),
    category VARCHAR(100) DEFAULT 'TEXTIL MILITAR',
    image_url TEXT DEFAULT '/static/images/placeholder.png',
    stock INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    requires_size INTEGER DEFAULT 0,
    min_stock_alert INTEGER DEFAULT 20,
    available_sizes TEXT DEFAULT '',
    keywords TEXT DEFAULT '',
    technical_specs TEXT DEFAULT '',
    fabric TEXT DEFAULT '',
    buttons_closures TEXT DEFAULT '',
    durability TEXT DEFAULT '',
    thickness_weight TEXT DEFAULT '',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'America/Caracas'),
    updated_by VARCHAR(100) DEFAULT 'ADMIN'
);

-- 2. TABLA: clients (Registro Oficial de Clientes)
CREATE TABLE IF NOT EXISTS clients (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    cedula VARCHAR(50) UNIQUE NOT NULL,
    phone VARCHAR(50) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'America/Caracas')
);

-- 3. TABLA: orders (Pedidos / Citas Oficiales de Retiro y Facturación)
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    ticket_code VARCHAR(50) UNIQUE NOT NULL,
    client_name VARCHAR(255) NOT NULL,
    cedula VARCHAR(50) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    items_summary TEXT NOT NULL,
    items_detail JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_items INTEGER NOT NULL DEFAULT 1,
    total_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    amount_usd NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    amount_ves NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    bcv_rate_applied NUMERIC(12, 4) NOT NULL DEFAULT 0.00,
    bcv_rate_date VARCHAR(50),
    payment_method VARCHAR(100) NOT NULL,
    receipt_ref VARCHAR(100),
    receipt_bank VARCHAR(100),
    receipt_date VARCHAR(50),
    receipt_image_url TEXT,
    ocr_raw_text TEXT,
    manual_payment_data TEXT,
    ocr_data_json TEXT,
    iva_amount NUMERIC(12, 2) DEFAULT 0.00,
    pickup_date VARCHAR(50) NOT NULL,
    pickup_time VARCHAR(50) NOT NULL,
    status VARCHAR(100) DEFAULT 'PENDIENTE POR ATENCIÓN',
    is_off_hours INTEGER DEFAULT 0,
    reminder_sent INTEGER DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'America/Caracas'),
    synced_to_supabase INTEGER DEFAULT 1
);

-- 4. TABLA: order_status_history (Auditoría Histórica de Estados del Pedido)
CREATE TABLE IF NOT EXISTS order_status_history (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    ticket_code VARCHAR(50),
    previous_status VARCHAR(100),
    new_status VARCHAR(100) NOT NULL,
    changed_by VARCHAR(100) DEFAULT 'ADMIN',
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'America/Caracas')
);

-- 5. TABLA: inventory_movements (Kardex Histórico Militar de Entradas/Salidas/Ajustes)
CREATE TABLE IF NOT EXISTS inventory_movements (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    movement_type VARCHAR(50) NOT NULL,
    quantity INTEGER NOT NULL,
    previous_stock INTEGER NOT NULL,
    new_stock INTEGER NOT NULL,
    order_id INTEGER,
    client_name VARCHAR(255),
    created_by VARCHAR(100) DEFAULT 'SISTEMA',
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'America/Caracas')
);

-- 6. TABLA: bcv_rates (Histórico Oficial de Tasas BCV por Fecha)
CREATE TABLE IF NOT EXISTS bcv_rates (
    rate_date VARCHAR(50) PRIMARY KEY,
    rate NUMERIC(12, 4) NOT NULL,
    updated_at VARCHAR(50) NOT NULL
);

-- 7. TABLA: system_config (Configuración del Sistema: Cuentas Bancarias, Horarios, etc.)
CREATE TABLE IF NOT EXISTS system_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT
);

-- 8. TABLA: product_waitlist (Lista de Espera cuando un Producto no tiene Stock)
CREATE TABLE IF NOT EXISTS product_waitlist (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(50) NOT NULL,
    client_name VARCHAR(255),
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name VARCHAR(255) NOT NULL,
    status VARCHAR(50) DEFAULT 'PENDIENTE',
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'America/Caracas'),
    notified_at VARCHAR(50)
);

-- ==============================================================================
-- ÍNDICES DE RENDIMIENTO (OPTIMIZACIÓN DE CONSULTAS FRECUENTES)
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_products_slug ON products(slug);
CREATE INDEX IF NOT EXISTS idx_products_active_stock ON products(is_active, stock);
CREATE INDEX IF NOT EXISTS idx_clients_cedula ON clients(cedula);
CREATE INDEX IF NOT EXISTS idx_clients_phone ON clients(phone);
CREATE INDEX IF NOT EXISTS idx_orders_ticket ON orders(ticket_code);
CREATE INDEX IF NOT EXISTS idx_orders_cedula ON orders(cedula);
CREATE INDEX IF NOT EXISTS idx_orders_phone ON orders(phone);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_order_hist_order ON order_status_history(order_id);
CREATE INDEX IF NOT EXISTS idx_inventory_prod ON inventory_movements(product_id);
CREATE INDEX IF NOT EXISTS idx_waitlist_status_prod ON product_waitlist(status, product_id);
CREATE INDEX IF NOT EXISTS idx_waitlist_phone ON product_waitlist(phone);

-- ==============================================================================
-- DATOS INICIALES: CONFIGURACIÓN BANCARIA Y OPERATIVA POR DEFECTO
-- ==============================================================================
INSERT INTO system_config (key, value) VALUES
    ('pagomovil_bank', 'Banco Banesco (0134)'),
    ('pagomovil_phone', '0414-7778899'),
    ('pagomovil_id', 'J-998877665'),
    ('transfer_bank', 'Banco Mercantil'),
    ('transfer_account', '0105-0000-00-1111222233'),
    ('transfer_holder', 'INDUSTRIA MILITAR TIUNA CA'),
    ('business_hours_start', '08:00'),
    ('business_hours_end', '17:00')
ON CONFLICT (key) DO NOTHING;

-- ==============================================================================
-- DATOS INICIALES: PRODUCTOS DEL CATÁLOGO TIUNA CON FICHAS TÉCNICAS
-- ==============================================================================
INSERT INTO products (
    name, slug, description, price, price_display, category,
    image_url, stock, is_active, requires_size, min_stock_alert,
    available_sizes, keywords, fabric, buttons_closures, durability, thickness_weight, updated_by
) VALUES
(
    'CHAQUETA PATRIOTA TIUNA',
    'chaqueta-patriota-tiuna',
    'Chaqueta oficial de campaña con velcro y tela Ripstop antidesgarro reglamentaria FANB.',
    45.00,
    '$45.00 Ref',
    'TEXTIL MILITAR',
    '/static/images/placeholder.png',
    67,
    1,
    1,
    20,
    'S, M, L, XL, XXL',
    'chaqueta, patriota, camuflaje, uniforme, militar',
    'Ripstop Militar Antidesgarro de alta tenacidad (65% Poliéster / 35% Algodón peinado de fibra larga), ligamento tafetán con retícula antirrasgado.',
    '5 botones militares de pasta reforzada de 4 orificios ocultos bajo tapeta protectora anti-enganche + cierre frontal de alta tracción y paneles de velcro táctico mil-spec en pecho y mangas.',
    'Confección para operaciones tácticas y de campo de alto rendimiento. Alta resistencia a la fricción de campaña, abrasión, lavado industrial sin encogimiento (> 2.000 horas operativas de vida útil).',
    'Calibre pesado reglamentario de 240 g/m² (±5%), densidad térmica equilibrada y transpirabilidad de campaña.',
    'TALLER_CONFECCION'
),
(
    'BOTAS MILITARES CAMPAÑA',
    'botas-militares-campana',
    'Botas de cuero táctico flor hidrofugado y lona Cordura de alta resistencia con suela vulcanizada.',
    60.00,
    '$60.00 Ref',
    'CALZADO MILITAR',
    '/static/images/placeholder.png',
    50,
    1,
    1,
    20,
    '38, 39, 40, 41, 42, 43, 44',
    'botas, calzado, militar, campana, combate',
    'Cuero vacuno genuino flor hidrofugado calibre 2.0 - 2.2 mm de alta tenacidad, combinado con paneles laterales en lona balística Cordura de nylon 1000D impermeable.',
    '8 pares de ojetes y pasacables metálicos pavonados anticorrosivos con sistema de amarre rápido. Cordones trenzados de paracord militar (+150 kg de tracción).',
    'Diseñada para marchas continuas en condiciones extremas (+18 a 24 meses de servicio operativo continuo). Plantilla ergonómica antibacterial con amortiguación.',
    'Espesor de cuero flor de 2.2 mm con puntera y talón reforzados. Suela inyectada de caucho vulcanizado nitrilo antideslizante con tacos de tracción profunda.',
    'ADMIN'
),
(
    'GORRA TÁCTICA PATRIOTA',
    'gorra-tactica-patriota',
    'Gorra ajustable verde oliva con panel de velcro frontal y visera termo-moldeada indeformable.',
    15.00,
    '$15.00 Ref',
    'ACCESORIOS',
    '/static/images/placeholder.png',
    79,
    1,
    0,
    20,
    '',
    'gorra, cachucha, verde oliva, patriota, velcro',
    'Tejido Ripstop antidesgarro militar (65% Poliéster / 35% Algodón peinado) de 220 g/m², con tratamiento protector contra rayos ultravioleta (UV) y absorción de sudor.',
    'Botón superior metálico forrado de perfil ultra-bajo. Ajuste posterior con cierre de velcro mil-spec y panel frontal de velcro (7.5 x 5 cm).',
    'Alta resistencia a la exposición solar extrema, humedad y sudoración continua sin pérdida de forma ni tonalidad.',
    'Visera rígida termo-moldeada de 6 costuras paralelas indeformable. Estructura de 6 paneles con ojales bordados de ventilación.',
    'ADMIN'
),
(
    'PARCHE BANDERA DE VENEZUELA',
    'parche-bandera-venezuela',
    'Distintivo táctico bandera nacional tricolor con 8 estrellas bordadas de alta precisión y velcro.',
    5.00,
    '$5.00 Ref',
    'INSIGNIAS Y PARCHES',
    '/static/images/placeholder.png',
    100,
    1,
    0,
    20,
    '',
    'parche, bandera, venezuela, tricolor, 8 estrellas, velcro',
    'Hilatura 100% poliéster de alta tenacidad con hilos de brillo reglamentario fijados térmicamente sobre base de sarga militar pesada. Borde overlock perimetral reforzado.',
    'Respaldo completo con sistema micro-velcro de gancho (Hook) grado militar cosido con doble pespunte perimetral para acople instantáneo.',
    'Colores reactivos con máxima solidez a la intemperie; resistente a sol, agua, lodo y uso táctico intensivo.',
    'Grosor táctico de 2.5 mm; dimensiones reglamentarias 8.0 x 5.0 cm.',
    'ADMIN'
)
ON CONFLICT (slug) DO NOTHING;

-- ==============================================================================
-- INSTRUCCIONES DE SUPABASE STORAGE (ALMACENAMIENTO DE IMÁGENES)
-- ==============================================================================
-- En el panel de Supabase:
-- 1. Ve a "Storage" en el menú lateral.
-- 2. Crea un Bucket llamado: "siscomer" (Marca la casilla "Public bucket" = true).
-- 3. Dentro del bucket puedes tener carpetas: "catalog" y "receipts".
-- 4. O ejecuta el siguiente bloque SQL si tienes permisos de superusuario:

INSERT INTO storage.buckets (id, name, public) 
VALUES ('siscomer', 'siscomer', true)
ON CONFLICT (id) DO NOTHING;

-- Política de lectura pública para que cualquier cliente y el panel web puedan ver las fotos:
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'objects' AND policyname = 'Acceso Publico Lectura Siscomer'
    ) THEN
        CREATE POLICY "Acceso Publico Lectura Siscomer" 
        ON storage.objects FOR SELECT 
        USING (bucket_id = 'siscomer');
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'objects' AND policyname = 'Subida Autenticada Siscomer'
    ) THEN
        CREATE POLICY "Subida Autenticada Siscomer" 
        ON storage.objects FOR INSERT 
        WITH CHECK (bucket_id = 'siscomer');
    END IF;
END $$;

-- Fin del script SQL
