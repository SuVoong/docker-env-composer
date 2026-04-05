# docker-env-composer
 
CLI + TUI interactiva para gestionar entornos de desarrollo Odoo sobre Docker.
 
Permite crear bases de datos desde templates de forma instantanea (~2 segundos),
listar y limpiar entornos de desarrollo sin salir del terminal.
 
## Funcionalidades
 
- **Templates instantaneos**: convierte un ZIP dump a PostgreSQL template una sola vez;
  las copias posteriores tardan ~2 segundos independientemente del tamaño
- **Wizard TUI**: interfaz interactiva en terminal para crear y gestionar bases de datos
- **Modo directo**: creacion por linea de comandos sin TUI, util para scripting
- **Schema sync automatico**: al importar un ZIP, sincroniza el esquema de la BD con
  los modulos instalados (columnas faltantes, vistas huerfanas, modelos obsoletos)
- **Neutralizacion**: desactiva `ir.cron` y `queue_job` para evitar ejecuciones
  involuntarias en entornos de desarrollo
- **Metadata y TTL**: cada BD tiene descripcion, fecha de creacion y expiracion configurable
- **Limpieza automatica**: `dec clean` elimina las bases de datos expiradas
- **Auto-deteccion**: lee el `docker-compose.yml` del directorio actual para detectar
  contenedores, credenciales y version de Odoo sin configuracion adicional
 
## Instalacion
 
```bash
git clone https://github.com/SuVoong/docker-env-composer.git
cd docker-env-composer
pipx install -e .
```
 
**Requisitos:**
- Python 3.10+
- Docker con los contenedores de Odoo corriendo
- PostgreSQL client tools (`psql`, `createdb`, `dropdb`)
 
## Uso con Odoo
 
Ejecutar siempre desde el directorio donde esta el `docker-compose.yml` de tu entorno.
 
### 1. Preparar un template desde ZIP
 
Necesitas un fichero ZIP con el dump de la base de datos. Colócalo en la carpeta
`templates/` junto al `docker-compose.yml`:
 
```
mi-proyecto/
├── docker-compose.yml
└── templates/
    └── test_base.zip        ← dump de Odoo en ZIP
```
 
Importarlo como PostgreSQL template (solo hay que hacerlo una vez por ZIP):
 
```bash
dec template import test_base
```
 
Este proceso restaura el dump, ejecuta el schema sync de 5 fases dentro del
contenedor y marca la BD como template de PostgreSQL para copias instantaneas.
 
Para ver todos los templates ZIP disponibles y cuales ya estan importados:
 
```bash
dec template list
```
 
Ejemplo de salida:
```
Templates ZIP (requieren importar):
  - test_base (234.5 MB) [importado]
  - full_demo (890.2 MB)
 
Templates PostgreSQL (copias instantaneas):
  - dec_tmpl_test_base
```
 
### 2. Crear una base de datos
 
**Via TUI (wizard interactivo):**
 
```bash
dec create
```
 
Se abre un wizard donde seleccionas el template, nombre, descripcion, modulos
extra a instalar y TTL (dias hasta expiracion).
 
**Via comando directo:**
 
```bash
dec create --name Test_base_loyalty --template test_base --ttl 7
dec create --name Test_demo --template test_base --modules pos_loyalty,account --ttl 30
dec create --name prueba_rapida --template test_base --no-tui
```
 
Opciones disponibles:
 
| Opcion | Descripcion |
|--------|-------------|
| `--name` / `-n` | Nombre de la base de datos |
| `--template` / `-t` | Template a usar (nombre sin extension) |
| `--ttl` | Dias hasta expiracion (sin TTL = no expira) |
| `--modules` / `-m` | Modulos extra a instalar (separados por coma) |
| `--description` / `-d` | Descripcion del entorno |
| `--no-tui` | Modo directo sin interfaz interactiva |
 
### 3. Gestionar bases de datos
 
```bash
dec list
```
 
Abre el dashboard con todas las bases de datos registradas:
 
```
 Nombre              Descripcion          Version  Template      Creada      Expira          Tamaño
 Test_base_loyalty    Test fidelizacion    16.0     tpv_base      2026-04-01  2026-04-08(3d)  1.2 GB
 Test_demo            Demo cliente X       16.0     tpv_base      2026-04-02  ─               890 MB
 prueba_rapida       (sin registrar)      16.0     ─             ─           ─               234 MB
```
 
Acciones desde el dashboard:
- `d` + `d` — eliminar la BD seleccionada (doble pulsacion como confirmacion)
- `r` — refrescar
- `q` — salir
 
Para limpiar todas las bases de datos expiradas:
 
```bash
dec clean           # pide confirmacion
dec clean --yes     # sin confirmacion
```
 
## Estructura del proyecto
 
```
src/docker_env_composer/
├── cli.py                          # Entry point: comando `dec`
├── core/                           # Logica generica (ERP-agnostic)
│   ├── detect.py                   # Auto-deteccion desde docker-compose.yml
│   ├── docker.py                   # Primitivas Docker (exec, cp)
│   ├── postgres.py                 # Operaciones PostgreSQL (create, drop, templates)
│   ├── registry.py                 # Metadata de bases de datos (~/.dec/registry.json)
│   ├── templates.py                # Pipeline ZIP -> PG template (delega a plugins)
│   └── utils.py                    # Utilidades compartidas
├── plugins/                        # Logica especifica por ERP
│   ├── base.py                     # ERPPlugin (interfaz abstracta)
│   ├── generic.py                  # Fallback: solo dump/restore, sin ERP
│   └── odoo/                       # Plugin Odoo
│       ├── __init__.py             # OdooPlugin: deteccion, post-restore, modulos
│       ├── filestore.py            # Gestion filestore (/opt/odoo/odoo-data/filestore)
│       ├── neutralize.py           # Desactivar queue_job + ir_cron
│       └── schema_sync.py          # Script AST 5 fases (se ejecuta dentro del contenedor)
└── tui/                            # Interfaz terminal (Textual)
    ├── app.py                      # Entry points TUI
    ├── banner.py                   # Banner ASCII
    ├── styles.tcss                 # Estilos
    └── screens/
        ├── create.py               # Wizard de creacion
        └── dashboard.py            # Dashboard: listado + gestion
```
 
## Licencia
 
MIT