"""
Paso 2: convierte las rutas dibujadas en gpx.studio en recorridos GPS.

Cada trazo de data/gpx_dibujado/ tiene pocos puntos y va pegado al eje de la
calle. Aquí se genera lo que grabaría un celular dentro de la micro: un punto
cada pocos segundos, con hora, paradas en esquinas, frenadas, el carril
derecho y el error normal de un GPS (unos pocos metros).

Los recorridos salen en data/gpx_entrada/<ruta>.gpx.

Uso:
    python 2_generar_recorridos.py
"""

import gzip
import json
import sys
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import gpxpy
import gpxpy.gpx
import numpy as np
import osmnx as ox
import shapely
from shapely.geometry import LineString

from rendimiento import Cronometro, guardar as guardar_ejecucion

BASE = Path(__file__).resolve().parent
CARPETA_DIBUJOS = BASE / "data" / "gpx_dibujado"
CARPETA_SALIDA = BASE / "data" / "gpx_entrada"
CARPETA_PARAMETROS = BASE / "data" / "parametros_rutas"
ARCHIVO_MAPA = BASE / "data" / "mapa" / "juliaca_red.graphml.gz"

HORA_PERU = timezone(timedelta(hours=-5))
TIEMPO_RAMPA_SEG = 8      # lo que tarda en arrancar o frenar
VEREDA_ENTRADA_M = 12     # metros antes de la parada en que se cierra a la vereda
VEREDA_SALIDA_M = 15      # metros después en que vuelve a su carril
CARRIL_DERECHO_M = 1.5    # la micro va por su carril, no por el eje de la calle


def leer_config():
    ruta = BASE / "config.json"
    if not ruta.exists():
        sys.exit("No se encontró config.json en la carpeta del proyecto.")
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def plantilla_parametros(ruta):
    return {
        "ruta": ruta,
        "linea": "",
        "sentido": "ida",
        "fecha": date.today().isoformat(),
        "hora_salida": "06:30:00",
        "tiempo_total_min": 45,
        "pasaje_soles": 1.0,
        "prob_parada": 0.22,
        "pausa_parada_seg": [5, 35],
        "variacion_velocidad": 0.15,
        "factor_trafico_centro": 1.6,
        "desvio_vereda_m": [2, 4],
        "ruido_gps_m": 2.5,
        "intervalo_seg": 2,
    }


def leer_parametros(ruta):
    """Devuelve los parámetros de la ruta, o None si hubo que crear la plantilla."""
    archivo = CARPETA_PARAMETROS / f"{ruta}.json"
    if not archivo.exists():
        with open(archivo, "w", encoding="utf-8") as f:
            json.dump(plantilla_parametros(ruta), f, ensure_ascii=False, indent=2)
        print(f"  No había parámetros. Se creó la plantilla data/parametros_rutas/{ruta}.json")
        print("  Pon ahí la hora de salida y la duración del viaje, y vuelve a ejecutar.")
        return None
    with open(archivo, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- trazo dibujado

def leer_trazo(archivo):
    """Puntos (lat, lon, altura) del trazo dibujado."""
    with open(archivo, encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    puntos = [(p.latitude, p.longitude, p.elevation) for t in gpx.tracks for s in t.segments for p in s.points]
    if not puntos:
        puntos = [(p.latitude, p.longitude, p.elevation) for r in gpx.routes for p in r.points]
    if not puntos:
        puntos = [(w.latitude, w.longitude, w.elevation) for w in gpx.waypoints]
    puntos = sin_repetidos(puntos)
    if len(puntos) < 2:
        raise ValueError("el trazo no tiene suficientes puntos (se necesitan al menos 2)")
    return puntos


def sin_repetidos(puntos):
    return [p for i, p in enumerate(puntos) if i == 0 or p[:2] != puntos[i - 1][:2]]


def quitar_retrocesos(puntos, max_m):
    """Quita los picos donde la línea se pasa de una esquina y regresa (giro de más de 150°)."""
    quitados = 0
    cambio = max_m > 0
    while cambio:
        cambio = False
        i = 1
        while i < len(puntos) - 1:
            (y0, x0), (y1, x1), (y2, x2) = puntos[i - 1][:2], puntos[i][:2], puntos[i + 1][:2]
            escala_x = 111_320 * np.cos(np.radians(y1))
            a = np.array([(x1 - x0) * escala_x, (y1 - y0) * 111_320])
            b = np.array([(x2 - x1) * escala_x, (y2 - y1) * 111_320])
            largo_a, largo_b = np.linalg.norm(a), np.linalg.norm(b)
            if largo_a > 0 and largo_b > 0:
                if a @ b / (largo_a * largo_b) < -0.87 and min(largo_a, largo_b) <= max_m:
                    del puntos[i]
                    quitados += 1
                    cambio = True
                    continue
            i += 1
        puntos = sin_repetidos(puntos)
    return puntos, quitados


def a_metros(puntos):
    linea = LineString([(lon, lat) for lat, lon, _ in puntos])
    serie = gpd.GeoSeries([linea], crs="EPSG:4326")
    crs_utm = serie.estimate_utm_crs()
    return serie.to_crs(crs_utm).iloc[0], crs_utm


def perfil_altura(puntos, linea_m):
    """Altura de cada vértice del trazo y su distancia desde el inicio."""
    xy = np.array(linea_m.coords)
    acumulado = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    altura = np.array([np.nan if e is None else e for _, _, e in puntos], dtype=float)
    if np.isnan(altura).all():
        return acumulado, np.full(len(acumulado), 3825.0)
    validos = ~np.isnan(altura)
    return acumulado, np.interp(acumulado, acumulado[validos], altura[validos])


# ---------------------------------------------------------------- esquinas del trazo

def cargar_intersecciones():
    if not ARCHIVO_MAPA.exists():
        sys.exit(
            "No se encontró el mapa (data/mapa/juliaca_red.graphml.gz).\n"
            "Ejecuta primero:  python 1_descargar_mapa.py"
        )
    print("Cargando el mapa...")
    with gzip.open(ARCHIVO_MAPA, "rt", encoding="utf-8") as f:
        nodos = ox.graph_to_gdfs(ox.load_graphml(graphml_str=f.read()), edges=False)
    return nodos[nodos["street_count"].fillna(0).astype(int) >= 3]


def esquinas_del_trazo(linea_m, crs_utm, intersecciones, radio, dist_min):
    """Metros desde el inicio en que el trazo pasa por cada esquina (puede pasar dos veces por la misma)."""
    minx, miny, maxx, maxy = gpd.GeoSeries([linea_m], crs=crs_utm).to_crs("EPSG:4326").total_bounds
    cerca = intersecciones.cx[minx - 0.002:maxx + 0.002, miny - 0.002:maxy + 0.002].to_crs(crs_utm)
    cerca = cerca[cerca.distance(linea_m) <= radio]

    distancias = np.arange(0, linea_m.length, 1.0)
    muestras = shapely.get_coordinates(shapely.line_interpolate_point(linea_m, distancias))
    posiciones = []
    for punto in cerca.geometry:
        d = np.hypot(muestras[:, 0] - punto.x, muestras[:, 1] - punto.y)
        dentro = np.flatnonzero(d <= radio)
        if dentro.size == 0:
            continue
        for pasada in np.split(dentro, np.flatnonzero(np.diff(dentro) > 1) + 1):
            posiciones.append(distancias[pasada[np.argmin(d[pasada])]])

    unidas = []
    for p in sorted(posiciones):
        if not unidas or p - unidas[-1] >= dist_min:
            unidas.append(p)
    internas = [p for p in unidas if dist_min < p < linea_m.length - dist_min]
    return np.array([0.0, *internas, linea_m.length])


# ---------------------------------------------------------------- horario

def en_zona(lat, lon, zona):
    return zona["sur"] <= lat <= zona["norte"] and zona["oeste"] <= lon <= zona["este"]


def horario(posiciones, linea_m, crs_utm, p, zona, rng):
    """Segundos desde la salida en que llega a cada esquina, y cuánto se queda parada en cada una."""
    total = float(p["tiempo_total_min"]) * 60
    distancias = np.diff(posiciones)

    medio = gpd.GeoSeries(shapely.line_interpolate_point(linea_m, (posiciones[:-1] + posiciones[1:]) / 2),
                          crs=crs_utm).to_crs("EPSG:4326")
    en_centro = np.array([en_zona(y, x, zona) for x, y in zip(medio.x, medio.y)])
    peso = distancias * np.where(en_centro, float(p.get("factor_trafico_centro", 1.0)), 1.0)
    v = float(p.get("variacion_velocidad", 0))
    peso = peso * rng.uniform(1 - v, 1 + v, size=len(peso))

    pausa_min, pausa_max = p.get("pausa_parada_seg", [5, 35])
    para = rng.random(len(posiciones)) < float(p.get("prob_parada", 0))
    para[0], para[-1] = True, False      # espera pasajeros al salir; al llegar se corta la grabación
    pausas = np.where(para, rng.uniform(pausa_min, pausa_max, size=len(posiciones)), 0.0)
    if pausas.sum() > 0.6 * total:
        pausas *= 0.6 * total / pausas.sum()

    tiempo_tramo = peso / peso.sum() * (total - pausas.sum())
    llegada = np.zeros(len(posiciones))
    for i in range(1, len(posiciones)):
        llegada[i] = llegada[i - 1] + pausas[i - 1] + tiempo_tramo[i - 1]
    llegada[-1] = total
    return llegada, pausas


# ---------------------------------------------------------------- movimiento

def xy_en(linea_m, metros):
    return shapely.get_coordinates(shapely.line_interpolate_point(linea_m, np.asarray(metros, dtype=float)))


def giro_en(linea_m, metros, ventana=15):
    """Cuántos grados gira la ruta en cada posición, mirando 15 m antes y 15 m después."""
    a, b, c = xy_en(linea_m, metros - ventana), xy_en(linea_m, metros), xy_en(linea_m, metros + ventana)
    v1, v2 = b - a, c - b
    cruz = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
    return np.degrees(np.abs(np.arctan2(cruz, (v1 * v2).sum(axis=1))))


def velocidad_en_esquina(giro):
    """Velocidad al pasar una esquina sin parar, como fracción de la velocidad del tramo."""
    return np.select([giro > 50, giro > 20], [0.3, 0.55], 0.8)


def avance_en_tramo(u, v_ini, v_fin, rampa):
    """Fracción del tramo recorrida en la fracción de tiempo u: arranca a v_ini, acelera y frena hasta v_fin."""
    malla = np.linspace(0, 1, 201)
    v = np.ones_like(malla)
    sube = malla < rampa
    v[sube] = v_ini + (1 - v_ini) * malla[sube] / rampa
    baja = malla > 1 - rampa
    v[baja] = np.minimum(v[baja], v_fin + (1 - v_fin) * (1 - malla[baja]) / rampa)
    acumulado = np.concatenate([[0], np.cumsum((v[1:] + v[:-1]) / 2)])
    return np.interp(u, malla, acumulado / acumulado[-1])


def metros_en_el_tiempo(posiciones, llegada, pausas, linea_m, intervalo):
    """Metros recorridos en cada instante de grabación."""
    v_esquina = np.where(pausas > 0, 0.0, velocidad_en_esquina(giro_en(linea_m, posiciones)))
    v_esquina[-1] = 0.0

    segundos = np.append(np.arange(0, llegada[-1], intervalo), llegada[-1])
    metros = np.full_like(segundos, np.nan)
    for i in range(len(posiciones)):
        quieto = (segundos >= llegada[i]) & (segundos <= llegada[i] + pausas[i])
        metros[quieto] = posiciones[i]
        if i == len(posiciones) - 1:
            continue
        partida = llegada[i] + pausas[i]
        duracion = llegada[i + 1] - partida
        en_tramo = (segundos > partida) & (segundos < llegada[i + 1])
        if duracion > 0 and en_tramo.any():
            u = (segundos[en_tramo] - partida) / duracion
            avance = avance_en_tramo(u, v_esquina[i], v_esquina[i + 1], min(0.45, TIEMPO_RAMPA_SEG / duracion))
            metros[en_tramo] = posiciones[i] + (posiciones[i + 1] - posiciones[i]) * avance

    faltan = np.isnan(metros)
    metros[faltan] = np.interp(segundos[faltan], llegada, posiciones)
    return segundos, metros


def suave(t):
    return t * t * (3 - 2 * t)


def desvio_vereda(metros, paradas, rango, rng):
    """Metros hacia la derecha: antes de cada parada se cierra de golpe a la vereda y luego vuelve."""
    desvio = np.zeros_like(metros)
    for s in paradas:
        x = metros - s
        peso = np.zeros_like(metros)
        antes = (x >= -VEREDA_ENTRADA_M) & (x <= 0)
        despues = (x > 0) & (x <= VEREDA_SALIDA_M)
        peso[antes] = suave(1 + x[antes] / VEREDA_ENTRADA_M)
        peso[despues] = suave(1 - x[despues] / VEREDA_SALIDA_M)
        desvio = np.maximum(desvio, rng.uniform(*rango) * peso)
    return desvio


def ruido_gps(n, sigma, rng, memoria=0.9):
    """
    Error del GPS. Cambia de a pocos entre un punto y el siguiente (como en un
    celular real), en vez de saltar al azar en cada lectura.
    """
    error = np.zeros((n, 2))
    error[0] = rng.normal(0, sigma, 2)
    paso = rng.normal(0, sigma * np.sqrt(1 - memoria ** 2), size=(n, 2))
    for i in range(1, n):
        error[i] = memoria * error[i - 1] + paso[i]
    return np.clip(error, -3 * sigma, 3 * sigma)


def puntos_gps(linea_m, metros, paradas, p, rng):
    centro = xy_en(linea_m, metros)
    direccion = xy_en(linea_m, metros + 3) - xy_en(linea_m, metros - 3)
    largo = np.linalg.norm(direccion, axis=1, keepdims=True)
    direccion = np.divide(direccion, largo, out=np.zeros_like(direccion), where=largo > 0)
    derecha = np.column_stack([direccion[:, 1], -direccion[:, 0]])

    lateral = CARRIL_DERECHO_M + desvio_vereda(metros, paradas, p.get("desvio_vereda_m", [2, 4]), rng)
    return centro + derecha * lateral[:, None] + ruido_gps(len(metros), float(p.get("ruido_gps_m", 2.5)), rng)


# ---------------------------------------------------------------- salida

def guardar_gpx(ruta, p, salida, segundos, lat, lon, altura, archivo):
    gpx = gpxpy.gpx.GPX()
    gpx.creator = "JuliacaBus-DS"
    gpx.name = ruta
    gpx.keywords = f"linea {p.get('linea', '')}, {p.get('sentido', '')}"
    gpx.time = salida

    pista = gpxpy.gpx.GPXTrack(name=ruta)
    segmento = gpxpy.gpx.GPXTrackSegment()
    for s, la, lo, h in zip(segundos, lat, lon, altura):
        segmento.points.append(gpxpy.gpx.GPXTrackPoint(
            round(float(la), 6), round(float(lo), 6), elevation=round(float(h), 1),
            time=salida + timedelta(seconds=float(s)),
        ))
    pista.segments.append(segmento)
    gpx.tracks.append(pista)
    archivo.write_text(gpx.to_xml(prettyprint=False), encoding="utf-8")


def generar(archivo, config, intersecciones, reloj):
    ruta = archivo.stem
    p = leer_parametros(ruta)
    if p is None:
        return None
    rng = np.random.default_rng(p.get("semilla", config.get("semilla_aleatoria", 42) + zlib.crc32(ruta.encode())))

    puntos, quitados = quitar_retrocesos(leer_trazo(archivo), config.get("max_retroceso_m", 0))
    if quitados:
        print(f"  Se corrigieron {quitados} retrocesos del trazo.")
    linea_m, crs_utm = a_metros(puntos)
    acumulado, altura_trazo = perfil_altura(puntos, linea_m)
    reloj.marcar("lectura")

    posiciones = esquinas_del_trazo(linea_m, crs_utm, intersecciones,
                                    config["radio_busqueda_m"], config["distancia_min_entre_esquinas_m"])
    reloj.marcar("esquinas")
    llegada, pausas = horario(posiciones, linea_m, crs_utm, p, config["zona_centro"], rng)
    segundos, metros = metros_en_el_tiempo(posiciones, llegada, pausas, linea_m, float(p.get("intervalo_seg", 2)))

    xy = puntos_gps(linea_m, metros, posiciones[pausas > 0], p, rng)
    gps = gpd.GeoSeries(gpd.points_from_xy(xy[:, 0], xy[:, 1]), crs=crs_utm).to_crs("EPSG:4326")
    altura = np.interp(metros, acumulado, altura_trazo) + rng.normal(0, 0.4, len(metros))
    reloj.marcar("movimiento")

    salida = datetime.fromisoformat(f"{p['fecha']}T{p['hora_salida']}").replace(tzinfo=HORA_PERU)
    guardar_gpx(ruta, p, salida, segundos, gps.y, gps.x, altura, CARPETA_SALIDA / f"{ruta}.gpx")

    separacion = shapely.distance(shapely.points(xy), linea_m)
    llegada_hora = salida + timedelta(seconds=float(segundos[-1]))
    reloj.marcar("guardado")
    return {
        "ruta": ruta,
        "puntos": len(segundos),
        "salida": salida.strftime("%H:%M"),
        "llegada": llegada_hora.strftime("%H:%M"),
        "km": linea_m.length / 1000,
        "paradas": int((pausas[1:] > 0).sum()),
        "separacion_media": separacion.mean(),
        "separacion_max": separacion.max(),
    }


def main():
    config = leer_config()
    for carpeta in (CARPETA_DIBUJOS, CARPETA_SALIDA, CARPETA_PARAMETROS):
        carpeta.mkdir(parents=True, exist_ok=True)

    archivos = sorted(CARPETA_DIBUJOS.glob("*.gpx"))
    if not archivos:
        sys.exit("No hay trazos en data/gpx_dibujado/. Copia ahí las rutas dibujadas en gpx.studio.")

    reloj_total = Cronometro()
    intersecciones = cargar_intersecciones()
    reloj_total.marcar("carga_mapa")
    print()

    resultados, tiempos = [], []
    for archivo in archivos:
        print(f"> {archivo.name}")
        reloj = Cronometro()
        try:
            r = generar(archivo, config, intersecciones, reloj)
        except gpxpy.gpx.GPXXMLSyntaxException:
            print("  Error: el archivo no es un GPX válido.")
            continue
        except (ValueError, KeyError) as error:
            print(f"  Error: {error}. Revisa el trazo o sus parámetros.")
            continue
        if r:
            resultados.append(r)
            tiempos.append({"ruta": r["ruta"], "puntos": r["puntos"], "segundos": round(reloj.total(), 3),
                            **{k: round(v, 3) for k, v in reloj.partes.items()}})

    if not resultados:
        sys.exit("\nNo se generó ningún recorrido.")

    print("\nResumen")
    print(f"  {'Ruta':<26}{'Puntos':>8}{'Horario':>15}{'Km':>7}{'Paradas':>9}{'Error GPS':>17}")
    for r in resultados:
        horario_txt = f"{r['salida']}-{r['llegada']}"
        error_txt = f"{r['separacion_media']:.1f} m (máx {r['separacion_max']:.0f})"
        print(f"  {r['ruta']:<26}{r['puntos']:>8}{horario_txt:>15}{r['km']:>7.2f}{r['paradas']:>9}{error_txt:>17}")
    print("\n  Error GPS = separación entre cada punto y el eje de la calle.")
    reloj_total.marcar("rutas")
    guardar_ejecucion("generar_recorridos", reloj_total, tiempos,
                      km=round(sum(r["km"] for r in resultados), 2))
    print("\nListo. Recorridos en data/gpx_entrada/ (tiempos en data/salida/ejecucion/)")


if __name__ == "__main__":
    main()
