# miCombi · Rutas de micro, paraderos y horarios

Parte del proyecto **JuliacaBus-DS**, recomendador de rutas de transporte urbano
para Juliaca y San Miguel (San Román, Puno, Perú).

En Juliaca no hay paraderos formales: la micro para en cualquier esquina. Por
eso aquí cada **esquina** (cruce de calles) se trata como un **paradero**.

Este proyecto toma el recorrido GPS de una micro, lo **ajusta al eje de las
calles** de OpenStreetMap (*map matching*), marca cada esquina como paradero y
calcula **a qué hora pasó** por cada una y **dónde se detuvo**.

## Rutas

| Línea | Sentido | Ruta | Horario | Km | Paraderos | Paradas | Vel. media |
|---|---|---|---|---|---|---|---|
| LM-3 | ida | Ayabacas → Bosque | 09:00 – 10:20 | 18,76 | 172 | 40 | 14,1 km/h |
| LM-3 | vuelta | Bosque → Ayabacas | 10:30 – 11:39 | 18,34 | 170 | 33 | 16,0 km/h |
| L23 | ida | Molina → Escuri | 14:00 – 14:54 | 13,75 | 152 | 29 | 15,3 km/h |
| L23 | vuelta | Escuri → Molina | 15:05 – 16:06 | 13,10 | 148 | 38 | 12,9 km/h |
| L1 | ida | Rinconada → Guadalupe | 18:00 – 18:55 | 8,04 | 106 | 32 | 8,8 km/h |
| L1 | vuelta | Guadalupe → Rinconada | 19:05 – 19:53 | 7,93 | 103 | 30 | 9,9 km/h |

## Flujo

```
 gpx.studio                 2_generar_recorridos.py            3_procesar_rutas.py
┌───────────────┐          ┌────────────────────────┐        ┌──────────────────────────┐
│ trazo dibujado│  ──────► │ recorrido GPS (ANTES)  │ ─────► │ ruta procesada (DESPUÉS) │
│ pocos puntos  │          │ punto cada 2 s, horas, │        │ eje de las calles,       │
│               │          │ paradas, error de GPS  │        │ paraderos y horarios     │
└───────────────┘          └────────────────────────┘        └──────────────────────────┘
 data/gpx_dibujado/          data/gpx_entrada/                  data/salida/
```

## Contenido

```
miCombi/
├── 1_descargar_mapa.py      descarga las calles de OpenStreetMap
├── 2_generar_recorridos.py  trazo dibujado -> recorrido GPS
├── 3_procesar_rutas.py      recorrido GPS -> ruta sobre las calles + paraderos + horarios
├── visor.py                 aplicación para ver el antes y el después
├── rendimiento.py           mide tiempos y lee los datos del equipo
├── config.json              parámetros generales
├── requirements.txt
├── .streamlit/config.toml   colores del visor
├── .vscode/                 opciones para ejecutar con F5
└── data/
    ├── gpx_dibujado/        rutas dibujadas en gpx.studio
    ├── gpx_entrada/         recorridos GPS
    ├── parametros_rutas/    horario y comportamiento de cada recorrido
    ├── mapa/                red de calles (juliaca_red.graphml.gz, 1,2 MB)
    └── salida/
        ├── resumen_rutas.csv          antes / después de todas las rutas
        ├── esquinas/                  paraderos (CSV) y ruta procesada (GPX)
        ├── horarios/                  hora de paso por cada paradero (CSV)
        ├── ejecucion/                 tiempos de ejecución y REPORTE.md
        └── mapas/                     mapas HTML (no se suben; se regeneran)
```

Todo el repositorio pesa menos de 3 MB. El mapa de calles va comprimido (de 14 MB
pasa a 1,2 MB), así que los pasos 2 y 3 funcionan sin internet.

## Instalación (Windows)

Necesitas Python 3.10 o más reciente. Abre la carpeta en VS Code y en la terminal
(PowerShell) escribe:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Si PowerShell no deja activar el entorno, ejecuta una vez:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

En VS Code, elige el intérprete `.venv` con `Ctrl+Shift+P` → *Python: Select Interpreter*.

## Uso

Con el entorno activado:

```powershell
streamlit run visor.py
```

Los resultados ya están en `data/salida/`, así que el visor abre directamente.
Para volver a generar todo:

```powershell
python 1_descargar_mapa.py
python 2_generar_recorridos.py
python 3_procesar_rutas.py
```

En VS Code también puedes usar **F5** y elegir el paso en la lista.

### 1. Mapa (`1_descargar_mapa.py`)

Descarga la red vial de Juliaca y San Miguel con OSMnx y la guarda comprimida.
Si ya existe no la vuelve a descargar; para actualizarla: `python 1_descargar_mapa.py --forzar`.

### 2. Recorridos GPS (`2_generar_recorridos.py`)

Toma cada trazo de `data/gpx_dibujado/` y su archivo `data/parametros_rutas/<ruta>.json`
y produce el recorrido con un punto cada 2 s en `data/gpx_entrada/<ruta>.gpx`.

```json
{
  "ruta": "RINCONADA-GUADALUPE-L1",
  "linea": "L1",
  "sentido": "ida",
  "fecha": "2026-10-06",
  "hora_salida": "18:00:00",
  "tiempo_total_min": 55,
  "pasaje_soles": 1.0,
  "prob_parada": 0.3,
  "pausa_parada_seg": [5, 45],
  "variacion_velocidad": 0.15,
  "factor_trafico_centro": 1.9,
  "desvio_vereda_m": [2, 4],
  "ruido_gps_m": 2.5,
  "intervalo_seg": 2,
  "semilla": 4247240216
}
```

| Campo | Qué hace |
|---|---|
| `hora_salida`, `tiempo_total_min` | el recorrido empieza a esa hora y dura exactamente eso |
| `prob_parada` | probabilidad de que la micro pare en cada esquina |
| `pausa_parada_seg` | duración mínima y máxima de cada parada |
| `factor_trafico_centro` | cuánto más lento va dentro de `zona_centro` (config.json) |
| `variacion_velocidad` | cada tramo puede ir hasta ese porcentaje más rápido o más lento |
| `desvio_vereda_m` | cuánto se cierra a la derecha al recoger pasajeros |
| `ruido_gps_m` | error típico del GPS, en metros |
| `intervalo_seg` | cada cuántos segundos hay un punto |
| `semilla` | número que fija el azar de la ruta: con la misma semilla sale siempre el mismo recorrido |

Cada recorrido incluye paradas en esquinas reales, arranque y frenado (unos 8 s),
menos velocidad en las curvas y en el centro, carril derecho, desvío hacia la vereda
antes de cada parada y un error de GPS de unos 2,5 m que cambia de a poco entre un
punto y el siguiente (proceso autorregresivo AR(1)).

### 3. Procesamiento (`3_procesar_rutas.py`)

| Paso | Qué se hace | Herramienta |
|---|---|---|
| Lectura | Puntos, horas y alturas del GPX (`<trk>`, `<rte>` o `<wpt>`) | gpxpy |
| Proyección | De grados a metros (UTM 19 S, EPSG:32719) | GeoPandas / pyproj |
| Limpieza | Quita puntos repetidos y saltos de más de 90 km/h | NumPy |
| Muestreo | Un punto cada 20 m a lo largo del recorrido | Shapely |
| Candidatos | Calles a menos de 35 m de cada muestra | índice espacial R-tree |
| **Ajuste a las calles** (*map matching*) | Modelo oculto de Markov + algoritmo de Viterbi (Newson y Krumm, 2009) | NetworkX |
| Reconstrucción | Se une el eje de las calles elegidas; se quitan picos de ida y vuelta de menos de 15 m | Shapely |
| Tramos sin calle | Si el mapa no tiene esa calle, el pedazo se conserva enderezado (Douglas-Peucker) | Shapely |
| Paraderos | Cada esquina (nodo con 3 o más calles) por donde pasa la ruta; se unen las de menos de 25 m | OSMnx |
| Horarios | Cada punto GPS se ubica sobre la ruta (referenciación lineal) y se interpola la hora de paso por cada esquina | NumPy |
| Paradas | Tiempo a menos de 3 km/h y a menos de 20 m de una esquina | NumPy |

**Cómo funciona el ajuste a las calles.** Para cada muestra del recorrido se buscan
las calles cercanas (candidatas). Cada candidata recibe un puntaje por lo cerca
que está del punto GPS (*emisión*, distribución normal con σ = 7 m). Entre una muestra
y la siguiente se compara la distancia yendo por las calles (Dijkstra) con la
distancia en línea recta: si se parecen, el cambio es probable (*transición*,
β = 15 m). El algoritmo de Viterbi elige la secuencia de calles con el mejor
puntaje total. Así, aunque el GPS se desvíe unos metros, la ruta queda en la
calle correcta y no salta a calles paralelas.

| Salida | Contenido |
|---|---|
| `salida/esquinas/<ruta>_esquinas.csv` | ruta, orden, id_esquina, id_nodo_osm, lat, lon, dist_acumulada_m, calles, es_terminal |
| `salida/esquinas/<ruta>_procesada.gpx` | la ruta sobre el eje de las calles y cada paradero como punto |
| `salida/horarios/<ruta>_horarios.csv` | lo anterior + hora_llegada, seg_desde_salida, hubo_parada, pausa_seg, velocidad_tramo_kmh |
| `salida/mapas/<ruta>_mapa.html` | mapa doble antes / después (se abre con doble clic) |
| `salida/resumen_rutas.csv` | comparación antes / después de todas las rutas |

### Visor (`visor.py`)

- **Antes y después**: dos mapas que se mueven juntos. A la izquierda los puntos del
  GPS; a la derecha la ruta procesada con sus paraderos (relleno = la micro se detuvo).
- **Paraderos y horarios**: la tabla de esquinas con su hora y descargas en CSV y GPX.
- **Velocidad**: velocidad según el GPS y kilómetros recorridos en el tiempo.
- **Reproducir viaje**: la micro avanzando en el mapa con su hora.
- **Método**: los pasos y parámetros del procesamiento.
- **Todas las líneas**: las seis rutas juntas, cada línea con su color.
- **Ejecución**: el equipo usado y cuánto tardó cada paso y cada ruta.

Los mapas tienen fondo de calles en gris (para que resalten las rutas) o satelital.

## Resultados de ejecución

Cada script mide cuánto tarda cada parte y lo guarda en `data/salida/ejecucion/`
(un JSON por paso). Con todo eso arma [`REPORTE.md`](data/salida/ejecucion/REPORTE.md),
con tablas listas para informes. También se ven en el visor, en **Ejecución**.

| | |
|---|---|
| Equipo | Lenovo LOQ 15APH8 (82XT), laptop |
| Procesador | AMD Ryzen 7 7840HS (8 núcleos / 16 hilos) |
| Memoria RAM | 16 GB |
| Sistema operativo | Windows 11 |
| Python | 3.13 |

Promedio de 5 ejecuciones:

| Paso | Tiempo total | Por ruta (promedio) | Memoria máxima |
|---|---|---|---|
| 2. Generar recorridos GPS (6 rutas, 11 016 puntos) | 2,34 ± 0,16 s | 0,18 s | 357 MB |
| 3. Procesar rutas (6 rutas, 11 016 puntos) | 6,95 ± 0,19 s | 0,60 s | 366 MB |

En el paso 3, unos 3,4 s son de preparar el mapa (una sola vez) y el resto es el
procesamiento: entre 0,43 y 0,87 s por ruta, unos 3 000 puntos GPS por segundo.
Los tiempos no incluyen el arranque de Python y cambian un poco en cada ejecución.

## config.json

| Clave | Valor | Para qué |
|---|---|---|
| `lugares`, `bbox_respaldo`, `tipo_red` | Juliaca y San Miguel, `drive` | qué mapa se descarga |
| `radio_busqueda_m` | 15 | distancia del trazo dibujado a una esquina (paso 2) |
| `distancia_min_entre_esquinas_m` | 25 | esquinas más cercanas se unen (avenidas con berma central) |
| `max_retroceso_m` | 150 | corrige el trazo dibujado cuando se pasa de una esquina y regresa |
| `semilla_aleatoria` | 42 | semilla base si una ruta no tiene la suya |
| `zona_centro` | recuadro | zona con más tráfico |
| `ajuste_calles` | 20 / 35 / 6 / 7 / 15 | muestreo, radio de candidatos, máximo de candidatos, σ y β del ajuste |
| `lineas` | colores | color de cada línea en los mapas |

## Descargar el proyecto

```powershell
git clone https://github.com/shonales/miCombi.git
cd miCombi
```

Luego sigue los pasos de **Instalación**. El repositorio no incluye el entorno
`.venv`, la caché de descargas ni los mapas HTML (se generan con `3_procesar_rutas.py`).

## Problemas comunes

| Mensaje | Solución |
|---|---|
| `No se encontró el mapa` | ejecuta `python 1_descargar_mapa.py` |
| `No se pudo descargar` | revisa la conexión a internet |
| `no tiene suficientes puntos` | el GPX está vacío o tiene un solo punto |
| `no es un GPX válido` | vuelve a exportar el archivo desde gpx.studio |
| `... m del recorrido van por donde el mapa no tiene calle` | esa calle falta en OpenStreetMap; el pedazo se conserva enderezado |

Mapas © colaboradores de OpenStreetMap (ODbL) y Esri (imagen satelital).
