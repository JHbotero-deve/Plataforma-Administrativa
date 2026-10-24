-- Registra AliExpress como fuente admitida por el importador manual.
-- La generación de enlaces se realiza únicamente cuando el enlace promocional ya existe
-- o cuando se habiliten credenciales oficiales de afiliados.
INSERT INTO platforms (name, base_url)
VALUES ('aliexpress', 'https://www.aliexpress.com')
ON CONFLICT (name) DO NOTHING;
