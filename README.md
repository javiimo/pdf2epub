# pdf2epub

Interfaz en Tk para orquestar conversiones PDF→EPUB sobre la CLI de Calibre (`ebook-convert`).  
Permite mantener varias configuraciones, combinarlas con presets por capas, importar líneas CLI ya existentes y previsualizar el OEB antes de generar el EPUB definitivo.

## Funcionalidades destacadas
- **Pestañas independientes** con opciones persistentes y estado del visor.
- **Presets combinables por capas** (dispositivo, columnas, contenido, TOC, limpieza, debug) con detección de conflictos.
- **Importación CLI inteligente**: limpia las opciones actuales, aplica únicamente las de la línea, ignora flags desconocidas y avisa de las no soportadas por tu binario.
- **Previsualización OEB**: genera un subconjunto del PDF con `qpdf` y muestra el primer HTML lineal en el visor embebido (`tkinterweb`).
- **Atajos útiles**: clonación de pestañas, guardado de presets personalizados, exportación/importación de configuraciones.

## Requisitos previos
- Python 3.10 o superior.
- Calibre con `ebook-convert` accesible en el `PATH` (recomendado ≥ 6.x).
- `qpdf` instalado para generar subconjuntos del PDF original.
- Librerías del sistema necesarias para Tk y fuentes básicas (ya incluidas en la mayoría de distribuciones).

## Instalación
Se recomienda trabajar dentro de un entorno virtual dedicado:

```bash
python3 -m venv .venv
source .venv/bin/activate           # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> Tip: si prefieres no activar el entorno, ejecuta con ruta absoluta (`./.venv/bin/python run.py`, `./.venv/bin/python -m pytest`, etc.).

## Puesta en marcha
```bash
python run.py               # o ./venv/bin/python run.py
```

Argumentos disponibles:
- `--catalog <ruta>`: usa un catálogo JSON alternativo (por defecto se toma `assets/options_catalog.json`).

### Importar una línea CLI existente
1. Abre la app y crea/selecciona una pestaña.
2. Usa **Archivo → Importar línea CLI** (o el botón correspondiente en la barra).
3. Pega tu comando `ebook-convert …` (se admiten líneas con `\`).
4. El formulario se limpia y se rellenan únicamente las opciones soportadas.  
   Las flags desconocidas o no soportadas se informan en la consola de la pestaña.

### Presets combinables
1. Pulsa **Presets → Seleccionar**.
2. Marca uno o varios presets. Se aplican siguiendo el orden lógico Base → Columnas → Contenido → TOC → Limpieza → Debug y el último gana en opciones exclusivas.
3. Los conflictos detectados y las reglas post-procesadas (heurísticas desactivadas, etc.) se resumen en la consola y en la barra de estado.

## Ejecutar pruebas
Dentro del entorno virtual:

```bash
python -m pytest          # o ./venv/bin/python -m pytest
```

La suite incluye pruebas de integración con Tkinter; si ves avisos de hilos de `tkinterweb`, son esperables en entornos headless pero no afectan al resultado.

## Estructura principal
- `app/` – widgets Tk, formularios y tema.
- `core/` – lógica de negocio (catálogo, runners, presets, parser CLI, validaciones).
- `assets/` – datos estáticos como el catálogo de opciones.
- `tests/` – suite `pytest` con dobles y fixtures para UI y runners.

## Problemas frecuentes
- **`ebook-convert` no acepta una opción importada**: la consola mostrará el flag omitido; asegúrate de que tu versión de Calibre la soporta.
- **Previsualización falla por dependencias**: revisa que `qpdf` esté instalado y que `ebook-convert` esté en el `PATH`.
- **Tk no se inicia**: en entornos sin servidor gráfico puedes necesitar un backend virtual (p.ej. `xvfb-run python run.py`).

¡Listo! Lanza `run.py`, selecciona tu PDF y ajusta las opciones de forma guiada para obtener un EPUB limpio.*** End Patch
