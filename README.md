# docker-env-composer

CLI + TUI interactiva para gestionar entornos de desarrollo ERP sobre Docker.

Soporta **Odoo** out-of-the-box y es extensible a otros ERPs (ERPNext, Tryton, etc.) mediante plugins.

## Features

- **Auto-deteccion**: lee `docker-compose.yml` del directorio actual para detectar ERP, version, containers y puertos
- **Plugin architecture**: logica ERP-specific aislada en plugins (`odoo`, `generic`), core 100% agnostico
- **Wizard TUI**: interfaz interactiva en terminal para crear, listar y eliminar bases de datos
- **Templates instantaneos**: convierte ZIP dumps a PostgreSQL templates para copias en ~2 segundos
- **Metadata y descripciones**: cada base de datos tiene descripcion, fecha de creacion y expiracion
- **Limpieza automatica**: TTL configurable y `dec clean` para borrar bases expiradas
- **Multi-version**: funciona con cualquier version de Odoo (v11, v16, v17+) que tenga Docker Compose

## Instalacion

```bash
# Desarrollo local
git clone https://github.com/SuVoong/docker-env-composer.git
cd docker-env-composer
pipx install -e .
```

## Uso

Ejecutar desde el directorio donde esta tu `docker-compose.yml`:

```bash
# Wizard interactivo
dec create          # crear base de datos con wizard TUI
dec list            # dashboard con todas las bases de datos
dec drop            # selector interactivo para eliminar

# Modo directo (sin TUI)
dec create --name TPV_test_loyalty --template tpv_base --ttl 7 --no-tui
dec drop mi_test --yes
dec clean --yes     # eliminar todas las expiradas

# Templates
dec template list                   # ver templates disponibles
dec template import tpv_base        # importar ZIP -> PG template (una vez)
```

## Templates

La primera vez que uses un template ZIP, importalo como PostgreSQL template:

```bash
# Importar (lento, solo una vez por template)
dec template import tpv_base

# Despues, crear bases de datos es instantaneo (~2s)
dec create --name TPV_test_x --template tpv_base --no-tui
```

Los archivos ZIP deben estar en `templates/` junto al `docker-compose.yml`.

## Plugin Architecture

El core es ERP-agnostico. Toda la logica especifica de cada ERP vive en plugins:

### Plugin interface

```python
class ERPPlugin(ABC):
    name: str                           # "odoo", "erpnext", "generic"
    def detect_from_compose(services)   # detectar ERP desde docker-compose.yml
    def post_restore(container, db, pg) # schema sync, registry rebuild, etc.
    def install_modules(container, db, modules)
    def restore_filestore(container, db, src_dir)
    def remove_filestore(container, db)
    def copy_filestore(container, src_db, dst_db)
    def neutralize(pg, db)              # desactivar crons, queue jobs, etc.
```

### Plugins disponibles

| Plugin | Detecta por | Comportamiento |
|--------|-------------|----------------|
| `odoo` | Imagen Docker con "odoo" | Schema sync (5 fases AST), odoo-bin, filestore, queue_job/ir_cron |
| `generic` | Fallback (cualquier PostgreSQL) | Solo dump/restore + PG template, sin ERP-specific |

### Odoo: Schema Sync (pipeline de import)

Al importar un ZIP como template (`dec template import`), el plugin Odoo ejecuta:

1. **Restaurar dump.sql** — carga tolerante a errores
2. **Post-restore** (plugin):
   - **odoo-bin -u base** — sincroniza core + registry
   - **Schema sync** (5 fases AST-based):
     - Phase 1: Anadir columnas faltantes (Python fields -> `ALTER TABLE`)
     - Phase 2: Eliminar vistas de modulos ausentes del disco
     - Phase 3: Eliminar vistas heredadas con referencias a campos inexistentes
     - Phase 4: Limpiar `ir.model` huerfanos + access, rules, constraints
     - Phase 5: Detectar y eliminar columnas espurias (artefactos de imports anteriores)
3. **Restaurar filestore** (plugin) — copia attachments al container
4. **Neutralizar** (plugin) — queue_job + desactivar ir.cron
5. **Marcar como PG template** — copias futuras instantaneas

### Phase 5: Columnas espurias

Versiones anteriores del schema sync tenian un bug de producto cartesiano:
al escanear campos con regex, asignaba TODOS los campos de un fichero `.py`
a TODOS los modelos del mismo fichero. Esto creaba columnas en tablas incorrectas
(ej: `product_tmpl_id` en `product_template`, causando errores de "ambiguous column"
en queries SQL de Odoo).

La version actual usa analisis AST por clase, asociando cada campo solo con su
modelo correcto. Phase 5 detecta y elimina automaticamente columnas espurias
de imports anteriores.

## Estructura del proyecto

```
src/docker_env_composer/
├── cli.py                          # Entry point: comando `dec`
├── core/                           # Generico (ERP-agnostic)
│   ├── detect.py                   # Auto-deteccion desde docker-compose.yml via plugins
│   ├── docker.py                   # Primitivas Docker (exec, cp)
│   ├── postgres.py                 # Operaciones PostgreSQL (create, drop, templates)
│   ├── registry.py                 # Metadata de bases de datos (~/.dec/registry.json)
│   └── templates.py                # Pipeline ZIP -> PG template (delega a plugins)
├── plugins/                        # ERP-specific
│   ├── base.py                     # ERPPlugin ABC (interfaz)
│   ├── generic.py                  # GenericPlugin: solo dump/restore (sin ERP)
│   └── odoo/                       # OdooPlugin: schema sync, odoo-bin, filestore
│       ├── __init__.py             # Plugin principal
│       ├── filestore.py            # Operaciones filestore (/opt/odoo/...)
│       ├── neutralize.py           # queue_job + ir_cron
│       └── schema_sync.py          # Script AST de 5 fases (se ejecuta en container)
└── tui/                            # Interfaz terminal (Textual)
    ├── app.py                      # Entry points TUI
    ├── banner.py                   # ASCII art banner
    ├── styles.tcss                 # Estilos Textual
    └── screens/
        ├── create.py               # Wizard de creacion
        ├── dashboard.py            # Lista de bases de datos
        └── drop.py                 # Selector de eliminacion
```

## Crear un nuevo plugin

1. Crear `plugins/mi_erp.py` (o `plugins/mi_erp/`)
2. Implementar `ERPPlugin` ABC
3. Registrar en `core/detect.py` (`_load_plugins()`)

```python
from docker_env_composer.plugins.base import ERPPlugin

class MyERPPlugin(ERPPlugin):
    @property
    def name(self) -> str:
        return "myerp"

    @staticmethod
    def detect_from_compose(services: dict) -> dict | None:
        # Return env dict if detected, None otherwise
        ...
```

## Requisitos

- Python 3.10+
- Docker con contenedores corriendo
- PostgreSQL client tools (`psql`, `createdb`, `dropdb`)

## Licencia

MIT
