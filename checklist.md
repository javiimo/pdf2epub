Checklist de implementación.

- [ ] Render páginas a imagen con `pdftocairo -png -r 360 in.pdf out-%04d.png` (usa 420 dpi si hace falta; soporta recorte `-x -y -W -H`). Referencia: Debian Manpages.
- [ ] Inferir layout cargando el detector DocLayNet elegido y generando bboxes por clase, normalizados a píxeles de la imagen. Referencia: Hugging Face (+1).
- [ ] Detectar tablas corriendo TATR para table y opcionalmente TSR; convertir a bboxes finales y unir con layout si el IoU > 0.3. Referencia: Hugging Face (+1).
- [ ] Postprocesar cajas filtrando por área mínima, fusionando solapes, expandiendo el margen 4–6 pt y etiquetando mathblock/tableblock.
- [ ] Aplicar fallback por página marcándola como “rasterizar completa” si la cobertura de cajas ≥40% o el número de cajas ≥N.
- [ ] Rasterizar selectivamente cada bbox con `pdftocairo -png -r 360 -x X -y Y -W W -H H in.pdf part-p%04d-b%02d.png`. Referencia: Debian Manpages.
- [ ] Convertir texto con `ebook-convert in.pdf out_oeb` usando el preset Kobo sin crear EPUB.
- [ ] Ensamblar OEB abriendo `content.opf`, iterando el HTML del spine e insertando `<figure><img class="mathblock"...></figure>` o `<figure class="table">…` en la posición correcta. Si el HTML no preserva página, hacer fuzzy match (p. ej., RapidFuzz) de la línea contenedora y sustituir el `<p>` completo por la `<figure>`.
- [ ] Añadir CSS al OEB con `.mathblock,.table{max-width:100%;height:auto;display:block;margin:0.6em auto;page-break-inside:avoid}.`
- [ ] Validar dibujando overlays para QA rápida y guardando métricas básicas (cobertura, número de cajas, tiempo).
- [ ] Empaquetar opcionalmente ejecutando `ebook-convert out_oeb out.epub` al pulsar “Guardar EPUB”.
