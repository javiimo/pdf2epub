# Contexto 

Objetivo. Construir una GUI en Python/Tkinter para explorar y comparar configuraciones de ebook-convert al convertir PDF→EPUB. La app gestiona múltiples “config tabs”, previsualiza el HTML intermedio (OEB) leyendo el content.opf y renderizando el primer elemento del spine, y solo crea el .epub bajo demanda. Soporta rango de páginas recortando el PDF previo con qpdf.

Buscamos detectar formulas (tanto inline como en líneas aparte) y tablas con un modelo de layout sobre PNGs de las páginas. Fusionar cajas. Rasterizar solo esas cajas a 360–420 dpi. Inserta <img> en el OEB antes de empaquetar EPUB3.

Tecnologías. Python 3.10+, Tkinter/ttk (ttk.Notebook), tkinterweb como visor HTML, subprocess para invocar ebook-convert y qpdf, tempfile/atexit para temporales, pathlib/shutil, json para configs, xml.etree.ElementTree o lxml para OPF. Incluye detección y limpieza de temporales en arranque. Validación de entradas y tooltips generados desde un catálogo JSON de opciones.

Tienes en ./.venv-gpu. las herramientas necesarias para ejecutar los tests y además puedes añadir las librerías y todo lo que necesites para la ejecución en ese .venv-gpu. Puedes modificarlo según tus necesidades. Para lanzar las pruebas usa directamente los ejecutables del entorno, p. ej.:

```bash
.venv-gpu/bin/pytest
```

También puedes activar el entorno con `source .venv-gpu/bin/activate` si prefieres usar los comandos sin la ruta explícita.

Fuentes principales de opciones y descripciones: manual oficial de Calibre y manpages de ebook-convert para EPUB. Revisa siempre ebook-convert input.pdf output.epub -h en tu versión por cambios puntuales.

Dispones de una checklist.md donde deberás anotar cada tarea según la completes y de tool_description.md con un listado de las opciones de ebook-convert y lo que hacen brevemente.

Usa siempre los ejecutables de `.venv-gpu` para tests/comandos y marca checklist.md en cuanto cierres cada tarea.

# Estilo de programación

Asegúrate de usar estilo clean code. Hacerlo mantenible y tratar de añadir únicamente los cambios estrictamente necesarios y probar que funcionan con toy examples. Enfoca cada cambio para que respete el plan global y todas las piezas casen adecuadamente. Trata de hacerlo modular para poder detectar los fallos. Haz exceptions significativos y solo trata de resolverlos cuando tengas una forma clara de hacerlo.
