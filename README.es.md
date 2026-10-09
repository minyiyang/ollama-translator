# Ollama Translator

[![CI](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml/badge.svg)](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [Français](README.fr.md) | **Español** | [Deutsch](README.de.md) | [한국어](README.ko.md)

Un pipeline local y reanudable para traducir libros enteros, pensado para
prosa literaria del inglés al chino y del chino al inglés. Recibe un archivo
EPUB, RTF, de texto, Markdown, HTML, Word (.docx) o un PDF con texto y genera
un EPUB traducido, que también puede escribirse como Word, HTML, Markdown o
texto. Traduce además archivos de subtítulos (.srt, .vtt, .ass), subtítulo
por subtítulo y sin tocar los tiempos. Todo se ejecuta con modelos locales
de Ollama: sin API en la nube, sin servidor MCP y sin frameworks de agentes
como LangGraph o AutoGen.

**Pares de idiomas.** Inglés a chino simplificado (`en-zh`) y el sentido
inverso (`zh-en`) son los pares ajustados: en ellos funcionan todas las
comprobaciones, el glosario, la hoja de estilo y la reescritura de la prosa.
Francés (`fr`), japonés (`ja`), español (`es`), alemán (`de`) y coreano (`ko`)
tienen perfil propio (convenciones de puntuación, fórmulas de tratamiento,
numerales y ejemplos para los prompts), como idioma de origen o de destino y
en combinación con cualquier otro. El resto de idiomas se acepta en el nivel
genérico: las comprobaciones que el idioma no admite se omiten y la calidad
depende del modelo. El par se indica con `translation.direction: en>ja`
(códigos BCP 47) o, en el panel, con el ajuste *Idiomas* del trabajo.

## Qué problema resuelve

La traducción de un libro entero falla de maneras que una sola llamada al
modelo no puede arreglar. Al traducir capítulo a capítulo directamente con un
LLM suele ocurrir lo siguiente:

- **Deriva terminológica**: el mismo nombre de persona o de lugar se traduce
  de una forma en el capítulo 3 y de otra en el 20;
- **Estructura dañada**: etiquetas en línea como `<em>` o `<i>` se pierden al
  reescribir y el EPUB se abre con el formato roto;
- **Cantidades alteradas**: cifras, unidades, distancias y horas cambian sin
  aviso, y a simple vista es difícil notarlo;
- **Reparaciones que empeoran**: el modelo «corrige» una frase que estaba bien
  y sustituye una buena traducción por otra peor;
- **Una interrupción obliga a empezar de cero**: si falla en el capítulo 40,
  hay que repetirlo todo.

Este proyecto trata cada uno de esos problemas en una etapa independiente,
con su propio punto de control, en lugar de confiar en un prompt más largo.

## Cómo funciona

```text
decompile
  -> extract_glossary -> resolve_glossary -> approve_glossary
  -> build_story_context (optional)
  -> preprocess -> translate
  -> audit_translation -> audit_consistency -> repair_translation
  -> reprose_translation
  -> review_repaired -> repair_review -> validate_repaired
  -> translate_title -> compile -> validate_epub
```

Cada etapa guarda sus artefactos en un espacio de trabajo propio, bajo
`runs/<job-id>/`. `resume` omite las etapas que ya terminaron y pasaron la
validación, de modo que un trabajo interrumpido continúa desde la primera
etapa afectada y no desde el principio.

## Ejemplo: *Alicia en el país de las maravillas*

El libro de muestra del Proyecto Gutenberg incluido en el repositorio recorre
el flujo completo, del inglés al chino simplificado. Todos los pasos se hacen
en el panel local, desde el navegador:

```powershell
book-agent ui          # http://127.0.0.1:8765/
```

> La interfaz del panel todavía está cambiando, así que por ahora no hay
> capturas de pantalla; se volverán a tomar cuando se estabilice.

**1. Configurar e iniciar.** La página Trabajos lista todos los trabajos, diez
por página. *Añadir trabajo* solo pide el libro de origen (elige un archivo
conocido, búscalo o arrástralo), un nombre de archivo de configuración y un ID
de trabajo, que por defecto es el nombre de la configuración; una
configuración existente, como
[`configs/demo-alice.yaml`](configs/demo-alice.yaml), se reutiliza, y un
nombre nuevo parte de `config.example.yaml`. El trabajo se abre en su pestaña
**Configuración**. *Opciones* agrupa los ajustes que más se cambian (estilo de
traducción, modelo de cada rol con su estado de instalación ✓/✗, modo de
aprobación del glosario, comprobaciones de calidad, salida); *Todos los
ajustes* muestra cada campo de configuración con su tipo, límites, valor por
defecto y un botón para restablecerlo; *YAML* edita el archivo directamente, y
las tres vistas se mantienen sincronizadas. *Validar* guarda el archivo, lo
comprueba, hace una ejecución de prueba y pregunta a Ollama si todos los
modelos están instalados. *Iniciar traducción*, en la cabecera del trabajo,
solo se activa mientras la configuración no haya cambiado desde que pasó la
validación. Durante la ejecución, la cabecera ofrece *Pausar* (espera a que
termine la llamada al modelo en curso) y *Detener* (corta de inmediato); en
ambos casos *Reanudar* continúa desde el último punto de control. La
configuración de ejemplo usa solo modelos instalados localmente, activa la
reescritura de la prosa y la limpieza del EPUB, y deja que el modelo apruebe
el glosario. El mismo trabajo se lanza desde una terminal con
`.\scripts\demo-alice.ps1` (`-Resume` lo reanuda).

**2. Aprobación del glosario.** Con `workflow.llm_glossary_review: true`, las
entradas con evidencia, confianza alta y sin conflictos se aprueban
directamente y el resto pasa al revisor LLM; la página Glosario muestra los
términos aprobados, cuáles revisó el modelo, por qué y qué cambió. Con
revisión humana, el trabajo se pausa aquí y la misma página se convierte en
un editor: corrige la traducción de un término, su categoría, su nota o sus
alias, recházalo, filtra las entradas marcadas (palabras genéricas,
traducciones compartidas, confianza baja, sin evidencia) y lee las frases del
original en las que aparece cada término. Aprueba tu versión o envíala (o
envía el borrador intacto) al revisor LLM; en cualquier caso el pipeline se
reanuda.

**3. Traducción, auditoría y reparación.** La página Progreso sigue el trabajo
en vivo, se haya iniciado desde el panel o desde una terminal: cada etapa con
su estado, tareas de modelo previstas, unidad actual, llamadas, tokens de
salida y tiempo, además de la llamada al modelo en curso y el registro de la
sesión. Con este libro, el pipeline se pausó antes de compilar con tres
segmentos que no pudo verificar por sí solo.

**4. Revisión final.** La página Revisión final (que también se abre con
`book-agent review-ui .\runs\demo-alice-en-zh`) recorre esa cola de revisión.
Cada segmento muestra el original con su contexto, los hallazgos (haz clic en
uno para resaltar las palabras citadas), las versiones anteriores del
pipeline y un editor con diff en vivo y la misma comprobación determinista
que aplica `resolve-review`. Al aplicar las decisiones se aprueba el
borrador; después se compila y se valida el EPUB. Un borrador que espera
aprobación con la cola vacía se aprueba ahí mismo con **Aprobar borrador
final**.

**5. Leer y editar el libro entero.** La pestaña **Texto** muestra cada
capítulo en filas emparejadas de original y traducción. Cualquier segmento se
puede editar en el sitio, con un motivo, la misma comprobación determinista y
un diff. Cada cambio, incluida cada decisión de Revisión final, es un evento
en un único registro de ediciones que ninguna reejecución borra, así que se
puede revertir y consultar su historial. Si una reejecución cambia la
traducción de un segmento editado, este pasa a ser un conflicto que te toca
resolver. *Recompilar* reconstruye el libro con las ediciones en segundos.
**⤓ Exportar XLIFF** entrega el libro a una herramienta TAO, y
**⤒ Importar XLIFF** trae de vuelta el archivo del traductor con una vista
previa antes de escribir nada.

## Principios de diseño

- **Control determinista de la estructura verificable.** La estructura del
  EPUB, los marcadores en línea y los identificadores los gestiona el código,
  no el modelo.
- **Abstenerse antes que reparar sin certeza.** Una reparación que no se puede
  verificar se descarta en lugar de sobrescribir una traducción existente
  mejor; el segmento pasa a revisión humana.
- **Reintentos acotados.** El pipeline deja de llamar al modelo cuando la
  validación ya no converge, en vez de dar vueltas sobre el mismo fallo.
- **Tratamiento genérico de los fallos.** Cada clase de fallo se resuelve con
  reglas generales; no hay parches escritos para un libro o un pasaje
  concretos.
- **Contexto asignado según la necesidad.** Los 131K de contexto configurados
  son un techo, no una reserva. Las llamadas normales empiezan en 16K, y la
  reparación dirigida y su verificación en 8K; solo crecen por 16K, 32K, 64K
  y 131K cuando hace falta, lo que mantiene la memoria de GPU proporcional a
  cada petición.

## Inicio rápido

### 1. Prepara Ollama y los modelos

Ollama debe estar ejecutándose en local. Descarga los modelos que usa la
configuración:

```powershell
ollama pull qwen3.8:latest
ollama pull gemma4:31b
```

Antes de la primera etapa que llama a un modelo, la CLI verifica los nombres
de los modelos y la capacidad de contexto configurada.

### 2. Instala

```powershell
python -m pip install -e .
book-agent --version
```

También puedes instalar con los archivos de requisitos (las dependencias
declaradas en `pyproject.toml` son la referencia):

```powershell
python -m pip install -r requirements.txt
python -m book_agent --version
```

Requiere Python 3.11 o superior y `ollama>=0.6.2`.

### 3. Copia y revisa la configuración

```powershell
copy config.example.yaml my-book.yaml
book-agent config --file .\my-book.yaml
```

`config.example.yaml` es una configuración de partida conservadora para
producción, no una lista de valores por defecto; varios de sus valores
difieren a propósito de los del código. Todas las claves se pueden omitir, y
en ese caso se usan los valores por defecto de `book_agent/config.py`.

### 4. Haz una ejecución de prueba y luego lanza el trabajo

La ejecución de prueba no escribe archivos ni contacta con Ollama:

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --dry-run
```

Si todo está en orden, inicia el trabajo:

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --job-id "my-book-en-zh"
```

El comando imprime la ruta del espacio de trabajo. Guárdala: todos los
comandos posteriores operan sobre ese directorio. Los demás formatos de
entrada y los subtítulos usan el mismo comando y el mismo pipeline.

### 5. Consulta el estado y reanuda

```powershell
book-agent status .\runs\my-book-en-zh
book-agent resume .\runs\my-book-en-zh --plain
```

`--plain` produce una salida apta para registros y no cambia el
comportamiento del pipeline.

## Roles de los modelos

El reparto por defecto es conservador, y cada etapa puede usar su propio
modelo:

| Tarea | Modelo por defecto |
|---|---|
| Extracción de candidatos al glosario | `qwen3.8:27b` |
| Resolución y revisión de la terminología | `qwen3.8:latest` |
| Traducción inicial y reparación dirigida | `qwen3.8:latest` |
| Auditoría semántica | `gemma4:31b` |
| Verificación y arbitraje de cantidades | `gemma4:26b`, con escalado a `gemma4:31b` si hace falta |
| Comparación de reparaciones y verificación acotada | `gemma4:26b` |
| Propuesta de reescritura de la prosa | `qwen3.8:latest` |
| Verificación de la reescritura de la prosa | `gemma4:31b` |

Las llamadas de auditoría y verificación que devuelven JSON estructurado
tienen el modo thinking desactivado por defecto; no conviene activarlo salvo
que se haya medido una mejora de calidad.

## Revisión y reanudación

Con el valor por defecto `require_glossary_review: true`, el proceso se pausa
tras generar el borrador del glosario y espera una aprobación humana:

```powershell
book-agent approve "D:\runs\my-job" --glossary "D:\reviews\glossary.txt" --resume
```

También puedes pedir una revisión del modelo, restringida por esquema, y
continuar de inmediato:

```powershell
book-agent approve "D:\runs\my-job" --llm-glossary --resume
```

El revisor puede corregir o eliminar entradas, pero no inventar términos de
origen, y deja registrados el hash del prompt y del modelo, los intentos, el
modo de revisión y los artefactos.

### Panel en el navegador

`book-agent ui` sirve un panel que solo escucha en la dirección de loopback
(opciones `--runs`, `--configs`, `--port`, `--no-browser`). Cubre los mismos
puntos de revisión que los comandos anteriores.

La interfaz está disponible en inglés, chino simplificado, japonés, francés,
español, alemán y coreano, los idiomas para los que el pipeline tiene perfil.
Elige uno en el menú de la derecha de la cabecera; la elección se guarda en
el navegador y el idioma por defecto es el inglés. El idioma de la interfaz
es independiente de la dirección de traducción del trabajo (véase
[Localización del panel](docs/LOCALIZATION.md), en inglés).

- **Trabajos**: lista los trabajos con su dirección de traducción (por
  ejemplo, `EN → ZH`) y crea otros nuevos. Un trabajo terminado tiene un
  botón *Descargar* para el libro traducido, también en la cabecera del
  trabajo, donde se puede elegir el formato (EPUB, Word, HTML, Markdown o
  texto). La pestaña **Configuración** de cada trabajo lo edita, lo valida y
  lo inicia. **Series** agrupa los trabajos que comparten un glosario de
  serie versionado. Desde la cabecera se pausa (tras la llamada al modelo en
  curso), se detiene o se reanuda la ejecución; `book-agent pause
  <workspace>` pausa del mismo modo un trabajo iniciado desde una terminal;
- **Progreso**: sigue cualquier trabajo de `--runs` a partir de
  `state.sqlite3` y de los registros de sesión. Una etapa fallida o en pausa
  ofrece *Reanudar* (se conserva el trabajo terminado) y *Reejecutar*; una
  completada ofrece *Reejecutar desde aquí*. Antes de una reejecución
  (`retry --stage X --resume`) se avisa de qué etapas se rehacen y qué se
  pierde. La columna *Último cambio* indica lo último que le ocurrió a cada
  etapa (iniciada, terminada, fallida, detenida, en pausa, esperando
  revisión, restablecida) y cuándo;
- **Glosario**: edita y aprueba un glosario en pausa, o lo pasa al revisor
  LLM (`approve --glossary` / `--llm-glossary`);
- **Texto**: muestra el libro capítulo a capítulo en filas emparejadas de
  original y traducción, con filtros para los segmentos marcados, en cola,
  editados o en conflicto. Cuando termina `validate_repaired`, cualquier
  segmento se puede editar en el sitio: para guardar hace falta un motivo y
  pasar la comprobación determinista (estructura o marcadores rotos, texto
  vacío, sin traducir o duplicado y errores de puntuación bloquean el
  guardado; los demás hallazgos exigen un motivo de excepción). Las ediciones
  se guardan en `edits/segment-edits.jsonl`, un registro de solo anexión que
  no pertenece a ninguna etapa, así que las reejecuciones las conservan; al
  compilar se aplican sobre el borrador validado, y *Recompilar* reconstruye
  el libro cuando hay ediciones posteriores a él. Si una reejecución cambia
  la traducción de un segmento editado, este pasa a ser un conflicto que
  bloquea la compilación hasta que conserves tu edición o aceptes el texto
  nuevo. Cada edición tiene historial y se puede revertir. **⤓ Exportar
  XLIFF** descarga el libro como XLIFF 2.1; **⤒ Importar XLIFF** muestra una
  vista previa, segmento a segmento, de un archivo traducido (las unidades se
  emparejan por ID y solo si el original no ha cambiado) y escribe únicamente
  lo que confirmes, como ediciones con un mismo motivo (véase
  [Importación de XLIFF](docs/XLIFF_IMPORT.md), en inglés);
- **Revisión final**: resuelve la cola de revisión humana con la misma
  validación que `resolve-review`; aceptar un segmento exige un motivo
  predefinido o propio. Las decisiones se escriben en el mismo registro de
  ediciones que las de la pestaña Texto, y editar en Texto un segmento en
  cola también lo resuelve. Cuando los segmentos sin decidir caben en
  `workflow.compile_max_unresolved_review_segments`, ofrece aplicar los ya
  decididos y aprobar el borrador final. `book-agent review-ui <workspace>`
  abre esta página directamente.

Si el panel encuentra un error inesperado, muestra un aviso con el mensaje,
los detalles técnicos y un botón *Recargar* en lugar de una página en blanco.

Las ediciones se pueden consultar desde la línea de comandos o exportar como
XLIFF 2.1:

```powershell
book-agent edits "D:\runs\my-job"
book-agent edits "D:\runs\my-job" --export xliff --output my-job.xlf
```

La etapa `audit_consistency` del pipeline (activa por defecto, sin llamadas
al modelo) comprueba la coherencia del libro: una frase o una línea de
diálogo que se repite debe traducirse igual en todas partes, y la puntuación
debe seguir las convenciones del propio libro. Las divergencias pasan a
reparación; lo que la reparación no resuelve entra en la cola de revisión.
Además, `consistency.style_sheet.enabled: true` extrae, junto con el
glosario, una **hoja de estilo del libro**: expresiones recurrentes, que se
traducen igual allí donde reaparecen, y notas sobre cada personaje. Una
persona la revisa en el mismo punto de revisión que el glosario (en la
sección *Hoja de estilo* de la pestaña Glosario, o con
`approve --style FILE`), aunque el glosario lo revise el modelo. Las notas
de personaje son solo contexto: los pronombres y el tratamiento (tú/usted,
你/您) los deciden la redacción del original y la escena. Con
`consistency.story_context.enabled: true` se añade la etapa opcional
`build_story_context`: un resumen breve por capítulo (unos 6 s cada uno), a
partir del cual cada fragmento que se traduce recibe la historia hasta ese
punto, solo como contexto.
Consulta [Coherencia a nivel de libro](docs/BOOK_CONSISTENCY.md) (en inglés).

Lo que el panel inicia se ejecuta como la CLI habitual en un proceso hijo,
así que los registros y los puntos de control son idénticos a los de una
ejecución en terminal, y el trabajo continúa aunque se cierre el panel.

El panel es una aplicación React + TypeScript que vive en `frontend/`. Su
compilación de producción está versionada en `book_agent/web/static/`, de
modo que instalar el paquete no requiere Node.js. Para cambiar la interfaz
(Node 24):

```powershell
cd frontend
npm ci
npm run dev      # hot-reloading UI; proxies /api to a running `book-agent ui`
npm test         # helper unit tests and component tests (jsdom + Testing Library)
npm run build    # type-check and rebuild book_agent/web/static; commit the result
```

## Códigos de salida

| Código | Significado |
|---:|---|
| `0` | El comando terminó bien o el flujo se completó |
| `1` | Fallo de validación, de configuración, de modelo, de etapa o de ejecución |
| `2` | El flujo se pausó en un punto de revisión |
| `130` | Cancelación del usuario o cancelación cooperativa de la generación |

## Pruebas

La batería de pruebas es offline por diseño: ninguna prueba contacta con
Ollama, así que no hace falta un modelo local para ejecutarla.

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

## Más documentación

La referencia completa de comandos, la descripción de cada ajuste y los
detalles de operación se mantienen en inglés:

- [README en inglés](README.md)
- [Operación en producción](docs/OPERATIONS.md)
- [Diseño](docs/DESIGN.md)
- [Propuesta de coherencia a nivel de libro](docs/BOOK_CONSISTENCY.md)
- [Localización del panel](docs/LOCALIZATION.md)
- [Control de etapas en el panel: reanudar, reejecutar y aprobación anticipada](docs/STAGE_CONTROL.md)
- [Revisión del texto completo y seguimiento de ediciones manuales](docs/FULL_TEXT_REVIEW.md)
- [Importación de XLIFF](docs/XLIFF_IMPORT.md)
- [Glosario de serie en el panel](docs/SERIES_GLOSSARY_UI.md)
- [Libros en texto, Markdown, HTML y Word](docs/FORMAT_SUPPORT.md)
- [Plan de trabajo](docs/PLAN.md)
- [Plan de benchmark de frameworks de inferencia (aún sin ejecutar)](docs/FRAMEWORK_BENCHMARK_PLAN.md)

## Licencia

El código y la documentación del proyecto se publican bajo la
[licencia MIT](LICENSE). Los EPUB de `sample/` conservan los términos del
Proyecto Gutenberg que llevan incluidos y quedan fuera de la concesión MIT;
consulta [sample/README.md](sample/README.md). Este repositorio no distribuye
los modelos de Ollama, que siguen sujetos a sus respectivas licencias.
