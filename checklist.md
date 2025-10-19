Checklist detallado hacia posicionamiento perfecto (bloques vs. inline)

Objetivo general
- Detectar fórmulas y tablas, rasterizarlas y colocarlas en el OEB/EPUB sin artefactos. Empezar por una solución 100% efectiva (no inline) y después abordar el caso inline con un método específico.

Prerrequisitos (ya implementados)
- [x] Rasterización páginas PNG para detectores: `rasterize_pdf_to_png` (pdftocairo) a 360–420 dpi.
- [x] Detección layout + tablas: `infer_layout_on_image` + `infer_tables_on_image` y fusión con IoU.
- [x] Postprocesado de cajas: filtrado, merge, margen, etiquetas `mathblock`/`tableblock`.
- [x] Rasterización selectiva de bboxes a imágenes individuales: `rasterize_boxes_from_pdf`.
- [x] Ensamblado OEB: copiar imágenes, manifest en `content.opf`, CSS base.

Bloque A — Solución 100% efectiva (no inline)

Diseño y colocación respecto al texto (validación con Mastering.pdf p.67 y p.76)
- [x] Definir reglas de colocación no inline por segmentos de página: usar marcas de salto de página en HTML (anchors calibre_pb_*, epub:type=pagebreak, hr.pagebreak, etc.).
- [x] Implementar inserción por segmento + ratio vertical: `insert_figures_inline` con hints (page_index, y, page_height) para colocar tras el cierre de bloque más cercano dentro del segmento.
- [x] Fallback robusto: si faltan marcas de página o hints, insertar antes de `</body>` manteniendo el orden lógico.
- [x] Heurística de limpieza local: eliminar el texto matemático cercano a la figura cuando sea un párrafo corto con símbolos matemáticos; no tocar el resto.
- [ ] Tests de diseño (unidad, sintéticos):
  - [x] Segmentación por pagebreak: dos figuras en misma página se insertan en el segmento correcto y en orden.
  - [x] Limpieza de párrafo-math: el párrafo “E = mc^2” adyacente desaparece, no se eliminan párrafos normales.
- [ ] Tests de integración con Mastering.pdf:
  - [x] p.67 (tablas): detectar tablas, rasterizar y verificar que las imágenes aparecen en el segmento correcto del primer HTML del spine; no se duplica texto.
  - [x] p.76 (fórmulas en bloque): idem, con figuras `mathblock` en su segmento.

Integración en la pipeline para HTML y EPUB
- [x] Distribución por ficheros del spine: repartir figuras según número de segmentos por fichero y page_index; usar `preview.spine_linear_items`.
- [x] Integrar en `enrich_oeb_with_ml`: generar hints por caja (page_index, y, page_height), crear `FigureSpec` y llamar a `insert_figures_inline` por fichero con `page_offset` adecuado.
- [x] Garantizar CSS y manifest en OEB: `install_default_css`, `add_images_to_manifest`.
- [ ] Empaquetado EPUB: `package_epub_from_oeb` para comprobar que la colocación se mantiene al crear el `.epub`.
- [x] Empaquetado EPUB: `package_epub_from_oeb` para comprobar que la colocación se mantiene al crear el `.epub`.
- [x] Pruebas end-to-end (sin GUI): preview → enrich → package → inspección HTML dentro del EPUB para p.67 y p.76.

Aceptación Bloque A
- [ ] No quedan intentos textuales de fórmulas/tablas junto a las figuras insertadas.
- [ ] Todas las imágenes aparecen en el orden de lectura correcto dentro de su página (segmento) y respetan el tipo (mathblock/tableblock).
- [ ] El EPUB empaquetado conserva la maquetación sin desplazamientos extraños.

Bloque B — Tratamiento específico de fórmulas inline (posterior)

Detección de inline
- [ ] Criterios para “inline”: bbox totalmente contenido dentro de un párrafo de texto (segmento layout “text” que nuestro algoritmo marca) y altura relativa pequeña vs. línea.
- [ ] Postprocesado: marcar estas cajas como `mathinline` y separarlas del flujo `mathblock`.

Anclaje y colocación exacta
- [ ] Extracción de texto de contexto alrededor del bbox (antes/después) desde PDF con coordenadas (pdfminer/pdftotext -bbox).
- [ ] Normalización (hyphenation, ligaduras, espacios, NFKC) de texto PDF y HTML.
- [ ] Búsqueda aproximada en HTML (RapidFuzz) de las anclas “antes/después” y colocación de `<img class="mathinline">` exactamente entre ambos tokens.
- [ ] Eliminación del intento textual de la fórmula inline (solo el tramo correspondiente, no el párrafo entero).
- [ ] Fallback cuando falla el anclaje: inserción por “líneas virtuales” dentro del párrafo basada en longitud acumulada y ratio y/page_height.

Integración y pruebas para inline
- [ ] Extender `FigureSpec` con `before_text`/`after_text` y usarlo con prioridad en `insert_figures_inline`.
- [ ] Tests sintéticos de anclaje exacto y con hyphenation/ligaduras.
- [ ] Pruebas en Mastering.pdf en una página con fórmulas inline (a identificar en el dataset) confirmando que la imagen queda entre las palabras correctas.

Operativa y toggles en la GUI
- [x] Parámetro de DPI (360–420) y control de compresión PNG/JPEG.
- [x] Toggle “eliminar texto de ecuación” (bloque/inline) con umbrales configurables.
- [x] Registro de métricas: número de cajas, cobertura por página, método de colocación usado (anchor/ratio/fallback).

Notas de implementación
- Mantener el código modular (assemble/enrich/postprocess) y tests unitarios pequeños antes de integrar con Mastering.pdf.
- Validar cada paso con fixtures sintéticos antes de usar PDFs reales.
