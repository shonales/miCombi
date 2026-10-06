"""
Paso 3: procesa los recorridos GPS y deja cada esquina como paradero.

Para cada recorrido de data/gpx_entrada/:
  1. limpia los puntos (repetidos y saltos imposibles),
  2. ajusta el recorrido a las calles de OpenStreetMap (map matching con un
     modelo oculto de Markov y el algoritmo de Viterbi),
  3. marca como paradero cada esquina por donde pasa la ruta,
  4. si el recorrido tiene horas, calcula a qué hora pasó por cada paradero
     y si se detuvo ahí.

Uso:
    python 3_procesar_rutas.py
"""

import ast
import gzip
import json
import math
import sys
from datetime import timedelta, timezone
from pathlib import Path

import folium
import geopandas as gpd
import gpxpy
import gpxpy.gpx
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import shapely
from folium import plugins
from shapely.geometry import LineString
from shapely.ops import substring

from rendimiento import Cronometro, guardar as guardar_ejecucion

BASE = Path(__file__).resolve().parent
CARPETA_ENTRADA = BASE / "data" / "gpx_entrada"
CARPETA_PARAMETROS = BASE / "data" / "parametros_rutas"
ARCHIVO_MAPA = BASE / "data" / "mapa" / "juliaca_red.graphml.gz"
CARPETA_SALIDA = BASE / "data" / "salida"
CARPETA_ESQUINAS = CARPETA_SALIDA / "esquinas"
CARPETA_HORARIOS = CARPETA_SALIDA / "horarios"
CARPETA_MAPAS = CARPETA_SALIDA / "mapas"

HORA_PERU = timezone(timedelta(hours=-5))
VELOCIDAD_MAX_KMH = 90        # más rápido que esto es un salto del GPS
VELOCIDAD_DETENIDA_KMH = 3    # por debajo de esto la micro está parada
RADIO_PARADA_M = 20           # una detención cuenta para la esquina más cercana dentro de este radio
CORTE_DIJKSTRA_M = 600
RETROCESO_RUIDO_M = 15       # retrocesos menores a esto sobre una calle se ignoran


def leer_config():
    ruta = BASE / "config.json"
    if not ruta.exists():
        sys.exit("No se encontró config.json en la carpeta del proyecto.")
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def nombre_ruta(archivo):
    """'ruta_23.gpx' -> '23'."""
    nombre = archivo.stem
    for prefijo in ("ruta_", "ruta-", "ruta "):
        if nombre.lower().startswith(prefijo):
            return nombre[len(prefijo):]
    return nombre


def leer_parametros(ruta):
    archivo = CARPETA_PARAMETROS / f"{ruta}.json"
    if archivo.exists():
        with open(archivo, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------- red de calles

def nombres_de(valor):
    """El atributo 'name' de OSM puede venir como texto, como lista o como texto con forma de lista."""
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return []
    if isinstance(valor, str) and valor.startswith("["):
        try:
            valor = ast.literal_eval(valor)
        except (ValueError, SyntaxError):
            return [valor]
    if isinstance(valor, (list, tuple, set)):
        return [str(v) for v in valor]
    return [str(valor)]


class Red:
    """Calles en metros (UTM), sin sentido de circulación, con lo necesario para el ajuste."""

    def __init__(self, grafo_proyectado):
        self.g = grafo_proyectado.to_undirected()
        self.crs = self.g.graph["crs"]
        nodos, aristas = ox.graph_to_gdfs(self.g)
        aristas = aristas.reset_index()

        self.xy = {n: (x, y) for n, x, y in zip(nodos.index, nodos["x"], nodos["y"])}
        self.calles_esquina = nodos["street_count"].fillna(0).astype(int).to_dict()
        self.u = aristas["u"].to_numpy()
        self.v = aristas["v"].to_numpy()
        self.geoms = np.array([self.orientar(g, u) for g, u in zip(aristas.geometry, self.u)], dtype=object)
        self.largo = shapely.length(self.geoms)
        self.nombres = [" / ".join(sorted(set(nombres_de(n)))) for n in aristas["name"]]
        self.indice = gpd.GeoSeries(self.geoms, crs=self.crs).sindex

        self.calles = {}
        for u, v, datos in self.g.edges(data=True):
            for nombre in nombres_de(datos.get("name")):
                self.calles.setdefault(u, set()).add(nombre)
                self.calles.setdefault(v, set()).add(nombre)
        self._distancias = {}

    def orientar(self, geom, desde):
        """Devuelve la geometría empezando en el nodo 'desde'."""
        x, y = self.xy[desde]
        (x0, y0), (x1, y1) = geom.coords[0], geom.coords[-1]
        return geom if math.hypot(x0 - x, y0 - y) <= math.hypot(x1 - x, y1 - y) else geom.reverse()

    def distancias_desde(self, nodo):
        if nodo not in self._distancias:
            self._distancias[nodo] = nx.single_source_dijkstra_path_length(
                self.g, nodo, cutoff=CORTE_DIJKSTRA_M, weight="length")
        return self._distancias[nodo]

    def calle_entre(self, n1, n2):
        datos = min(self.g.get_edge_data(n1, n2).values(), key=lambda d: d["length"])
        geom = datos.get("geometry")
        if geom is None:
            geom = LineString([self.xy[n1], self.xy[n2]])
        return self.orientar(geom, n1)


def cargar_red():
    if not ARCHIVO_MAPA.exists():
        sys.exit(
            "No se encontró el mapa (data/mapa/juliaca_red.graphml.gz).\n"
            "Ejecuta primero:  python 1_descargar_mapa.py"
        )
    print("Cargando y preparando el mapa (unos segundos)...")
    with gzip.open(ARCHIVO_MAPA, "rt", encoding="utf-8") as f:
        return Red(ox.project_graph(ox.load_graphml(graphml_str=f.read())))


# ---------------------------------------------------------------- lectura y limpieza

def leer_recorrido(archivo):
    with open(archivo, encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    puntos = [p for t in gpx.tracks for s in t.segments for p in s.points]
    if not puntos:
        puntos = [p for r in gpx.routes for p in r.points]
    if not puntos:
        puntos = list(gpx.waypoints)
    if len(puntos) < 2:
        raise ValueError("el archivo no tiene suficientes puntos (se necesitan al menos 2)")

    latlon = np.array([(p.latitude, p.longitude) for p in puntos])
    horas = [p.time for p in puntos]
    return latlon, (horas if all(h is not None for h in horas) else None)


def a_metros(latlon, crs):
    puntos = gpd.GeoSeries(gpd.points_from_xy(latlon[:, 1], latlon[:, 0]), crs="EPSG:4326").to_crs(crs)
    return np.column_stack([puntos.x, puntos.y])


def a_latlon(xy, crs):
    puntos = gpd.GeoSeries(gpd.points_from_xy(xy[:, 0], xy[:, 1]), crs=crs).to_crs("EPSG:4326")
    return np.column_stack([puntos.y, puntos.x])


def limpiar(xy, segundos):
    """Índices de los puntos que se quedan: sin repetidos (si no hay horas) y sin saltos imposibles."""
    quedan = [0]
    for i in range(1, len(xy)):
        j = quedan[-1]
        d = math.hypot(*(xy[i] - xy[j]))
        if segundos is None:
            if d > 0:
                quedan.append(i)
            continue
        dt = segundos[i] - segundos[j]
        if dt > 0 and d / dt * 3.6 <= VELOCIDAD_MAX_KMH:
            quedan.append(i)
    return np.array(quedan)


def muestras_para_ajuste(xy, separacion):
    """Puntos cada 'separacion' metros a lo largo del recorrido (quita el amontonamiento de las paradas)."""
    linea = LineString(xy)
    distancias = np.append(np.arange(0, linea.length, separacion), linea.length)
    return shapely.get_coordinates(shapely.line_interpolate_point(linea, distancias))


# ---------------------------------------------------------------- ajuste a las calles

def candidatos(red, muestras, radio, maximo):
    """Para cada muestra, las calles cercanas: (arista, metros desde su inicio, distancia a la muestra)."""
    puntos = shapely.points(muestras)
    i_punto, i_arista = red.indice.query(shapely.buffer(puntos, radio), predicate="intersects")
    distancia = shapely.distance(puntos[i_punto], red.geoms[i_arista])

    opciones = [[] for _ in muestras]
    for ip, ia, d in zip(i_punto, i_arista, distancia):
        if d <= radio:
            opciones[ip].append((d, ia))
    resultado = []
    for ip, lista in enumerate(opciones):
        lista.sort()
        resultado.append([(ia, shapely.line_locate_point(red.geoms[ia], puntos[ip]), d) for d, ia in lista[:maximo]])
    return resultado


def extremos(red, c):
    """Los dos extremos de la calle del candidato: (nodo, metros hasta él, posición del extremo en la calle)."""
    arista, pos, _ = c
    return ((red.u[arista], pos, 0.0), (red.v[arista], red.largo[arista] - pos, red.largo[arista]))


def distancia_por_calle(red, a, b):
    if a[0] == b[0]:
        return abs(b[1] - a[1])
    mejor = math.inf
    for x, dx, _ in extremos(red, a):
        alcance = red.distancias_desde(x)
        for y, dy, _ in extremos(red, b):
            if y in alcance:
                mejor = min(mejor, dx + alcance[y] + dy)
    return mejor


def viterbi(red, muestras, opciones, sigma, beta):
    """
    Elige una calle para cada muestra. Premia estar cerca de la calle
    (emisión) y que la distancia por las calles entre dos muestras se parezca
    a la distancia en línea recta (transición). Devuelve (muestra, candidato).
    """
    elegidos, historia, puntaje, anterior = [], [], None, None

    def retroceder():
        j = int(np.argmax(puntaje))
        camino = []
        for t, punteros in reversed(historia):
            camino.append((t, opciones[t][j]))
            if punteros is not None:
                j = punteros[j]
        return camino[::-1]

    for t, cands in enumerate(opciones):
        if not cands:
            continue
        emision = np.array([-0.5 * (d / sigma) ** 2 for _, _, d in cands])
        if puntaje is None:
            puntaje, historia, anterior = emision, [(t, None)], t
            continue

        recta = math.hypot(*(muestras[t] - muestras[anterior]))
        nuevo = np.full(len(cands), -math.inf)
        punteros = np.zeros(len(cands), dtype=int)
        for j, b in enumerate(cands):
            for i, a in enumerate(opciones[anterior]):
                if puntaje[i] == -math.inf:
                    continue
                por_calle = distancia_por_calle(red, a, b)
                if por_calle == math.inf:
                    continue
                valor = puntaje[i] - abs(por_calle - recta) / beta
                if valor > nuevo[j]:
                    nuevo[j], punteros[j] = valor, i
        nuevo += emision

        if np.isinf(nuevo).all():
            elegidos += retroceder()
            puntaje, historia = emision, [(t, None)]
        else:
            puntaje = nuevo
            historia.append((t, punteros))
        anterior = t

    if puntaje is not None:
        elegidos += retroceder()
    return elegidos


def mejor_conexion(red, a, b):
    """Por qué extremo sale de la calle de 'a' y por cuál entra a la de 'b'."""
    mejor, conexion = math.inf, None
    for salida in extremos(red, a):
        alcance = red.distancias_desde(salida[0])
        for entrada in extremos(red, b):
            d = alcance.get(entrada[0])
            if d is None:
                try:
                    d = nx.shortest_path_length(red.g, salida[0], entrada[0], weight="length")
                except nx.NetworkXNoPath:
                    continue
            if salida[1] + d + entrada[1] < mejor:
                mejor, conexion = salida[1] + d + entrada[1], (salida, entrada)
    return conexion


def parte(geom, desde, hasta):
    tramo = substring(geom, desde, hasta)
    return list(tramo.coords) if tramo.geom_type == "LineString" else [tramo.coords[0]]


def construir_ruta(red, elegidos):
    """
    Une las calles elegidas en una sola línea que va por el eje de cada calle.
    Devuelve las coordenadas y los nodos por los que pasa, con su posición (m).
    """
    coords, nodos = [], []
    largo = 0.0

    def agregar(puntos):
        nonlocal largo
        for x, y in puntos:
            if coords:
                d = math.hypot(x - coords[-1][0], y - coords[-1][1])
                if d == 0:
                    continue
                largo += d
            coords.append((x, y))

    actual = elegidos[0]
    agregar(parte(red.geoms[actual[0]], actual[1], actual[1]))
    sentido = 0
    for b in elegidos[1:]:
        a = actual
        if a[0] == b[0]:
            avance = b[1] - a[1]
            # pequeños retrocesos sobre la misma calle son ruido del GPS
            if avance == 0 or (sentido and np.sign(avance) != sentido and abs(avance) < RETROCESO_RUIDO_M):
                continue
            agregar(parte(red.geoms[a[0]], a[1], b[1]))
            sentido = np.sign(avance)
        else:
            conexion = mejor_conexion(red, a, b)
            if conexion is None:
                continue
            (x, _, fin_a), (y, _, inicio_b) = conexion
            agregar(parte(red.geoms[a[0]], a[1], fin_a))
            nodos.append((x, largo))
            camino = nx.shortest_path(red.g, x, y, weight="length")
            for n1, n2 in zip(camino, camino[1:]):
                agregar(red.calle_entre(n1, n2).coords)
                nodos.append((n2, largo))
            agregar(parte(red.geoms[b[0]], inicio_b, b[1]))
            sentido = 1 if inicio_b == 0 else -1
        actual = b
    return coords, nodos


def quitar_picos(coords, max_m):
    """Quita vértices donde la línea da media vuelta y uno de los lados es corto (ruido al doblar)."""
    coords = list(coords)
    i = 1
    while i < len(coords) - 1:
        a = np.subtract(coords[i], coords[i - 1])
        b = np.subtract(coords[i + 1], coords[i])
        la, lb = np.hypot(*a), np.hypot(*b)
        if la == 0 or lb == 0 or (a @ b / (la * lb) < -0.87 and min(la, lb) <= max_m):
            del coords[i]
            i = max(i - 1, 1)
        else:
            i += 1
    return coords


def reubicar_nodos(red, linea, nodos):
    """Posición de cada nodo sobre la línea ya limpia (cambia unos metros al quitar los picos)."""
    medidor = Medidor(linea)
    return [(n, medidor.posicion(np.array(red.xy[n]), pos - 60, pos + 10)) for n, pos in nodos]


def enderezar(puntos):
    """Pedazo del recorrido sin calle en el mapa: se conserva, pero enderezado (Douglas-Peucker, 5 m)."""
    return list(LineString(puntos).simplify(5).coords)


# ---------------------------------------------------------------- paraderos

def paraderos(red, linea, nodos, arista_inicio, arista_fin, dist_min):
    eventos = [
        {"id_nodo_osm": n, "xy": red.xy[n], "dist": pos, "calles": set(red.calles.get(n, set()))}
        for n, pos in nodos if red.calles_esquina.get(n, 0) >= 3
    ]

    # avenidas con berma central: OSM pone dos nodos por cruce; se deja uno
    unidos = []
    for e in eventos:
        if unidos:
            previo = unidos[-1]
            if math.hypot(e["xy"][0] - previo["xy"][0], e["xy"][1] - previo["xy"][1]) < dist_min \
                    and e["dist"] - previo["dist"] <= 2 * dist_min:
                previo["calles"] |= e["calles"]
                continue
        unidos.append(dict(e, es_terminal=False))

    extremos_ruta = [(0.0, linea.coords[0], arista_inicio, 0), (linea.length, linea.coords[-1], arista_fin, -1)]
    for dist, xy, arista, lado in extremos_ruta:
        if unidos and abs(unidos[lado]["dist"] - dist) <= dist_min:
            unidos[lado]["es_terminal"] = True
            continue
        terminal = {"id_nodo_osm": "", "xy": xy, "dist": dist,
                    "calles": {red.nombres[arista]} - {""}, "es_terminal": True}
        if lado == 0:
            unidos.insert(0, terminal)
        else:
            unidos.append(terminal)
    return unidos


def tabla_paraderos(ruta, lista, crs):
    latlon = a_latlon(np.array([e["xy"] for e in lista]), crs)
    return pd.DataFrame({
        "ruta": ruta,
        "orden": range(1, len(lista) + 1),
        "id_esquina": [f"E{i:03d}" for i in range(1, len(lista) + 1)],
        "id_nodo_osm": [e["id_nodo_osm"] for e in lista],
        "lat": latlon[:, 0].round(7),
        "lon": latlon[:, 1].round(7),
        "dist_acumulada_m": [round(e["dist"], 1) for e in lista],
        "calles": [" / ".join(sorted(e["calles"])) for e in lista],
        "es_terminal": [e["es_terminal"] for e in lista],
    })


# ---------------------------------------------------------------- tiempos

class Medidor:
    """Mide en qué metro de la línea cae un punto, buscando solo dentro de una ventana."""

    def __init__(self, linea):
        c = np.array(linea.coords)
        self.acumulado = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(c, axis=0).T))])
        self.inicio, self.vector = c[:-1], np.diff(c, axis=0)
        self.largo2 = (self.vector ** 2).sum(axis=1)

    def posicion(self, p, desde, hasta):
        k0 = max(int(np.searchsorted(self.acumulado, desde, side="right")) - 1, 0)
        k1 = max(min(int(np.searchsorted(self.acumulado, hasta, side="left")), len(self.vector)), k0 + 1)
        t = np.clip(((p - self.inicio[k0:k1]) * self.vector[k0:k1]).sum(axis=1) / self.largo2[k0:k1], 0, 1)
        cerca = self.inicio[k0:k1] + self.vector[k0:k1] * t[:, None]
        k = int(np.argmin(np.hypot(*(p - cerca).T)))
        return self.acumulado[k0 + k] + t[k] * math.sqrt(self.largo2[k0 + k])


def avance_sobre_linea(linea, xy, segundos):
    """
    Metros recorridos sobre la ruta ajustada en cada punto GPS. Cada punto se
    busca solo cerca de donde iba el anterior y nunca hacia atrás.
    """
    medidor = Medidor(linea)
    avance = np.zeros(len(xy))
    actual = 0.0
    for i, p in enumerate(xy):
        dt = segundos[i] - segundos[i - 1] if i else 0
        actual = max(actual, medidor.posicion(p, max(0.0, actual - 30), actual + 40 + 25 * dt))
        avance[i] = actual
    return avance


def tiempos_por_paradero(tabla, avance, segundos, salida):
    s = tabla["dist_acumulada_m"].to_numpy(dtype=float)

    llegada = np.empty(len(s))
    for k, pos in enumerate(s):
        i = int(np.searchsorted(avance, pos, side="left"))
        if i == 0:
            llegada[k] = segundos[0]
        elif i >= len(avance):
            llegada[k] = segundos[-1]
        else:
            tramo = avance[i] - avance[i - 1]
            f = (pos - avance[i - 1]) / tramo if tramo > 0 else 0
            llegada[k] = segundos[i - 1] + f * (segundos[i] - segundos[i - 1])

    # tiempo detenido: se suma a la esquina más cercana
    dt = np.diff(segundos)
    velocidad = np.diff(avance) / np.where(dt > 0, dt, np.nan) * 3.6
    detenido = np.where(velocidad < VELOCIDAD_DETENIDA_KMH, dt, 0.0)
    pausa = np.zeros(len(s))
    for i in np.flatnonzero(detenido):
        pos = avance[i + 1]
        k = int(np.argmin(np.abs(s - pos)))
        if abs(s[k] - pos) <= RADIO_PARADA_M:
            pausa[k] += dt[i]

    # velocidad en marcha entre una esquina y la siguiente (sin el tiempo detenido en ese tramo)
    detenido_hasta = np.interp(llegada, segundos[1:], np.cumsum(detenido))
    en_marcha = np.diff(llegada) - np.diff(detenido_hasta)
    velocidad_tramo = np.full(len(s), np.nan)
    ok = en_marcha >= 1
    velocidad_tramo[:-1][ok] = np.diff(s)[ok] / en_marcha[ok] * 3.6

    horas = [(salida + timedelta(seconds=float(x))).strftime("%H:%M:%S") for x in llegada]
    return pd.DataFrame({
        "hora_llegada": horas,
        "seg_desde_salida": (llegada - segundos[0]).round(1),
        "hubo_parada": pausa >= 5,
        "pausa_seg": pausa.round(1),
        "velocidad_tramo_kmh": velocidad_tramo.round(1),
    })


# ---------------------------------------------------------------- salidas

def guardar_gpx(ruta, linea_latlon, tabla, archivo):
    gpx = gpxpy.gpx.GPX()
    gpx.creator = "JuliacaBus-DS"
    gpx.name = f"{ruta} - ruta procesada"
    gpx.description = "Ruta ajustada a las calles de OpenStreetMap; cada esquina es un paradero."
    pista = gpxpy.gpx.GPXTrack(name=f"{ruta} (procesada)")
    segmento = gpxpy.gpx.GPXTrackSegment()
    segmento.points.extend(gpxpy.gpx.GPXTrackPoint(round(la, 6), round(lo, 6)) for la, lo in linea_latlon)
    pista.segments.append(segmento)
    gpx.tracks.append(pista)

    for _, f in tabla.iterrows():
        detalle = f["calles"] or "sin nombre en OSM"
        if "hora_llegada" in tabla:
            detalle += f" | llegada {f['hora_llegada']} | pausa {f['pausa_seg']:.0f} s"
        gpx.waypoints.append(gpxpy.gpx.GPXWaypoint(
            latitude=f["lat"], longitude=f["lon"], name=f["id_esquina"], description=detalle,
            type="terminal" if f["es_terminal"] else "paradero",
        ))
    archivo.write_text(gpx.to_xml(prettyprint=False), encoding="utf-8")


ESTILO_MAPA = """<style>
  .calles-gris { filter: grayscale(0.85) brightness(1.05) contrast(0.9); }
  .etiqueta { background:#111827; color:white; padding:2px 6px; border-radius:4px;
              font:bold 11px sans-serif; white-space:nowrap; box-shadow:0 1px 3px #0006; }
</style>"""


def fondo(mapa):
    """Calles en gris para que resalten las rutas, y la foto satelital como opción."""
    folium.TileLayer("OpenStreetMap", name="Calles", class_name="calles-gris").add_to(mapa)
    folium.TileLayer("Esri.WorldImagery", name="Satélite", show=False).add_to(mapa)


def encuadre(latlon, ancho_px=640, alto_px=600):
    """Centro y zoom para que toda la ruta entre en un mapa de ese tamaño."""
    (lat_min, lon_min), (lat_max, lon_max) = latlon.min(axis=0), latlon.max(axis=0)
    zoom_ancho = math.log2(ancho_px * 360 / (256 * max(lon_max - lon_min, 1e-4)))
    zoom_alto = math.log2(alto_px * 360 / (256 * max(lat_max - lat_min, 1e-4) * 1.04))
    return [(lat_min + lat_max) / 2, (lon_min + lon_max) / 2], int(min(zoom_ancho, zoom_alto, 18))


def capa_puntos(latlon, color):
    """Todos los puntos GPS en una sola capa (se dibujan en canvas, carga rápido)."""
    puntos = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [round(lo, 6), round(la, 6)]}}
        for la, lo in latlon]}
    return folium.GeoJson(puntos, marker=folium.CircleMarker(radius=2, weight=0, fill=True,
                                                            fill_color=color, fill_opacity=0.8))


def capa_paraderos(tabla, color):
    """Paraderos en una sola capa: relleno si la micro se detuvo, negro si es terminal."""
    lugares = []
    for _, f in tabla.iterrows():
        paro = bool(f.get("hubo_parada", False))
        texto = f"{f['id_esquina']} · {f['calles'] if isinstance(f['calles'], str) and f['calles'] else 'sin nombre en OSM'}"
        if "hora_llegada" in f:
            texto += f" · {f['hora_llegada']}" + (f" · paró {f['pausa_seg']:.0f} s" if paro else "")
        terminal = bool(f["es_terminal"])
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


def etiqueta(mapa, latlon, texto):
    folium.Marker(latlon, icon=folium.DivIcon(html=f"<div class='etiqueta'>{texto}</div>",
                                              icon_anchor=(-8, 10))).add_to(mapa)


def guardar_mapa(ruta, crudo_latlon, linea_latlon, tabla, color, archivo):
    """Mapa doble: a la izquierda el recorrido GPS tal cual, a la derecha la ruta procesada."""
    centro, zoom = encuadre(crudo_latlon)
    mapa = plugins.DualMap(location=centro, zoom_start=zoom, tiles=None, prefer_canvas=True)
    fondo(mapa)

    folium.PolyLine(crudo_latlon.round(6).tolist(), color="#dc2626", weight=2, opacity=0.5).add_to(mapa.m1)
    capa_puntos(crudo_latlon, "#dc2626").add_to(mapa.m1)

    folium.PolyLine(linea_latlon.tolist(), color="white", weight=9, opacity=0.95).add_to(mapa.m2)
    folium.PolyLine(linea_latlon.tolist(), color=color, weight=5, opacity=1).add_to(mapa.m2)
    capa_paraderos(tabla, color).add_to(mapa.m2)
    etiqueta(mapa.m2, linea_latlon[0].tolist(), "Inicio")
    etiqueta(mapa.m2, linea_latlon[-1].tolist(), "Fin")

    folium.LayerControl(collapsed=True).add_to(mapa)
    titulo = ("<div style='position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:9999;"
              "background:white;padding:4px 10px;border-radius:6px;font:13px sans-serif;box-shadow:0 1px 4px #0004'>"
              f"<b>{ruta}</b> · izquierda: recorrido GPS (antes) · derecha: ruta procesada con paraderos (después)</div>")
    mapa.get_root().header.add_child(folium.Element(ESTILO_MAPA))
    mapa.get_root().html.add_child(folium.Element(titulo))
    mapa.save(str(archivo))


# ---------------------------------------------------------------- programa principal

def procesar(archivo, config, red, reloj):
    ruta = nombre_ruta(archivo)
    ajuste = config["ajuste_calles"]
    dist_min = config["distancia_min_entre_esquinas_m"]
    p = leer_parametros(ruta)

    latlon, horas = leer_recorrido(archivo)
    segundos = np.array([(h - horas[0]).total_seconds() for h in horas]) if horas else None
    xy = a_metros(latlon, red.crs)
    quedan = limpiar(xy, segundos)
    xy, latlon = xy[quedan], latlon[quedan]
    segundos = segundos[quedan] if segundos is not None else None

    muestras = muestras_para_ajuste(xy, ajuste["separacion_muestras_m"])
    reloj.marcar("lectura")
    opciones = candidatos(red, muestras, ajuste["radio_candidatos_m"], ajuste["max_candidatos"])
    sin_calle = sum(1 for o in opciones if not o)
    elegidos = viterbi(red, muestras, opciones, ajuste["sigma_gps_m"], ajuste["beta_m"])
    if len(elegidos) < 2:
        raise ValueError("el recorrido no pasa cerca de ninguna calle del mapa")
    reloj.marcar("ajuste")

    t0, t1 = elegidos[0][0], elegidos[-1][0]
    coords, nodos = construir_ruta(red, [c for _, c in elegidos])
    cabeza = enderezar([*map(tuple, muestras[:t0]), coords[0]])[:-1] if t0 > 0 else []
    cola = enderezar([coords[-1], *map(tuple, muestras[t1 + 1:])])[1:] if t1 < len(muestras) - 1 else []
    desplazamiento = LineString([*cabeza, coords[0]]).length if cabeza else 0.0
    tramo_calles = LineString(quitar_picos(coords, RETROCESO_RUIDO_M))
    linea = LineString(cabeza + list(tramo_calles.coords) + cola)
    nodos = reubicar_nodos(red, linea, [(n, pos + desplazamiento) for n, pos in nodos])
    fuera_m = linea.length - tramo_calles.length

    lista = paraderos(red, linea, nodos, elegidos[0][1][0], elegidos[-1][1][0], dist_min)
    tabla = tabla_paraderos(ruta, lista, red.crs)
    reloj.marcar("reconstruccion")

    tabla.to_csv(CARPETA_ESQUINAS / f"{ruta}_esquinas.csv", index=False, encoding="utf-8")
    reloj.marcar("guardado")

    tiempos = None
    if horas:
        salida = horas[0].astimezone(HORA_PERU) if horas[0].tzinfo else horas[0]
        avance = avance_sobre_linea(linea, xy, segundos)
        tiempos = tiempos_por_paradero(tabla, avance, segundos, salida)
        tabla = pd.concat([tabla, tiempos], axis=1)
        reloj.marcar("horarios")
        CARPETA_HORARIOS.mkdir(parents=True, exist_ok=True)
        columnas = ["ruta", "orden", "id_esquina", "lat", "lon", "dist_acumulada_m", "calles", "es_terminal",
                    "hora_llegada", "seg_desde_salida", "hubo_parada", "pausa_seg", "velocidad_tramo_kmh"]
        tabla[columnas].to_csv(CARPETA_HORARIOS / f"{ruta}_horarios.csv", index=False, encoding="utf-8")

    linea_latlon = a_latlon(np.array(linea.simplify(0.5).coords), red.crs)
    color = config.get("lineas", {}).get(p.get("linea"), "#2563eb")
    guardar_gpx(ruta, linea_latlon, tabla, CARPETA_ESQUINAS / f"{ruta}_procesada.gpx")
    guardar_mapa(ruta, latlon, linea_latlon, tabla, color, CARPETA_MAPAS / f"{ruta}_mapa.html")

    separacion = shapely.distance(shapely.points(xy), linea)
    largo_crudo = float(np.hypot(*np.diff(xy, axis=0).T).sum())
    resumen = {
        "ruta": ruta,
        "linea": p.get("linea", ""),
        "sentido": p.get("sentido", ""),
        "puntos_antes": len(xy),
        "puntos_despues": len(linea_latlon),
        "largo_antes_km": round(largo_crudo / 1000, 2),
        "largo_despues_km": round(linea.length / 1000, 2),
        "correccion_media_m": round(float(separacion.mean()), 1),
        "correccion_max_m": round(float(separacion.max()), 1),
        "muestras_sin_calle": sin_calle,
        "fuera_de_red_m": round(fuera_m),
        "paraderos": len(tabla),
        "paradas_detectadas": int(tiempos["hubo_parada"].sum()) if tiempos is not None else None,
        "salida": salida.strftime("%H:%M:%S") if horas else None,
        "llegada": (salida + timedelta(seconds=float(segundos[-1]))).strftime("%H:%M:%S") if horas else None,
        "duracion_min": round((segundos[-1] - segundos[0]) / 60, 1) if horas else None,
        "velocidad_media_kmh": round(linea.length / (segundos[-1] - segundos[0]) * 3.6, 1) if horas else None,
    }
    reloj.marcar("guardado")
    return resumen


def main():
    config = leer_config()
    CARPETA_ENTRADA.mkdir(parents=True, exist_ok=True)
    CARPETA_ESQUINAS.mkdir(parents=True, exist_ok=True)
    CARPETA_MAPAS.mkdir(parents=True, exist_ok=True)

    archivos = sorted(CARPETA_ENTRADA.glob("*.gpx"))
    if not archivos:
        sys.exit("No hay recorridos en data/gpx_entrada/. Ejecuta primero:  python 2_generar_recorridos.py")

    reloj_total = Cronometro()
    red = cargar_red()
    reloj_total.marcar("carga_mapa")
    print(f"Recorridos encontrados: {len(archivos)}\n")

    resumenes, tiempos = [], []
    for archivo in archivos:
        print(f"> {archivo.name}")
        reloj = Cronometro()
        try:
            r = procesar(archivo, config, red, reloj)
        except gpxpy.gpx.GPXXMLSyntaxException:
            print("  Error: el archivo no es un GPX válido (revisa que no esté dañado).")
        except ValueError as error:
            print(f"  Error: {error}.")
            continue
        r["tiempo_s"] = round(reloj.total(), 2)
        resumenes.append(r)
        tiempos.append({"ruta": r["ruta"], "puntos": r["puntos_antes"], "segundos": r["tiempo_s"],
                        **{k: round(v, 3) for k, v in reloj.partes.items()}})

    if not resumenes:
        sys.exit("\nNo se pudo procesar ningún recorrido.")

    resumen = pd.DataFrame(resumenes)
    resumen.to_csv(CARPETA_SALIDA / "resumen_rutas.csv", index=False, encoding="utf-8")

    print("\nResumen (antes = recorrido GPS, después = ruta ajustada a las calles)")
    print(f"  {'Ruta':<26}{'Puntos':>14}{'Km':>14}{'Corrección':>12}{'Paraderos':>11}{'Paradas':>9}")
    for r in resumenes:
        puntos = f"{r['puntos_antes']}->{r['puntos_despues']}"
        km = f"{r['largo_antes_km']:.2f}->{r['largo_despues_km']:.2f}"
        paradas = "-" if r["paradas_detectadas"] is None else r["paradas_detectadas"]
        print(f"  {r['ruta']:<26}{puntos:>14}{km:>14}{r['correccion_media_m']:>10.1f} m{r['paraderos']:>11}{paradas:>9}")
        if r["fuera_de_red_m"] > 0:
            print(f"    Aviso: {r['fuera_de_red_m']} m del recorrido van por donde el mapa no tiene calle;"
                  " ese pedazo se dejó tal cual, solo enderezado.")

    reloj_total.marcar("rutas")
    guardar_ejecucion("procesar_rutas", reloj_total, tiempos,
                      km=round(sum(r["largo_despues_km"] for r in resumenes), 2),
                      paraderos=sum(r["paraderos"] for r in resumenes))
    print("\nListo. Archivos en data/salida/ (tiempos en data/salida/ejecucion/)")


if __name__ == "__main__":
    main()
