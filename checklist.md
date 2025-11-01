Checklist — Preprocesado PDF con sustitución de fórmulas y tablas por imágenes

Objetivo general
- Transformar el pipeline para que, antes de llamar a Calibre, cada región detectada como fórmula o tabla en el PDF sea rasterizada a imagen y sustituya al contenido vectorial/texto original.

Preparación de detección
- [x] Normalizar la rasterización por página: generar PNGs consistentes (pdftocairo o fitz) con DPI conocido y registrar `cropbox`, `mediabox`, `rotate`.
- [x] Ejecutar PaddleOCR layout + TSR sobre cada PNG. Guardar bounding boxes en píxeles junto a metadatos (página, puntuación, etiqueta).
- [x] Filtrar cajas triviales y fusionar tablas de layout y TSR (IoU ≥ umbral). Mantener etiqueta `math` vs `table`.
- [x] Detectar regiones ya rasterizadas en el PDF (imágenes existentes) y marcar cajas coincidentes para omitirlas.

Mapeo de coordenadas
- [x] Calcular `scale = Wpx / width_pt` para cada página rasterizada (usar `cropbox`).
- [x] Convertir cada bbox detectada de píxeles a puntos aplicando la inversión de rotación (`/Rotate`) y el eje Y invertido.
- [x] Expandir ligeramente las cajas (márgenes configurables) en puntos para capturar trazos o ornamentos.

Captura de imagen por región
- [x] Implementar renderizado selectivo de una región PDF (`rect_pt`) a bitmap con DPI 300–600, preservando transparencia opcional.
- [x] Generar XObject de imagen con la resolución calculada (`ceil(w_pt*dpi/72)`, `ceil(h_pt*dpi/72)`).
- [x] Guardar imágenes temporalmente con metadatos (página, rect_pt, dpi, etiqueta).

Redacción del contenido original
- [x] Implementar utilidades para eliminar texto (`Tj`, `TJ`, `Tf`+`T*`) que intersecte `rect_pt` empleando operadores gráficos.
- [x] Eliminar gráficos vectoriales (líneas de tablas: `m`, `l`, `re`, `c`, `S`, `f`, etc.) dentro del rectángulo mediante análisis de path.
- [x] Para tablas complejas, aplicar anotaciones de redacción (`/Annots` `/Redact`) y ejecutar `apply_redactions` como fallback.
- [x] Validar que tras la redacción no queda contenido seleccionable ni vectorial en la región.

Inserción de imágenes en el PDF
- [x] Pintar fondo blanco en `rect_pt` (para evitar transparencia sobre contenido residual).
- [x] Insertar el XObject `/Image` con CTM que mapee `[0,Wpx]×[0,Hpx]` a `rect_pt`.
- [x] Ajustar el orden en el content stream para respetar la posición de lectura y mantener otros elementos intactos.
- [x] Añadir metadatos opcionales (por ejemplo `/Subtype /Form` envolviendo la imagen) para trazabilidad.

Control de calidad y pruebas
- [ ] Verificar que la selección de texto sobre las zonas procesadas es vacía en un visor PDF.
- [ ] Ejecutar conversión Calibre → EPUB y comprobar que las regiones aparecen como `<img>` en el HTML resultante.
- [ ] Añadir pruebas automáticas con PDFs sintéticos (fórmula, tabla con líneas, tabla como imagen) validando redacción e inserción.
- [ ] Caso borde: detectar y omitir regiones que ya eran imágenes originales.

Integración en la pipeline
- [ ] Encapsular el preprocesado en un módulo (`pdf_preprocessor.py`) que reciba el PDF original y produzca un PDF modificado temporal.
- [ ] Actualizar el flujo principal para usar el PDF preprocesado como entrada de Calibre, manteniendo compatibilidad con la GUI.
- [ ] Añadir toggles/configuración para DPI, márgenes, y opción “saltar regiones que ya son imágenes”.
- [ ] Registrar métricas del preprocesado (n.º de fórmulas/tablas sustituidas, DPI usado, tiempo por página).

Documentación y soporte operativo
- [ ] Documentar en README/tool_description la nueva etapa y sus parámetros.
- [ ] Incluir guía de resolución de problemas (p.ej. cómo ajustar DPI o márgenes si Calibre recorta mal).
- [ ] Actualizar la GUI para mostrar estado del preprocesado y advertencias (regiones omitidas, errores de redacción).

