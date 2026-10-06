"""
Paso 1: descarga la red de calles de Juliaca y San Miguel desde OpenStreetMap.

Uso:
    python 1_descargar_mapa.py            (usa el mapa guardado si ya existe)
    python 1_descargar_mapa.py --forzar   (vuelve a descargarlo)
"""

import argparse
import gzip
import json
import sys
from pathlib import Path

import osmnx as ox

from rendimiento import Cronometro, guardar as guardar_ejecucion

BASE = Path(__file__).resolve().parent
CARPETA_MAPA = BASE / "data" / "mapa"
ARCHIVO_MAPA = CARPETA_MAPA / "juliaca_red.graphml.gz"

ox.settings.cache_folder = str(CARPETA_MAPA / "cache")


def leer_config():
    ruta = BASE / "config.json"
    if not ruta.exists():
        sys.exit("No se encontró config.json en la carpeta del proyecto.")
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def descargar_por_nombre(lugares, tipo_red):
    print("Buscando por nombre:")
    for lugar in lugares:
        print(f"  - {lugar}")
    return ox.graph_from_place(lugares, network_type=tipo_red)


def descargar_por_bbox(bbox, tipo_red):
    print(f"Usando el recuadro de respaldo: N {bbox['norte']}, S {bbox['sur']}, "
          f"E {bbox['este']}, O {bbox['oeste']}")
    # OSMnx 2 recibe el recuadro como (oeste, sur, este, norte)
    caja = (bbox["oeste"], bbox["sur"], bbox["este"], bbox["norte"])
    return ox.graph_from_bbox(caja, network_type=tipo_red)


def descargar(config):
    tipo_red = config.get("tipo_red", "drive")
    try:
        return descargar_por_nombre(config["lugares"], tipo_red)
    except Exception as error:
        print(f"\nNo se pudo descargar por nombre ({type(error).__name__}: {error}).")

    try:
        return descargar_por_bbox(config["bbox_respaldo"], tipo_red)
    except Exception as error:
        sys.exit(
            "\nTampoco se pudo descargar con el recuadro de respaldo.\n"
            f"Detalle: {type(error).__name__}: {error}\n"
            "Revisa tu conexión a internet e inténtalo otra vez."
        )


def guardar(grafo):
    """Guarda el mapa comprimido (pasa de unos 14 MB a poco más de 1 MB)."""
    temporal = CARPETA_MAPA / "juliaca_red.graphml"
    ox.save_graphml(grafo, temporal)
    with open(temporal, "rb") as origen, gzip.open(ARCHIVO_MAPA, "wb", compresslevel=9) as destino:
        destino.write(origen.read())
    temporal.unlink()


def cargar():
    with gzip.open(ARCHIVO_MAPA, "rt", encoding="utf-8") as f:
        return ox.load_graphml(graphml_str=f.read())


def resumen(grafo):
    intersecciones = sum(1 for _, d in grafo.nodes(data=True) if int(d.get("street_count", 0)) >= 3)
    print("\nResumen del mapa")
    print(f"  Nodos:          {grafo.number_of_nodes():,}")
    print(f"  Aristas:        {grafo.number_of_edges():,}")
    print(f"  Intersecciones: {intersecciones:,}  (nodos donde se cruzan 3 o más calles)")


def main():
    parser = argparse.ArgumentParser(description="Descarga la red vial de Juliaca y San Miguel.")
    parser.add_argument("--forzar", action="store_true", help="descarga de nuevo aunque ya exista el mapa")
    args = parser.parse_args()

    config = leer_config()
    CARPETA_MAPA.mkdir(parents=True, exist_ok=True)

    if ARCHIVO_MAPA.exists() and not args.forzar:
        print(f"El mapa ya existe: {ARCHIVO_MAPA.relative_to(BASE)}")
        print("Si quieres descargarlo otra vez usa:  python 1_descargar_mapa.py --forzar")
        resumen(cargar())
        return

    print("Descargando la red de calles desde OpenStreetMap (puede tardar unos minutos)...\n")
    reloj = Cronometro()
    grafo = descargar(config)
    reloj.marcar("descarga")
    guardar(grafo)
    reloj.marcar("guardado")
    print(f"\nMapa guardado en {ARCHIVO_MAPA.relative_to(BASE)} ({ARCHIVO_MAPA.stat().st_size / 2 ** 20:.1f} MB)")
    resumen(grafo)
    guardar_ejecucion("descargar_mapa", reloj, [], nodos=grafo.number_of_nodes(), aristas=grafo.number_of_edges())


if __name__ == "__main__":
    main()
