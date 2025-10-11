- [x] Cambiar la paleta de colores a dark nordic blue
- [x] Añadir botón para cambio de tamaño de letra y persistir estos cambios de configuración entre runs.
- [x] Arreglar que para ver la descripción sale "\u2" o algo así en lo que entiendo que debería ser un icono me parece.
- [x] Look more modern. Hay botones y campos que no respetan la paleta de color nordic blue.
- [x] Mejorar la previsualización: permitir navegar todo el spine del OEB para ver cada capítulo tal y como quedará en el EPUB final.
- [x] Botón para cancelar una ejecución larga de previsualización o de exportar.
- [x] El scroll del ratón no funciona cuando tengo el puntero sobre la sección de las opciones a seleccionar. Solo si pongo el puntero encima del scroll, me funciona con la rueda del ratón. Esto no ocurre por ejemplo con los scrolls del visor y de la consola.
- [x] Mejorar los presets definidos por defecto (ya daré más indicaciones de cómo)
- [x] Poder seleccionar varios presets a la vez (si hay 2 presets que tocan la misma opción, podria añadir un warning con la resolución de conflicto, eligiendo la opción de uno o de otro)
- [x] Arreglar la función de import CLI. Por ejemplo, falla con (debería adaptar el nombre in al que yo seleccione, lo único que debe coger del comando son las opciones, no el nombre del input o del output):
ebook-convert in.pdf out.epub \
  --output-profile kobo --epub-version 3 \
  --pdf-engine=pdftohtml \
  --change-justification=original \
  --keep-ligatures --embed-all-fonts \
  --disable-unwrap-lines --dont-split-on-page-breaks \
  --minimum-line-height 1.25
- [x] CUando selecciono muchas opciones del preset el recuadro se hace demasiado grande y se sale de la pantalla el botón de aceptar. Añadir un scroll a la descripción de los presets seleccionados.
- [x] Limpiar opciones no soportadas o actualizar la versión de mi cli. Por qué tengo opciones no soportadas?
- [x] Añadir opción de borrar una configuración (eliminarla y si solo hay 1 abierta, que se quede como una nueva)
- [x] Añadir una forma de actualizar calibre o instalarlo de nuevo.
- [ ] Cuando miro las hints que se encuentran cerca del borde inferior de la app, se salen de la pantalla porque siempre renderizan hacia abajo, nunca hacia arriba o hacia al lado, aunque puedan no caber.
- [ ] Mejorar la implementación de fórmulas matemáticas.
- [ ] Mejorar la UI: en lugar de enseñar un montón de opciones y resaltar qué hace cada una, podría ser más interesante diseñar preguntas sobre el documento y dar opciones de respuesta para que en base a esas respuestas se seleccionen las opciones. Así es mucho más interpretable para alguien que no conoce la herramienta. Algo así como un formulario con opciones.
- [ ] Añadir icono a la app.
- [ ] En flags detectadas me sale --paragraph-type y segun el help, lo puedo poner en auto, pero entonces me salta el siguiente error:
"""Ejecutando previsualización…
[stderr] Usage: ebook-convert input_file output_file [options]
[stderr] Convert an e-book from one format to another.
[stderr] input_file is the input and output_file is the output. Both must be specified as the first two arguments to the command.
[stderr] The output e-book format is guessed from the file extension of output_file. output_file can also be of the special format .EXT where EXT is the output file extension. In this case, the name of the output file is derived from the name of the input file. Note that the filenames must not start with a hyphen. Finally, if output_file has no extension, then it is treated as a folder and an "open e-book" (OEB) consisting of HTML files is written to that folder. These files are the files that would normally have been passed to the output plugin.
[stderr] After specifying the input and output file you can customize the conversion by specifying various options. The available options depend on the input and output file types. To get help on them specify the input and output file and then use the -h option.
[stderr] For full documentation of the conversion system see
[stderr] https://manual.calibre-ebook.com/conversion.html
[stderr] Whenever you pass arguments to ebook-convert that have spaces in them, enclose the arguments in quotation marks. For example: "/some path/with spaces"
[stderr] ebook-convert: error: no such option: --paragraph-type
[ERROR] ebook-convert finalizó con código 2.

Comando: ebook-convert /tmp/pdf2epub-puupsp__/Mastering-subset.pdf /tmp/pdf2epub-puupsp__/preview-oeb --base-font-size 12 --chapter-mark pagebreak --disable-remove-fake-margins --embed-all-fonts --enable-heuristics --font-size-mapping 8,9,10,11,12,13,14,16 --keep-ligatures --linearize-tables --minimum-line-height 1.3 --output-profile kobo --paragraph-type auto --remove-paragraph-spacing --remove-paragraph-spacing-indent-size 1.2 --subset-embedded-fonts

stdout: <vacío>

stderr:
Usage: ebook-convert input_file output_file [options]

Convert an e-book from one format to another.

input_file is the input and output_file is the output. Both must be specified as the first two arguments to the command.

The output e-book format is guessed from the file extension of output_file. output_file can also be of the special format .EXT where EXT is the output file extension. In this case, the name of the output file is derived from the name of the input file. Note that the filenames must not start with a hyphen. Finally, if output_file has no extension, then it is treated as a folder and an "open e-book" (OEB) consisting of HTML files is written to that folder. These files are the files that would normally have been passed to the output plugin.

After specifying the input and output file you can customize the conversion by specifying various options. The available options depend on the input and output file types. To get help on them specify the input and output file and then use the -h option.

For full documentation of the conversion system see
https://manual.calibre-ebook.com/conversion.html

Whenever you pass arguments to ebook-convert that have spaces in them, enclose the arguments in quotation marks. For example: "/some path/with spaces"

ebook-convert: error: no such option: --paragraph-type"""

