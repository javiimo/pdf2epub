# pdf2epub

Interfaz gráfica en Tk para construir y ejecutar conversiones PDF→EPUB sobre `ebook-convert`. Permite gestionar múltiples configuraciones, crear presets y previsualizar el resultado HTML antes de generar el EPUB final.

## Requisitos previos
- Python 3.10 o superior.
- Calibre CLI (`ebook-convert`) disponible en el `PATH`.
- `qpdf` instalado y accesible.

## Instalación rápida
```bash
python -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Las dependencias principales son `tkinterweb` (visor embebido) y `lxml` para validar expresiones XPath. Si no necesitas validación avanzada puedes omitir `lxml`.

## Ejecución
```bash
python run.py
```

Argumentos opcionales:
- `--catalog <ruta>`: utiliza un catálogo JSON alternativo para los metadatos de opciones.

## Pruebas
```bash
pip install pytest
pytest
```

## Estructura relevante
- `app/`: componentes Tk y formularios.
- `core/`: lógica independiente de la UI (catálogo, validaciones, runners).
- `assets/`: catálogo de opciones.
- `tests/`: suite de pruebas con `pytest`.
