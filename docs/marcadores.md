# Marcadores por cadena: lo que sabemos

Notas de calibración de nflcut: cómo se comporta el marcador de cada cadena,
dónde está cada cosa, qué se probó y qué falló. Si algo deja de funcionar con
un partido nuevo, empieza por aquí.

Todas las coordenadas son sobre un fotograma de 1280×720 (los vídeos se
descargan a 720p) y van en formato `(x, y, ancho, alto)`.

## La idea en una frase

El **reloj de posesión** (los 40 segundos entre jugadas) delata cuándo hay
jugada: baja antes del snap, se para o desaparece durante la jugada y vuelve a
bajar justo después del pitido. Las repeticiones y celebraciones casi nunca
llevan marcador, así que caen solas.

## Resumen por cadena

| Cadena | Modo | Marcador (`bug`) | Reloj (`clock`) | Calibrado con |
|---|---|---|---|---|
| CBS | `frozen` | `(370, 608, 540, 77)` | `(703, 649, 34, 34)` | Ravens-Cowboys, S3 |
| NBC | `frozen` | `(330, 634, 620, 36)` | `(642, 673, 28, 18)` | Rams-Broncos, S3 |
| Prime Video | `frozen` | `(345, 630, 590, 56)` | `(684, 661, 34, 24)` | Falcons-Packers, S3 |
| ESPN / ABC | `frozen` | `(290, 628, 700, 70)` | `(684, 668, 32, 24)` | Eagles-Bears, S3 |
| FOX | `hidden` | `(350, 604, 570, 76)` | `(536, 571, 60, 26)` y `(818, 571, 60, 26)` | Seahawks-Commanders, Panthers-Browns, Jets-Lions, S3 |

Configuración en `nflcut/extract.py` (`PRESETS`).

### Modo `frozen` (CBS, NBC, Prime, ESPN)

- Antes del snap el reloj baja un número por segundo.
- Durante la jugada **se congela**. Casi siempre en 40 (se reinicia en el
  snap), a veces en 25 (tras tiempos muertos o anotaciones) y a veces en el
  valor que tenía en el snap: el operador tarda en reiniciarlo y el editor de
  la NFL corta antes. Visto en NBC: ":14" congelado en una jugada en la línea
  de gol.
- Tras el pitido vuelve a bajar (39, 38…). El 40 sigue en pantalla **al menos
  1 s** después del pitido; en la práctica el pitido llega **entre 1 y 3 s
  antes** del 39, porque el operador arranca el reloj con retraso.

Regla implementada (`live_mask` en `nflcut/cut.py`):

1. "Congelado" = el recuadro no cambia en 1,2 s. Cambio = más de un 2% de los
   píxeles con diferencia > 40. Con la diferencia media no basta: pasar de 13
   a 12 cambia muy pocos píxeles.
2. Los estados congelados que se repiten a lo largo del vídeo (≥3%) son los
   de reinicio: 40, 25… Esos cuentan como jugada.
3. Un congelado en un valor "raro" solo cuenta si viene **directamente de una
   cuenta atrás** y **no ha habido un 40 en los 4 s anteriores**. Si no, es
   tiempo muerto (reloj parado por lesión, revisión o tras un field goal).

### Modo `hidden` (FOX)

FOX no enseña el reloj de posesión durante la jugada, pero tampoco durante la
mayor parte de la cuenta atrás. Solo aparece:

- **justo después del pitido** (":39", ":38"…), unos segundos, y
- **en los últimos segundos antes del snap** (":15"… ":01"; a veces desde ":2x").

La franja del down (donde va el reloj) se coloca **sobre el lado del equipo
con la bola**: visitante a la izquierda, local a la derecha. Por eso se leen
dos recuadros fijos, el final de la franja izquierda y el de la derecha, y en
cada fotograma se usa el que tenga dígitos.

Regla implementada (`fox_live`):

1. Se lee la cifra de las decenas: `high` (":3x", la jugada acaba de terminar),
   `low` (":0x" a ":2x", el snap va a llegar) o `junk` (otro texto).
2. Un tramo con el reloj oculto justo después de una lectura `low` es la
   jugada.
3. Si un tramo oculto termina en ":3x" (el reloj se acaba de reiniciar), dentro
   hubo una jugada aunque no viéramos el `low` previo: se conservan sus
   últimos 10 s (`HURRY_TAIL`).

Detalles que costó descubrir:

- **El color de la franja es el del equipo.** Seattle (azul marino) y
  Washington (granate) dan franjas oscuras; Cleveland (naranja) y Carolina
  (azul celeste), claras. La primera versión buscaba "franja oscura con
  dígitos blancos" y en Panthers-Browns no encontró **ninguna** jugada. Ahora
  los dígitos se detectan por ser **blancos** (> 200 en gris) y las plantillas
  se comparan binarizadas, así que el fondo da igual.
- **Al elegir entre los dos recuadros hay que preferir el que tiene dígitos.**
  El recuadro vacío del otro lado encaja casi perfecto con la plantilla
  "vacío" y, si se elige por menor distancia, gana siempre.
- **El final del texto del down se cuela en el recuadro** ("…GOAL",
  "…ATTEMPT"). Esas lecturas cuentan como "reloj oculto", no como tiempo
  muerto.
- **Banner amarillo "FLAG"**: si el recuadro es sobre todo brillante (> 180 en
  gris) es el banner de penalti tras la jugada, no cuenta como oculto.
- Posiciones de los dígitos dentro del recorte de 60×26: colon en las columnas
  11-17, decenas en 20-33, unidades en 34-47, filas 5-21.
- Plantillas de las decenas: `nflcut/assets/digits/fox_tens.npz` (`high`,
  `low`, `junk`), sacadas agrupando recortes de los tres partidos de FOX.

## Detección de la cadena

- Se compara la **mosca de arriba a la derecha**, región `(1040, 10, 230, 60)`,
  con una plantilla por cadena en `nflcut/assets/logos/*.npz`. La plantilla es
  la mediana de 30-40 fotogramas más una máscara con el 30% de píxeles más
  estables.
- Distancia con la propia cadena: 6-16. Con las demás: 52-92. Por encima de
  `LOGO_MAX = 35` se considera cadena no soportada.
- En la app manda la cadena que da ESPN (`broadcasts`), mapeada en
  `NETWORK_HINTS` (`nflcut/jobs.py`). La mosca solo se usa cuando no hay pista.
  **ABC usa los gráficos de ESPN**, así que va al mismo preset.
- Primer intento descartado: comparar la zona del marcador. Todas las cadenas
  lo tienen en la misma franja inferior y la diferencia queda enterrada en el
  ruido del fondo.

## Presencia del marcador

- Distancia de cada fotograma a la **mediana local** del marcador (ventana de
  120 s, `BUG_WINDOW`). Con la mediana global falla en la segunda mitad: al
  cambiar el resultado el marcador "se aleja" de la media y parece que ha
  desaparecido (visto en NBC).
- Umbral automático por Otsu sobre la distribución de distancias. En CBS da
  43,6, prácticamente igual al 40 ajustado a mano.
- **ESPN deja el marcador también en muchas repeticiones y primeros planos.**
  Ahí el reloj sigue bajando, así que la regla del reloj las descarta igual.

## Pitido del árbitro

- Se oye en **~20-50% de las jugadas** según la mezcla: 18/76 en CBS, 16/55 en
  FOX, 15/79 en NBC, 33/64 en Prime, 24/73 en ESPN.
- Aspecto: **dos líneas paralelas hacia 3,8 y 4,0 kHz** (silbato de varias
  cámaras), de 0,15-1 s.
- Detector actual (`nflcut/whistle.py`): pico tonal en 3-4,6 kHz ≥ 12 dB
  sobre la mediana de la banda, estable (deriva ≤ 60 Hz) durante ≥ 0,15 s. Se
  busca solo entre 3 s y 0,3 s antes de que el reloj salga del 40, e
  ignorando los 2 primeros segundos de la jugada. En 10 detecciones revisadas
  a mano, ~8 coincidían con el final real de la jugada.
- Cuando se oye, se corta 0,1 s después de que empiece. Si no, 1 s antes de
  que aparezca el 39 (`WHISTLE_LAG`), lo que suele dejar 0,5-1,5 s de más.

Intentos que no funcionaron:

- **Canal lateral estéreo (L−R)** para quitar a los comentaristas: bajó de
  18 a 10 detecciones. El audio de YouTube es casi mono (el lateral es ~20%
  de la señal) y el pitido también se pierde.
- **Detector de energía en banda estrecha** frente a las frecuencias vecinas:
  daba "pitido" también en casi todos los tramos de jugada en marcha (el
  ruido del estadio tiene tonos en esa franja). Tantos falsos positivos como
  aciertos.
- Ideas sin probar: separar la voz con Demucs, o un clasificador de sonidos
  entrenado (AudioSet tiene clases de silbato).

## Parámetros del corte

| Parámetro | Valor | Para qué |
|---|---|---|
| `PRE_ROLL` | 0,5 s | Margen antes del snap |
| `POST_ROLL` | 0,1 s | Margen después del pitido |
| `WHISTLE_LAG` | 1,0 s | Pitido estimado sin audio: antes del 39 |
| `MERGE_GAP` | 1,0 s | Se unen jugadas separadas por menos de esto |
| `MIN_PLAY` | 1,5 s | Se descartan tramos más cortos (ruido) |
| `FADE` | 0,08 s | Fundido de audio en cada corte |

## Resultados validados

Revisados con hojas de fotogramas: arranques de una de cada 5-7 jugadas y los
tramos descartados más largos. No se han visto los vídeos enteros.

| Partido | Cadena | Original | Recortado | Jugadas |
|---|---|---|---|---|
| Ravens-Cowboys | CBS | 20:44 | 8:30 | 77 |
| Texans-Colts | CBS | 13:48 | 5:57 | 53 |
| Seahawks-Commanders | FOX | 18:20 | 7:30 | 59 |
| Panthers-Browns | FOX | 10:34 | 4:34 | 40 |
| Jets-Lions | FOX | 17:44 | 7:23 | 71 |
| Chargers-Bills | FOX | 15:18 | 6:37 | 62 |
| Rams-Broncos | NBC | 23:42 | 8:50 | 79 |
| Falcons-Packers | Prime Video | 14:56 | 6:23 | 64 |
| Eagles-Bears | ESPN | 14:58 | 8:19 | 73 |

Todos de la semana 3 de 2026.

## Fallos conocidos

- **FOX: se pierden los kickoffs.** No se muestra el reloj ni antes ni después,
  así que no hay nada que leer. Arreglo posible: movimiento en plano ancho con
  el marcador visible.
- **ESPN: se pierde el kickoff inicial** (sin marcador en pantalla).
- **Tiempo muerto tras algunas jugadas** cuando el reloj se queda en 40: el
  árbitro anunciando un penalti o jugadores celebrando. Más frecuente en ESPN,
  que por eso sale al 55% en vez del 35-45%.
- **FOX: el banner "FLAG"** a veces sale en la parte izquierda de la franja y no
  se detecta. Se cuelan ~7 s del árbitro.
- **Field goals muy cortos** (~1 s) en Prime: del snap a la patada pasa poco más
  de un segundo; puede cortarse el vuelo del balón.
- **Final del partido:** en CBS se coló la celebración con "FINAL" en el
  marcador (~5 s).
- **Jugadas pegadas:** el editor encadena jugadas sin pausa y el reloj sigue en
  40 entre ellas, así que salen como un único tramo de 15-20 s. No sobra nada,
  pero cuenta como una jugada.

## Cómo añadir o recalibrar una cadena

1. Descargar un vídeo de highlights de esa cadena en `data/<id del partido>/src.mp4`.
2. Sacar un fotograma (`ffmpeg -ss 120 -i src.mp4 -frames:v 1 full.png`) y
   localizar el marcador y el reloj de posesión.
3. Mirar el reloj a lo largo de varias jugadas (una tira a 2 fps del recorte).
   ¿Se congela durante la jugada (`frozen`) o desaparece (`hidden`)?
4. Añadir la entrada en `PRESETS` (`nflcut/extract.py`) y, si ESPN da un nombre
   de cadena nuevo, en `NETWORK_HINTS` (`nflcut/jobs.py`).
5. Crear la plantilla de la mosca: mediana de ~40 fotogramas en `LOGO_REGION`
   más la máscara del 30% más estable, guardada en `nflcut/assets/logos/<cadena>.npz`.
   Comprobar que su distancia es claramente menor que con las demás.
6. Analizar (`cut.analyze`) y revisar:
   - arranques de una muestra de jugadas: ¿caen en el snap?
   - los tramos descartados más largos: ¿solo repeticiones, intro y celebraciones?
   - las jugadas más largas y más cortas: ¿jugadas reales o tiempo muerto?
7. Probar con al menos **dos partidos con colores de equipo distintos**: FOX
   funcionaba con uno y fallaba con otro.

## Fuentes de datos

- **Partidos:** API pública de marcadores de ESPN,
  `site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard`. No es
  oficial. **Responde 403 a casi cualquier User-Agent que no sea el de curl**;
  se usa `curl/8.7.1`.
- **Vídeos:** las últimas 200 subidas de `youtube.com/@NFL/videos`, listadas
  con `yt-dlp` (~3 s). Títulos del tipo "Away vs[.] Home Game Highlights |
  2026 NFL Season Week 3", a veces con extras ("from Rio"). Se empareja por
  los dos nombres completos de equipo más "game highlights", con preferencia
  por el número de semana.

## Spoilers que hay que evitar en la interfaz

- El **balance de victorias y derrotas** de ESPN ya viene actualizado tras el
  partido. No se muestra.
- **"Final/OT"** delata una prórroga: solo se ve al abrir el resultado.
- El **estilo del ganador** (subrayado) solo se aplica tras abrir la persiana.
- Las **miniaturas de YouTube** suelen destripar el resultado: no se usan.
