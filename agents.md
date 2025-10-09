# Contexto 

Objetivo. Construir una GUI en Python/Tkinter para explorar y comparar configuraciones de ebook-convert al convertir PDF→EPUB. La app gestiona múltiples “config tabs”, previsualiza el HTML intermedio (OEB) leyendo el content.opf y renderizando el primer elemento del spine, y solo crea el .epub bajo demanda. Soporta rango de páginas recortando el PDF previo con qpdf.

Tecnologías. Python 3.10+, Tkinter/ttk (ttk.Notebook), tkinterweb como visor HTML, subprocess para invocar ebook-convert y qpdf, tempfile/atexit para temporales, pathlib/shutil, json para configs, xml.etree.ElementTree o lxml para OPF. Incluye detección y limpieza de temporales en arranque. Validación de entradas y tooltips generados desde un catálogo JSON de opciones.

Fuentes principales de opciones y descripciones: manual oficial de Calibre y manpages de ebook-convert para EPUB. Revisa siempre ebook-convert input.pdf output.epub -h en tu versión por cambios puntuales.

Dispones de una checklist.md donde deberás anotar cada tarea según la completes y de tool_description.md con un listado de las opciones de ebook-convert y lo que hacen brevemente.

# Estilo de programación

Asegúrate de usar estilo clean code. Hacerlo mantenible y tratar de añadir únicamente los cambios estrictamente necesarios y probar que funcionan con toy examples. Enfoca cada cambio para que respete el plan global y todas las piezas casen adecuadamente. Trata de hacerlo modular para poder detectar los fallos. Haz exceptions significativos y solo trata de resolverlos cuando tengas una forma clara de hacerlo.