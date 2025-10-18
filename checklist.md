Contexto

Buscamos usar un modelo de ML de layout para detectar ecuaciones (tanto inline como en bloque) y tablas sobre las páginas renderizadas del PDF. Con los bounding boxes de cada elemento:
- Rasterizamos solo esas regiones a 360–420 dpi.
- Insertamos las imágenes en el HTML del OEB/EPUB en la misma posición del texto original.
- Eliminamos el texto correspondiente (ecuaciones y tablas) para evitar deformaciones de la conversión PDF→EPUB.

Checklist de implementación.

- [x] Render páginas a imagen con `pdftocairo -png -r 360 in.pdf out-%04d.png` (usa 420 dpi si hace falta; soporta recorte `-x -y -W -H`). 
- [x] Inferir layout cargando el detector DocLayNet elegido y generando bboxes por clase, normalizados a píxeles de la imagen.
- [x] Detectar tablas corriendo TATR para table y opcionalmente TSR; convertir a bboxes finales y unir con layout si el IoU > 0.3.
- [x] Postprocesar cajas filtrando por área mínima, fusionando solapes, expandiendo el margen 4–6 pt y etiquetando mathblock/tableblock.
- [x] Aplicar fallback por página marcándola como “rasterizar completa” si la cobertura de cajas ≥40% o el número de cajas ≥N.
- [x] Rasterizar selectivamente cada bbox con `pdftocairo -png -r 360 -x X -y Y -W W -H H in.pdf part-p%04d-b%02d.png`. Referencia: Debian Manpages.
- [x] Convertir texto con `ebook-convert in.pdf out_oeb` usando el preset Kobo sin crear EPUB.
- [x] Ensamblar OEB abriendo `content.opf`, iterando el HTML del spine e insertando `<figure><img class="mathblock"...></figure>` o `<figure class="table">…` en la posición correcta. Si el HTML no preserva página, hacer fuzzy match (p. ej., RapidFuzz) de la línea contenedora y sustituir el `<p>` completo por la `<figure>`.
- [x] Añadir CSS al OEB con `.mathblock,.table{max-width:100%;height:auto;display:block;margin:0.6em auto;page-break-inside:avoid}.`
- [x] Validar dibujando overlays para QA rápida y guardando métricas básicas (cobertura, número de cajas, tiempo).
- [x] Empaquetar opcionalmente ejecutando `ebook-convert out_oeb out.epub` al pulsar “Guardar EPUB”.
