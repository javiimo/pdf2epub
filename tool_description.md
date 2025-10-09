# Opciones de ebook-convert para PDF→EPUB (nombre → qué hace)
## Comunes / Perfiles 
manual.calibre-ebook.com

--input-profile: perfil de interpretación de entrada.

--output-profile: perfil del dispositivo de salida.

--help, --version.

## Look & Feel (tipografías, CSS, espaciado) 
manual.calibre-ebook.com

--asciiize: translitera Unicode a ASCII.

--base-font-size <pt>: base para reescalado tipográfico.

--change-justification left|justify|original: fuerza alineación.

--disable-font-rescaling: desactiva reescalado de fuentes.

--embed-all-fonts: incrusta todas las fuentes detectadas.

--embed-font-family <familia>: fija fuente base incrustada.

--expand-css: CSS no abreviado.

--extra-css <ruta|css>: CSS adicional.

--filter-css <prop,prop,...>: elimina propiedades CSS.

--font-size-mapping 8 vals: mapa xx-small→huge.

--insert-blank-line y --insert-blank-line-size <em>: línea en blanco entre párrafos.

--keep-ligatures: conserva ligaduras Unicode.

--line-height <pt> y --minimum-line-height <percent>: interlineado.

--linearize-tables: “des-tabla” maquetaciones con tablas.

--margin-top|right|bottom|left <pt>: márgenes.

--remove-paragraph-spacing y --remove-paragraph-spacing-indent-size <em>: quita espacio y aplica sangría.

--smarten-punctuation / --unsmarten-punctuation: comillas y guiones tipográficos.

--subset-embedded-fonts: sub-conjunto de glifos.

--transform-css-rules <ruta> / --transform-html-rules <ruta>: reglas de transformación.

## Heuristics (desactivables individualmente) 
manual.calibre-ebook.com

--enable-heuristics: activa el bloque.

--disable-dehyphenate, --disable-delete-blank-paragraphs, --disable-fix-indents, --disable-format-scene-breaks, --disable-italicize-common-cases, --disable-markup-chapter-headings, --disable-renumber-headings, --disable-unwrap-lines: anula acciones concretas.

--html-unwrap-factor <0..1>: umbral de “desenvolver” líneas.

--replace-scene-breaks <texto>.

## Search & Replace (regex) 
manual.calibre-ebook.com

--search-replace <ruta>: fichero con pares regex→reemplazo.

--sr1-search|--sr1-replace … --sr3-*: tres reglas in-line rápidas.

## Structure Detection (capítulos, portada, comienzo) 
manual.calibre-ebook.com

--add-alt-text-to-img: completa alt con metadatos.

--chapter <XPath>: detecta títulos de capítulo.

--chapter-mark pagebreak|rule|none|both: marca capítulos.

--disable-remove-fake-margins: conserva márgenes simulados.

--insert-metadata: inserta metadatos al inicio.

--page-breaks-before <XPath>: saltos antes de elementos.

--prefer-metadata-cover: prioriza portada detectada.

--remove-first-image: elimina primera imagen.

--start-reading-at <XPath>: punto de inicio de lectura.

Table of Contents (TOC) 
manual.calibre-ebook.com

--duplicate-links-in-toc: permite duplicados.

--level1-toc|--level2-toc|--level3-toc <XPath>: niveles 1–3.

--max-toc-links <n>: máximo de enlaces auto-TOC.

--no-chapters-in-toc: no añade capítulos auto-detectados.

--toc-filter <regex>: filtra entradas.

--toc-threshold <n>: umbral para enlazar capítulos.

--use-auto-toc: fuerza usar TOC auto-generado.

## Metadata 
manual.calibre-ebook.com

--author-sort, --authors, --book-producer, --comments, --cover, --isbn, --language, --pubdate, --publisher, --rating, --read-metadata-from-opf/--from-opf/-m, --series, --series-index, --tags, --timestamp, --title, --title-sort.

## Debug 
manual.calibre-ebook.com

--debug-pipeline|-d <dir>: vuelca fases OEB/HTML/CSS.

--verbose|-v (repetible): verbosidad.

## PDF Input (entrada PDF) 
manual.calibre-ebook.com

--input-encoding <charset>: fuerza codificación.

--no-images: no extrae imágenes.

--pdf-engine calibre|pdftohtml: motor; calibre elimina encabezados/pies automáticamente.

--pdf-footer-regex / --pdf-header-regex: elimina primera/última línea con regex.

--pdf-footer-skip <px> / --pdf-header-skip <px>: recorte por píxeles; valores negativos = auto-detección; 0 = no quitar.

--unwrap-factor <0..1>: umbral de desenvuelto de línea.

## EPUB Output (salida EPUB) 
manual.calibre-ebook.com
+1

--dont-split-on-page-breaks: no dividir por saltos de página.

--flow-size <KB>: divide HTML grandes; 0 = sin división.

--epub-version 2|3: versión del EPUB.

--epub-inline-toc: TOC inline en el contenido.

--epub-toc-at-end: coloca el TOC inline al final.

--epub-flatten: aplanar estructura de ficheros del EPUB.

--epub-max-image-size WxH|none|profile: límite de imagen.

--no-default-epub-cover: no crear portada por defecto.

--no-svg-cover: no usar SVG en portada.

--preserve-cover-aspect-ratio: escala portada sin deformar.

--extract-to <dir>: extrae el EPUB generado para inspección.

--pretty-print: salida legible para humanos.

--toc-title <texto>: título del TOC inline.