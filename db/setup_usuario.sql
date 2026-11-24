-- Corre este script UNA VEZ conectado como superusuario (postgres).
-- Crea un usuario dedicado y la base de datos del proyecto.

-- 1. Crear usuario (reemplaza CAMBIAR_ESTA_CLAVE por una contraseña propia)
CREATE USER carroya_user WITH PASSWORD 'CAMBIAR_ESTA_CLAVE';

-- 2. Crear base de datos con ese usuario como dueño
CREATE DATABASE carroya OWNER carroya_user;

-- 3. Conectarse a la BD y dar permisos sobre el schema public
\c carroya
GRANT ALL ON SCHEMA public TO carroya_user;
