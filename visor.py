"""
Visor de JuliacaBus-DS: compara el recorrido GPS (antes) con la ruta
procesada (después) y muestra los paraderos y sus horarios.

Uso:
    streamlit run visor.py
"""

import json
import re
from pathlib import Path

import folium
import gpxpy
import numpy as np
import pandas as pd
import streamlit as st
from folium import plugins

from rendimiento import PARTES, PASOS

BASE = Path(__file__).resolve().parent
CARPETA_DIBUJOS = BASE / "data" / "gpx_dibujado"
CARPETA_ENTRADA = BASE / "data" / "gpx_entrada"
CARPETA_SALIDA = BASE / "data" / "salida"
CARPETA_ESQUINAS = CARPETA_SALIDA / "esquinas"
CARPETA_HORARIOS = CARPETA_SALIDA / "horarios"
CARPETA_EJECUCION = CARPETA_SALIDA / "ejecucion"

ROJO_GPS = "#dc2626"
SECCIONES = ["Antes y después", "Paraderos y horarios", "Velocidad", "Reproducir viaje", "Método"]
ESTILO_MAPA = """<style>
  .calles-gris { filter: grayscale(0.85) brightness(1.05) contrast(0.9); }
  .etiqueta { background:#111827; color:white; padding:2px 6px; border-radius:4px;
              font:bold 11px sans-serif; white-space:nowrap; box-shadow:0 1px 3px #0006; }
  .leyenda { position:fixed; bottom:18px; right:12px; z-index:9999; background:white; padding:8px 10px;
             border-radius:8px; font:12px sans-serif; box-shadow:0 1px 5px #0004; line-height:1.6; }
</style>"""


# ---------------------------------------------------------------- datos

def leer_json(ruta):
    if not ruta.exists():
        return {}
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def fecha_mod(*rutas):
    return sum(r.stat().st_mtime for r in rutas if r and r.exists())


@st.cache_data(show_spinner=False)
def leer_gpx(ruta, version):
    """Puntos de la pista (o de la ruta) de un GPX como tabla."""
    with open(ruta, encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    puntos = [p for t in gpx.tracks for s in t.segments for p in s.points] or [p for r in gpx.routes for p in r.points]
    tabla = pd.DataFrame({"lat": [p.latitude for p in puntos], "lon": [p.longitude for p in puntos]})
    if puntos and all(p.time for p in puntos):
        tabla["hora"] = pd.to_datetime([p.time.replace(tzinfo=None) for p in puntos])
    return tabla


@st.cache_data(show_spinner=False)
def leer_csv(ruta, version):
    return pd.read_csv(ruta, dtype={"ruta": str, "id_nodo_osm": str}) if ruta.exists() else None


def gpx(ruta):
    return leer_gpx(ruta, fecha_mod(ruta)) if ruta and ruta.exists() else None


def csv(ruta):
    return leer_csv(ruta, fecha_mod(ruta)) if ruta and ruta.exists() else None


def archivo_crudo(ruta):
    for nombre in (f"{ruta}.gpx", f"ruta_{ruta}.gpx"):
        if (CARPETA_ENTRADA / nombre).exists():
            return CARPETA_ENTRADA / nombre
    return None


def archivo_paraderos(ruta):
    horarios = CARPETA_HORARIOS / f"{ruta}_horarios.csv"
    return horarios if horarios.exists() else CARPETA_ESQUINAS / f"{ruta}_esquinas.csv"


def archivos_de(ruta):
    return (archivo_crudo(ruta), CARPETA_ESQUINAS / f"{ruta}_procesada.gpx", archivo_paraderos(ruta),
            CARPETA_DIBUJOS / f"{ruta}.gpx")


def trayecto(ruta):
    """'RINCONADA-GUADALUPE-L1' -> 'Rinconada → Guadalupe' (quita el código de la línea)."""
    sin_linea = re.sub(r"-L[A-Z]*-?\d+$", "", ruta, flags=re.IGNORECASE)
    return " → ".join(t.title() for t in sin_linea.split("-"))


@st.cache_data(show_spinner=False)
def velocidad_gps(ruta, version):
    """Velocidad entre puntos seguidos del GPS, suavizada para que se lea mejor."""
    crudo = gpx(archivo_crudo(ruta))
    lat, lon = np.radians(crudo["lat"].to_numpy()), np.radians(crudo["lon"].to_numpy())
    a = np.sin(np.diff(lat) / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
    metros = 2 * 6_371_000 * np.arcsin(np.sqrt(a))
    segundos = crudo["hora"].diff().dt.total_seconds().to_numpy()[1:]
    kmh = pd.Series(metros / np.where(segundos > 0, segundos, np.nan) * 3.6)
    kmh = kmh.rolling(3, center=True, min_periods=1).median().rolling(5, center=True, min_periods=1).mean()
    return pd.DataFrame({"hora": crudo["hora"].iloc[1:].to_numpy(), "km/h": kmh.round(1)})


# ---------------------------------------------------------------- piezas de los mapas

def fondo(mapa):
    """Calles en gris para que resalten las rutas, y la foto satelital como opción."""
    folium.TileLayer("OpenStreetMap", name="Calles", class_name="calles-gris").add_to(mapa)
    folium.TileLayer("Esri.WorldImagery", name="Satélite", show=False).add_to(mapa)


def encuadre(tabla, ancho_px, alto_px):
    """Centro y zoom para que toda la ruta entre en un mapa de ese tamaño."""
    lat_min, lat_max = tabla["lat"].min(), tabla["lat"].max()
    lon_min, lon_max = tabla["lon"].min(), tabla["lon"].max()
    zoom_ancho = np.log2(ancho_px * 360 / (256 * max(lon_max - lon_min, 1e-4)))
    zoom_alto = np.log2(alto_px * 360 / (256 * max(lat_max - lat_min, 1e-4) * 1.04))
    return [(lat_min + lat_max) / 2, (lon_min + lon_max) / 2], int(min(zoom_ancho, zoom_alto, 18))


def linea_con_borde(puntos, color, grosor, **opciones):
    """Línea con borde blanco, para que se distinga sobre cualquier fondo."""
    grupo = folium.FeatureGroup(control=False)
    folium.PolyLine(puntos, color="white", weight=grosor + 4, opacity=0.95).add_to(grupo)
    folium.PolyLine(puntos, color=color, weight=grosor, opacity=1, **opciones).add_to(grupo)
    return grupo


def capa_puntos(tabla, color):
    """Todos los puntos GPS en una sola capa (se dibujan en canvas, carga rápido)."""
    puntos = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [round(lo, 6), round(la, 6)]}}
        for la, lo in tabla[["lat", "lon"]].values]}
    return folium.GeoJson(puntos, marker=folium.CircleMarker(radius=2, weight=0, fill=True,
                                                            fill_color=color, fill_opacity=0.8))


def capa_paraderos(paraderos, color):
    """Paraderos en una sola capa: relleno si la micro se detuvo, negro si es terminal."""
    lugares = []
    for _, f in paraderos.iterrows():
        paro = bool(f.get("hubo_parada", False))
        terminal = bool(f["es_terminal"])
        calles = f["calles"] if isinstance(f["calles"], str) else "sin nombre en OSM"
        texto = f"<b>{f['id_esquina']}</b> · {calles}"
        if "hora_llegada" in f:
            texto += f"<br>llegada <b>{f['hora_llegada']}</b>" + (f" · paró {f['pausa_seg']:.0f} s" if paro else "")
        lugares.append({"type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]},
                        "properties": {"texto": texto, "radio": 8 if terminal else 5,
                                       "borde": "#111827" if terminal else color,
                                       "relleno": "#111827" if terminal else (color if paro else "white")}})
    return folium.GeoJson(
        {"type": "FeatureCollection", "features": lugares},
        marker=folium.CircleMarker(radius=5, weight=2, fill=True, fill_opacity=1),
        style_function=lambda x: {"radius": x["properties"]["radio"], "color": x["properties"]["borde"],
                                  "fillColor": x["properties"]["relleno"]},
        tooltip=folium.GeoJsonTooltip(fields=["texto"], labels=False),
    )


def etiqueta(mapa, punto, texto):
    folium.Marker(punto, icon=folium.DivIcon(html=f"<div class='etiqueta'>{texto}</div>",
                                             icon_anchor=(-8, 10))).add_to(mapa)


def terminar(mapa, leyenda):
    folium.LayerControl(collapsed=True).add_to(mapa)
    mapa.get_root().header.add_child(folium.Element(ESTILO_MAPA))
    mapa.get_root().html.add_child(folium.Element(f"<div class='leyenda'>{leyenda}</div>"))
    return mapa.get_root().render()


# ---------------------------------------------------------------- mapas (se guardan en memoria)

@st.cache_data(show_spinner="Armando el mapa...")
def html_antes_despues(ruta, color, ver_dibujo, version):
    crudo_archivo, procesada_archivo, paraderos_archivo, dibujo_archivo = archivos_de(ruta)
    crudo, linea = gpx(crudo_archivo), gpx(procesada_archivo)

    centro, zoom = encuadre(crudo, 620, 560)
    mapa = plugins.DualMap(location=centro, zoom_start=zoom, tiles=None, prefer_canvas=True)
    fondo(mapa)
    if ver_dibujo and dibujo_archivo.exists():
        folium.PolyLine(gpx(dibujo_archivo)[["lat", "lon"]].values.tolist(), color="#6b7280", weight=2,
                        dash_array="6 6", tooltip="Trazo dibujado en gpx.studio").add_to(mapa)

    folium.PolyLine(crudo[["lat", "lon"]].round(6).values.tolist(), color=ROJO_GPS, weight=2,
                    opacity=0.5).add_to(mapa.m1)
    capa_puntos(crudo, ROJO_GPS).add_to(mapa.m1)

    puntos = linea[["lat", "lon"]].values.tolist()
    linea_con_borde(puntos, color, 5).add_to(mapa.m2)
    capa_paraderos(csv(paraderos_archivo), color).add_to(mapa.m2)
    etiqueta(mapa.m2, puntos[0], "Inicio")
    etiqueta(mapa.m2, puntos[-1], "Fin")

    return terminar(mapa,
                    f"<span style='color:{ROJO_GPS}'>●</span> punto GPS (antes)<br>"
                    f"<span style='color:{color};font-weight:bold'>━</span> ruta procesada<br>"
                    f"<span style='color:{color}'>●</span> paradero donde se detuvo<br>"
                    f"<span style='color:{color}'>○</span> paradero (esquina) sin parar<br>"
                    "● terminal")


@st.cache_data(show_spinner="Armando el mapa...")
def html_todas(rutas, version):
    """rutas: tuplas (ruta, linea, sentido, color)."""
    tablas = [(ruta, linea, sentido, color, gpx(CARPETA_ESQUINAS / f"{ruta}_procesada.gpx"))
              for ruta, linea, sentido, color in rutas]
    tablas = [t for t in tablas if t[4] is not None]
    centro, zoom = encuadre(pd.concat([t[4] for t in tablas]), 1100, 620)
    mapa = folium.Map(location=centro, zoom_start=zoom, tiles=None, prefer_canvas=True)
    fondo(mapa)
    for ruta, linea, sentido, color, tabla in sorted(tablas, key=lambda t: t[2] == "ida"):
        puntos = tabla[["lat", "lon"]].values.tolist()
        linea_con_borde(puntos, color, 5, dash_array=None if sentido == "ida" else "10 8",
                        tooltip=f"Línea {linea} · {sentido} · {trayecto(ruta)}").add_to(mapa)
        for punto, texto in ((puntos[0], "inicio"), (puntos[-1], "fin")):
            folium.CircleMarker(punto, radius=6, color="#111827", fill=True, fill_color=color, fill_opacity=1,
                                tooltip=f"{ruta} · {texto}").add_to(mapa)
    colores = {linea: color for _, linea, _, color in rutas}
    leyenda = "".join(f"<span style='color:{c};font-weight:bold'>━━</span> Línea {n}<br>" for n, c in colores.items())
    return terminar(mapa, leyenda + "━━ ida &nbsp; ╍╍ vuelta")


@st.cache_data(show_spinner="Preparando el viaje...")
def html_reproduccion(ruta, color, version):
    crudo_archivo, procesada_archivo, paraderos_archivo, _ = archivos_de(ruta)
    crudo = gpx(crudo_archivo)
    paso = crudo.iloc[::5]
    if paso.index[-1] != crudo.index[-1]:
        paso = pd.concat([paso, crudo.iloc[[-1]]])
    viaje = {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": paso[["lon", "lat"]].round(6).values.tolist()},
             "properties": {"times": paso["hora"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
                            "style": {"color": color, "weight": 5}}}

    centro, zoom = encuadre(crudo, 1100, 560)
    mapa = folium.Map(location=centro, zoom_start=zoom, tiles=None, prefer_canvas=True)
    fondo(mapa)
    folium.PolyLine(gpx(procesada_archivo)[["lat", "lon"]].values.tolist(), color="#9ca3af", weight=4,
                    opacity=0.7).add_to(mapa)
    capa_paraderos(csv(paraderos_archivo), "#6b7280").add_to(mapa)
    plugins.TimestampedGeoJson({"type": "FeatureCollection", "features": [viaje]}, period="PT10S",
                               add_last_point=True, auto_play=False, loop=False, max_speed=30,
                               loop_button=True, date_options="HH:mm:ss", time_slider_drag_update=True,
                               transition_time=100).add_to(mapa)
    return terminar(mapa, f"<span style='color:{color};font-weight:bold'>━</span> recorrido hasta la hora elegida")


# ---------------------------------------------------------------- secciones

def seccion_antes_despues(ruta, r, color, ver_dibujo):
    izquierda, derecha = st.columns(2)
    izquierda.markdown(f"**ANTES** · recorrido GPS: {int(r['puntos_antes']):,} puntos con el error normal del GPS")
    derecha.markdown(f"**DESPUÉS** · ruta sobre el eje de las calles y {int(r['paraderos'])} paraderos (esquinas)")
    st.iframe(html_antes_despues(ruta, color, ver_dibujo, fecha_mod(*archivos_de(ruta))), height=580)
    st.caption("Los dos mapas se mueven juntos. Pasa el mouse sobre un paradero para ver sus calles y su hora. "
               "Arriba a la derecha puedes cambiar a vista satelital.")

    comparacion = pd.DataFrame({
        "": ["Puntos de la línea", "Longitud", "Separación media del eje de la calle",
             "Separación máxima", "Paraderos", "Tramo sin calle en el mapa"],
        "Antes (recorrido GPS)": [f"{int(r['puntos_antes']):,}", f"{r['largo_antes_km']:.2f} km",
                                  f"{r['correccion_media_m']:.1f} m", f"{r['correccion_max_m']:.1f} m", "—", "—"],
        "Después (procesada)": [f"{int(r['puntos_despues']):,}", f"{r['largo_despues_km']:.2f} km", "0 m",
                                "0 m", str(int(r["paraderos"])), f"{int(r['fuera_de_red_m'])} m"],
    })
    st.dataframe(comparacion, hide_index=True, width="stretch")
    st.caption("El recorrido de antes mide más porque el error del GPS hace zigzag y suma metros que la micro "
               "no recorrió.")


def seccion_paraderos(ruta):
    paraderos = csv(archivo_paraderos(ruta))
    if "hora_llegada" in paraderos:
        tabla = paraderos[["orden", "id_esquina", "calles", "dist_acumulada_m", "hora_llegada",
                           "hubo_parada", "pausa_seg", "velocidad_tramo_kmh", "es_terminal"]].copy()
        tabla["dist_acumulada_m"] = tabla["dist_acumulada_m"] / 1000
        tabla["calles"] = tabla["calles"].fillna("(sin nombre en OSM)")
        st.dataframe(tabla, hide_index=True, width="stretch", height=520, column_config={
            "orden": "N°", "id_esquina": "Paradero", "calles": "Calles que se cruzan",
            "dist_acumulada_m": st.column_config.NumberColumn("Km", format="%.2f"),
            "hora_llegada": "Llegada", "hubo_parada": "¿Paró?",
            "pausa_seg": st.column_config.NumberColumn("Pausa (s)", format="%.0f"),
            "velocidad_tramo_kmh": st.column_config.ProgressColumn(
                "Vel. al siguiente (km/h)", format="%.0f", min_value=0, max_value=40),
            "es_terminal": "Terminal"})
    else:
        st.dataframe(paraderos, hide_index=True, width="stretch")

    crudo_archivo, procesada, paraderos_archivo, _ = archivos_de(ruta)
    d1, d2, d3 = st.columns(3)
    d1.download_button("Paraderos (CSV)", paraderos_archivo.read_bytes(), file_name=paraderos_archivo.name,
                       width="stretch")
    if procesada.exists():
        d2.download_button("Ruta procesada (GPX)", procesada.read_bytes(), file_name=procesada.name, width="stretch")
    if crudo_archivo:
        d3.download_button("Recorrido GPS (GPX)", crudo_archivo.read_bytes(), file_name=crudo_archivo.name,
                           width="stretch")


def seccion_velocidad(ruta, color, con_horas):
    if not con_horas:
        st.info("Este recorrido no tiene horas.")
        return
    st.markdown("**Velocidad de la micro según el GPS** · cada caída a cero es una parada")
    st.area_chart(velocidad_gps(ruta, fecha_mod(archivo_crudo(ruta))), x="hora", y="km/h", color=color, height=280)
    paraderos = csv(archivo_paraderos(ruta))
    if "hora_llegada" in paraderos:
        dia = str(gpx(archivo_crudo(ruta))["hora"].iloc[0].date())
        avance = pd.DataFrame({"hora": pd.to_datetime(dia + " " + paraderos["hora_llegada"]),
                               "km": paraderos["dist_acumulada_m"] / 1000})
        st.markdown("**Kilómetros recorridos en el tiempo** · donde la línea se aplana, la micro va lento o espera")
        st.line_chart(avance, x="hora", y="km", color=color, height=260)


def seccion_reproducir(ruta, color, con_horas):
    if not con_horas:
        st.info("Este recorrido no tiene horas.")
        return
    st.caption("Dale a ▶ en la esquina inferior izquierda del mapa. Cada paso son 10 segundos del viaje.")
    st.iframe(html_reproduccion(ruta, color, fecha_mod(*archivos_de(ruta))), height=580)


def seccion_metodo(config):
    ajuste = config.get("ajuste_calles", {})
    st.markdown(f"""
#### 1. Datos de entrada (antes)
- **Trazo dibujado** en gpx.studio: pocos puntos, sobre el eje de la calle (`data/gpx_dibujado/`).
- **Recorrido GPS** (`2_generar_recorridos.py`): se recorre el trazo con un punto cada 2 s, con:
  - horario y duración de cada viaje (`data/parametros_rutas/`),
  - paradas al azar en esquinas, más largas en hora punta,
  - aceleración y frenado (unos 8 s), menos velocidad en las curvas y en el centro de Juliaca,
  - carril derecho y desvío hacia la vereda al recoger pasajeros,
  - error de GPS de unos 2,5 m que cambia de a pocos (proceso autorregresivo AR(1)).

#### 2. Procesamiento (`3_procesar_rutas.py`)
| Paso | Qué se hace | Herramienta |
|---|---|---|
| Lectura | Puntos, horas y alturas del GPX | gpxpy |
| Proyección | De grados a metros (UTM zona 19 S, EPSG:32719) | GeoPandas / pyproj |
| Limpieza | Quita puntos repetidos y saltos de más de 90 km/h | NumPy |
| Muestreo | Un punto cada {ajuste.get('separacion_muestras_m', 20)} m a lo largo del recorrido | Shapely |
| Candidatos | Calles a menos de {ajuste.get('radio_candidatos_m', 35)} m de cada muestra (hasta {ajuste.get('max_candidatos', 6)}) | Índice espacial R-tree |
| **Ajuste a las calles** (map matching) | Modelo oculto de Markov + algoritmo de Viterbi (Newson y Krumm, 2009) | NetworkX |
| · probabilidad de emisión | Distribución normal de la distancia a la calle, σ = {ajuste.get('sigma_gps_m', 7)} m | |
| · probabilidad de transición | Diferencia entre la distancia por calles (Dijkstra) y en línea recta, β = {ajuste.get('beta_m', 15)} m | |
| Reconstrucción | Se une el eje de las calles elegidas y se quitan picos de ida y vuelta menores a 15 m | Shapely |
| Tramos sin calle | Si el mapa no tiene la calle, ese pedazo se conserva enderezado (Douglas-Peucker, 5 m) | Shapely |
| Paraderos | Cada esquina (nodo con 3 o más calles, `street_count`) por donde pasa la ruta; se unen las que están a menos de {config.get('distancia_min_entre_esquinas_m', 25)} m | OSMnx |
| Horarios | Cada punto GPS se ubica sobre la ruta (referenciación lineal) y se interpola la hora de paso por cada esquina | NumPy |
| Paradas | Tiempo con velocidad menor a 3 km/h a menos de 20 m de la esquina | NumPy |

#### 3. Mapa base
Red vial de OpenStreetMap descargada con OSMnx (`1_descargar_mapa.py`), tipo `drive`, guardada comprimida.
Para el ajuste se usa sin sentido de circulación, porque en Juliaca los sentidos de las calles no siempre
están completos en OSM.
""")


def leer_ejecucion():
    return {paso: leer_json(CARPETA_EJECUCION / f"{paso}.json")
            for paso in PASOS if (CARPETA_EJECUCION / f"{paso}.json").exists()}


def vista_ejecucion():
    st.title("Resultados de ejecución")
    datos = leer_ejecucion()
    if not datos:
        st.info("Todavía no hay tiempos medidos. Ejecuta 2_generar_recorridos.py y 3_procesar_rutas.py.")
        return

    e = list(datos.values())[-1]["equipo"]
    with st.container(border=True):
        st.markdown("**Equipo donde se ejecutó**")
        izquierda, derecha = st.columns([3, 2])
        izquierda.markdown(f"""
| | |
|---|---|
| Equipo | {e['equipo']} |
| Procesador | {e['procesador']} |
| Núcleos / hilos | {e.get('nucleos') or '-'} / {e['hilos']} |
| Memoria RAM | {e['ram_gb']} GB |
| Sistema operativo | {e['sistema']} |
| Python | {e['python']} |
""")
        derecha.dataframe(pd.DataFrame(e["librerias"].items(), columns=["Librería", "Versión"]),
                          hide_index=True, width="stretch")

    columnas = st.columns(len(datos))
    for columna, (paso, d) in zip(columnas, datos.items()):
        columna.metric(PASOS[paso], f"{d['total_s']:.2f} s", border=True, help=f"Ejecutado el {d['fecha']}")
        detalle = [f"memoria máxima {d['memoria_max_mb']} MB"] if d.get("memoria_max_mb") else []
        if "carga_mapa" in d["partes_s"]:
            detalle.append(f"carga del mapa {d['partes_s']['carga_mapa']:.2f} s")
        columna.caption(" · ".join(detalle))

    for paso, d in datos.items():
        if not d["rutas"]:
            continue
        st.subheader(PASOS[paso])
        tabla = pd.DataFrame(d["rutas"])
        partes = [p for p in PARTES if p in tabla.columns]
        etapas = {p: f"{i}. {PARTES[p]}" for i, p in enumerate(partes, start=1)}
        st.bar_chart(tabla.set_index("ruta")[partes].rename(columns=etapas), horizontal=True, height=260,
                     x_label="", y_label="segundos")
        tabla["puntos_por_s"] = tabla["puntos"] / tabla["segundos"]
        st.dataframe(tabla[["ruta", "puntos", *partes, "segundos", "puntos_por_s"]], hide_index=True,
                     width="stretch", column_config={
                         "ruta": "Ruta", "puntos": "Puntos GPS", "segundos": st.column_config.NumberColumn(
                             "Total (s)", format="%.2f"),
                         "puntos_por_s": st.column_config.NumberColumn("Puntos por segundo", format="%.0f"),
                         **{p: st.column_config.NumberColumn(PARTES[p][:1].upper() + PARTES[p][1:], format="%.2f") for p in partes}})
        c = st.columns(4)
        c[0].metric("Puntos GPS", f"{tabla['puntos'].sum():,}")
        c[1].metric("Promedio por ruta", f"{tabla['segundos'].mean():.2f} s")
        c[2].metric("Por cada 1 000 puntos", f"{tabla['segundos'].sum() / tabla['puntos'].sum() * 1000:.2f} s")
        if d.get("km"):
            c[3].metric("Por kilómetro de ruta", f"{tabla['segundos'].sum() / d['km'] * 1000:.0f} ms")

    reporte = CARPETA_EJECUCION / "REPORTE.md"
    if reporte.exists():
        st.download_button("Descargar el reporte (Markdown)", reporte.read_bytes(), file_name="REPORTE.md")
    st.caption("Los tiempos no incluyen el arranque de Python ni la carga de las librerías. "
               "Cambian un poco en cada ejecución.")


# ---------------------------------------------------------------- página

st.set_page_config(page_title="JuliacaBus-DS · Rutas", page_icon="🚌", layout="wide")
st.markdown("""<style>
  .block-container {padding-top: 1.4rem;}
  div[data-testid="stMetricValue"] {font-size: 1.3rem;}
  .chip {display:inline-block;padding:2px 10px;border-radius:999px;color:white;font-weight:600;font-size:.85rem}
</style>""", unsafe_allow_html=True)

config = leer_json(BASE / "config.json")
colores_lineas = config.get("lineas", {})
resumen = csv(CARPETA_SALIDA / "resumen_rutas.csv")

st.sidebar.title("🚌 JuliacaBus-DS")
st.sidebar.caption("Rutas de micro de Juliaca y San Miguel")

if resumen is None or resumen.empty:
    st.info("Todavía no hay rutas procesadas. Ejecuta en orden: 1_descargar_mapa.py, "
            "2_generar_recorridos.py y 3_procesar_rutas.py.")
    st.stop()

resumen = resumen.copy()
resumen[["linea", "sentido"]] = resumen[["linea", "sentido"]].fillna("")
orden = list(colores_lineas)
resumen["_linea"] = resumen["linea"].map(lambda x: orden.index(x) if x in orden else 99)
resumen["_sentido"] = resumen["sentido"].map({"ida": 0, "vuelta": 1}).fillna(2)
resumen = resumen.sort_values(["_linea", "_sentido", "ruta"])


def color_de(linea):
    return colores_lineas.get(linea, "#2563eb")


vista = st.sidebar.radio("Ver", ["Una ruta", "Todas las líneas", "Ejecución"])

if vista == "Ejecución":
    vista_ejecucion()
    st.stop()

if vista == "Todas las líneas":
    st.title("Todas las líneas")
    rutas = tuple((r["ruta"], r["linea"], r["sentido"], color_de(r["linea"])) for _, r in resumen.iterrows())
    version = fecha_mod(*[CARPETA_ESQUINAS / f"{r[0]}_procesada.gpx" for r in rutas])
    st.iframe(html_todas(rutas, version), height=640)
    tabla = resumen[["linea", "sentido", "ruta", "salida", "llegada", "duracion_min", "largo_despues_km",
                     "velocidad_media_kmh", "paraderos", "paradas_detectadas"]]
    st.dataframe(tabla, hide_index=True, width="stretch", column_config={
        "linea": "Línea", "sentido": "Sentido", "ruta": "Ruta", "salida": "Salida", "llegada": "Llegada",
        "duracion_min": st.column_config.NumberColumn("Duración (min)", format="%.0f"),
        "largo_despues_km": st.column_config.NumberColumn("Km", format="%.2f"),
        "velocidad_media_kmh": st.column_config.NumberColumn("Vel. media (km/h)", format="%.1f"),
        "paraderos": "Paraderos", "paradas_detectadas": "Paradas"})
    st.stop()

lineas = list(dict.fromkeys(resumen["linea"]))
linea = st.sidebar.radio("Línea", lineas, format_func=lambda x: f"Línea {x}" if x else "Sin línea")
de_la_linea = resumen[resumen["linea"] == linea]
ruta = st.sidebar.radio("Sentido", list(de_la_linea["ruta"]),
                        format_func=lambda x: f"{de_la_linea.set_index('ruta').loc[x, 'sentido'].capitalize()} · "
                                              f"{trayecto(x)}")
ver_dibujo = st.sidebar.toggle("Mostrar el trazo dibujado", value=False)

r = resumen.set_index("ruta").loc[ruta]
color = color_de(r["linea"])
crudo_archivo = archivo_crudo(ruta)
con_horas = crudo_archivo is not None and "hora" in gpx(crudo_archivo)

st.markdown(f"<span class='chip' style='background:{color}'>Línea {r['linea']}</span> "
            f"<span class='chip' style='background:#374151'>{r['sentido']}</span>"
            f"<h2 style='margin:.3rem 0 0'>{trayecto(ruta)}</h2>", unsafe_allow_html=True)

c = st.columns(6)
c[0].metric("Horario", f"{str(r['salida'])[:5]} – {str(r['llegada'])[:5]}" if con_horas else "-", border=True)
c[1].metric("Duración", f"{r['duracion_min']:.0f} min" if con_horas else "-", border=True)
c[2].metric("Distancia", f"{r['largo_despues_km']:.2f} km", border=True)
c[3].metric("Paraderos", int(r["paraderos"]), border=True)
c[4].metric("Paradas", int(r["paradas_detectadas"]) if con_horas else "-", border=True)
c[5].metric("Vel. media", f"{r['velocidad_media_kmh']:.1f} km/h" if con_horas else "-", border=True)

if "tiempo_s" in r and pd.notna(r["tiempo_s"]):
    st.caption(f"⏱ Procesado en {r['tiempo_s']:.2f} s (ver «Ejecución» en el menú)")

seccion = st.segmented_control("Sección", SECCIONES, default=SECCIONES[0], label_visibility="collapsed") \
    or SECCIONES[0]

if crudo_archivo is None and seccion != "Método":
    st.error("No se encontró el recorrido GPS en data/gpx_entrada/.")
elif seccion == "Antes y después":
    seccion_antes_despues(ruta, r, color, ver_dibujo)
elif seccion == "Paraderos y horarios":
    seccion_paraderos(ruta)
elif seccion == "Velocidad":
    seccion_velocidad(ruta, color, con_horas)
elif seccion == "Reproducir viaje":
    seccion_reproducir(ruta, color, con_horas)
else:
    seccion_metodo(config)

st.sidebar.divider()
st.sidebar.caption("Mapas © OpenStreetMap, Esri · Procesamiento con OSMnx, NetworkX, Shapely y GeoPandas")
