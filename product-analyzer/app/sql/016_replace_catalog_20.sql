-- Reemplazo inicial del catálogo por 20 productos propios.
CREATE TABLE IF NOT EXISTS catalog_seed_runs (
    seed_name VARCHAR(100) PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM catalog_seed_runs WHERE seed_name = 'catalogo_20_productos_2026_10') THEN
        INSERT INTO platforms (name, base_url) VALUES ('personal', NULL)
        ON CONFLICT (name) DO NOTHING;

        INSERT INTO categories (name) VALUES
            ('electronica'),('hogar'),('fitness'),('ropa'),('calzado'),('accesorios'),('otros')
        ON CONFLICT (name) DO NOTHING;

        UPDATE published_cards
        SET is_published = FALSE, updated_at = NOW();

        UPDATE products
        SET is_active = FALSE, is_blocked = TRUE, updated_at = NOW();

        INSERT INTO products (
            platform_id, category_id, external_id, title, image_url, image_gallery,
            product_url, current_price, currency, rating, reviews_count,
            sales_estimate, is_active, stock, sku, description,
            previous_price, catalog_batch_id, catalog_expires_at, is_blocked, source_metadata
        )
        SELECT
            pl.id, c.id, v.external_id, v.title, v.image_url,
            jsonb_build_array(v.image_url), NULL, v.price, 'COP', v.rating,
            v.reviews_count, v.sales_estimate, TRUE, v.stock, v.sku, v.description,
            v.previous_price, NOW(), NOW() + INTERVAL '48 hours', FALSE,
            jsonb_build_object('source','catalogo_inicial','seed','catalogo_20_productos_2026_10')
        FROM (VALUES
            ('CENT-001','Audífonos Bluetooth Pro ANC','https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=900&q=85','electronica',189900,4.8,324,780,35,'CENT-AUD-001','Cancelación de ruido, Bluetooth 5.3 y hasta 30 horas de batería.',249900),
            ('CENT-002','Smartwatch Active Fit 2','https://images.unsplash.com/photo-1524805444758-089113d48a6d?auto=format&fit=crop&w=900&q=85','electronica',219900,4.7,218,520,28,'CENT-WAT-002','Monitor deportivo, notificaciones, sueño y resistencia al agua.',289900),
            ('CENT-003','Teclado Mecánico RGB Compacto','https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=900&q=85','electronica',159900,4.6,187,430,22,'CENT-KEY-003','Teclado mecánico compacto con iluminación RGB y conexión USB.',199900),
            ('CENT-004','Mouse Gamer Ergonomico RGB','https://images.unsplash.com/photo-1527814050087-3793815479db?auto=format&fit=crop&w=900&q=85','electronica',89900,4.6,265,690,40,'CENT-MOU-004','Sensor preciso, siete botones programables y diseño ergonómico.',119900),
            ('CENT-005','Cámara Web Full HD 1080p','https://images.unsplash.com/photo-1516035069371-29a1b244cc32?auto=format&fit=crop&w=900&q=85','electronica',129900,4.5,142,360,18,'CENT-CAM-005','Videollamadas Full HD con micrófono integrado y enfoque automático.',159900),
            ('CENT-006','Lámpara LED de Escritorio','https://images.unsplash.com/photo-1507473885765-e6ed057f782c?auto=format&fit=crop&w=900&q=85','hogar',79900,4.7,301,810,45,'CENT-LAM-006','Lámpara LED regulable para estudio, trabajo y lectura.',99900),
            ('CENT-007','Termo Térmico 750 ml','https://images.unsplash.com/photo-1602143407151-7111542de6e8?auto=format&fit=crop&w=900&q=85','hogar',69900,4.8,411,920,55,'CENT-TER-007','Termo de acero inoxidable con aislamiento térmico de doble pared.',89900),
            ('CENT-008','Organizador Modular de Escritorio','https://images.unsplash.com/photo-1494438639946-1ebd1d20bf85?auto=format&fit=crop&w=900&q=85','hogar',54900,4.5,176,470,32,'CENT-ORG-008','Organizador modular para accesorios, cables y elementos de oficina.',69900),
            ('CENT-009','Silla Ergonómica de Oficina','https://images.unsplash.com/photo-1503602642458-232111445657?auto=format&fit=crop&w=900&q=85','hogar',549900,4.7,98,180,9,'CENT-SIL-009','Silla con soporte lumbar, altura regulable y respaldo ergonómico.',699900),
            ('CENT-010','Mochila Urbana Antirrobo','https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=900&q=85','accesorios',149900,4.8,355,760,38,'CENT-MOC-010','Mochila para portátil con compartimentos internos y cierre de seguridad.',189900),
            ('CENT-011','Gafas de Sol Polarizadas UV400','https://images.unsplash.com/photo-1511499767150-a48a237f0083?auto=format&fit=crop&w=900&q=85','accesorios',99900,4.6,203,540,31,'CENT-GAF-011','Gafas polarizadas con protección UV400 y montura ligera.',139900),
            ('CENT-012','Reloj Minimalista de Acero','https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=900&q=85','accesorios',179900,4.7,156,330,17,'CENT-REL-012','Reloj analógico de diseño minimalista con correa de acero.',229900),
            ('CENT-013','Tenis Urbanos Unisex','https://images.unsplash.com/photo-1542291026-7eec264c27ff?auto=format&fit=crop&w=900&q=85','calzado',199900,4.7,287,650,36,'CENT-TEN-013','Tenis urbanos ligeros para uso diario con suela de alto agarre.',249900),
            ('CENT-014','Zapatillas Running Flex','https://images.unsplash.com/photo-1551107696-a4b0c5a0d9a2?auto=format&fit=crop&w=900&q=85','calzado',229900,4.8,242,510,27,'CENT-RUN-014','Zapatillas para entrenamiento y running con amortiguación flexible.',289900),
            ('CENT-015','Camiseta Básica Premium','https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?auto=format&fit=crop&w=900&q=85','ropa',69900,4.6,329,880,50,'CENT-CAM-015','Camiseta de algodón de corte regular y acabado premium.',89900),
            ('CENT-016','Chaqueta Impermeable Ligera','https://images.unsplash.com/photo-1544966503-7cc5ac882d5f?auto=format&fit=crop&w=900&q=85','ropa',189900,4.7,141,290,15,'CENT-CHA-016','Chaqueta ligera repelente al agua para ciudad y actividades al aire libre.',239900),
            ('CENT-017','Mat de Yoga Antideslizante','https://images.unsplash.com/photo-1599447421416-3414500d18a5?auto=format&fit=crop&w=900&q=85','fitness',89900,4.8,274,610,34,'CENT-YOG-017','Mat de yoga de alta adherencia y superficie acolchada.',119900),
            ('CENT-018','Bandas Elásticas de Resistencia','https://images.unsplash.com/photo-1517836357463-d25dfeac3438?auto=format&fit=crop&w=900&q=85','fitness',59900,4.7,198,530,30,'CENT-BAN-018','Set de bandas de resistencia para entrenamiento en casa.',79900),
            ('CENT-019','Botella Deportiva 1 Litro','https://images.unsplash.com/photo-1602143407151-7111542de6e8?auto=format&fit=crop&w=900&q=85','fitness',64900,4.6,233,570,33,'CENT-BOT-019','Botella reutilizable de gran capacidad para gimnasio y actividades deportivas.',84900),
            ('CENT-020','Soporte Ajustable para Celular','https://images.unsplash.com/photo-1586953208448-b95a79798f07?auto=format&fit=crop&w=900&q=85','accesorios',49900,4.6,361,970,60,'CENT-SOP-020','Soporte plegable y ajustable para celular y videollamadas.',69900)
        ) AS v(external_id,title,image_url,category,price,rating,reviews_count,sales_estimate,stock,sku,description,previous_price)
        JOIN platforms pl ON pl.name = 'personal'
        JOIN categories c ON c.name = v.category
        ON CONFLICT (platform_id, external_id) DO UPDATE SET
            category_id=EXCLUDED.category_id,title=EXCLUDED.title,image_url=EXCLUDED.image_url,
            image_gallery=EXCLUDED.image_gallery,current_price=EXCLUDED.current_price,
            currency=EXCLUDED.currency,rating=EXCLUDED.rating,reviews_count=EXCLUDED.reviews_count,
            sales_estimate=EXCLUDED.sales_estimate,is_active=TRUE,stock=EXCLUDED.stock,
            sku=EXCLUDED.sku,description=EXCLUDED.description,previous_price=EXCLUDED.previous_price,
            catalog_expires_at=EXCLUDED.catalog_expires_at,is_blocked=FALSE,
            source_metadata=EXCLUDED.source_metadata,updated_at=NOW();

        INSERT INTO product_scores (product_id,price_score,demand_score,trend_score,opportunity_score,calculated_at)
        SELECT p.id, 88, LEAST(99, 55 + COALESCE(p.reviews_count,0) / 10.0),
               84, LEAST(98, 78 + COALESCE(p.rating,0) * 3), NOW()
        FROM products p
        JOIN platforms pl ON pl.id=p.platform_id
        WHERE pl.name='personal'
          AND p.external_id LIKE 'CENT-%'
        ON CONFLICT (product_id) DO UPDATE SET
            price_score=EXCLUDED.price_score,demand_score=EXCLUDED.demand_score,
            trend_score=EXCLUDED.trend_score,opportunity_score=EXCLUDED.opportunity_score,
            calculated_at=NOW();

        INSERT INTO published_cards (
            product_id,title,subtitle,price_display,image_url,product_url,
            sale_price,cost_price,profit_amount,profit_margin_pct,
            opportunity_score,footer,accent,is_published,sort_order,published_at,updated_at
        )
        SELECT p.id,p.title,'Producto propio · entrega en Colombia',
               to_char(p.current_price,'FM999G999G999') || ' COP',
               p.image_url,NULL,p.current_price,p.current_price,0,0,
               s.opportunity_score,'Disponible en Central','#b6f23a',TRUE,
               row_number() OVER (ORDER BY p.id),NOW(),NOW()
        FROM products p
        JOIN platforms pl ON pl.id=p.platform_id
        LEFT JOIN product_scores s ON s.product_id=p.id
        WHERE pl.name='personal' AND p.external_id LIKE 'CENT-%'
        ON CONFLICT (product_id) DO UPDATE SET
            title=EXCLUDED.title,subtitle=EXCLUDED.subtitle,
            price_display=EXCLUDED.price_display,image_url=EXCLUDED.image_url,
            sale_price=EXCLUDED.sale_price,cost_price=EXCLUDED.cost_price,
            opportunity_score=EXCLUDED.opportunity_score,footer=EXCLUDED.footer,
            accent=EXCLUDED.accent,is_published=TRUE,sort_order=EXCLUDED.sort_order,
            updated_at=NOW();

        INSERT INTO catalog_seed_runs(seed_name) VALUES ('catalogo_20_productos_2026_10');
    END IF;
END $$;
