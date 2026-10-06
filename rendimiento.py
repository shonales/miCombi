"""
Tiempos de ejecución y datos del equipo donde corren los scripts.

Cada paso guarda sus tiempos en data/salida/ejecucion/<paso>.json y se arma
data/salida/ejecucion/REPORTE.md con todo junto.
"""

import ctypes
import json
import os
import platform
import sys
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "data" / "salida" / "ejecucion"
LIBRERIAS = ["osmnx", "networkx", "shapely", "geopandas", "numpy", "pandas", "gpxpy", "folium", "streamlit"]
PASOS = {
    "descargar_mapa": "1. Descargar mapa",
    "generar_recorridos": "2. Generar recorridos GPS",
    "procesar_rutas": "3. Procesar rutas",
}
PARTES = {
    "carga_mapa": "carga del mapa",
    "descarga": "descarga",
    "lectura": "lectura",
    "esquinas": "esquinas",
    "movimiento": "movimiento y GPS",
    "ajuste": "ajuste a las calles",
    "reconstruccion": "ruta y paraderos",
    "horarios": "horarios",
    "guardado": "guardado",
    "rutas": "todas las rutas",
}


def mayuscula(texto):
    """Primera letra en mayúscula sin tocar el resto ("movimiento y GPS" -> "Movimiento y GPS")."""
    return texto[:1].upper() + texto[1:]


class Cronometro:
    """c.marcar("lectura") suma a "lectura" los segundos pasados desde la marca anterior."""

    def __init__(self):
        self.inicio = self.anterior = time.perf_counter()
        self.partes = {}

    def marcar(self, nombre):
        ahora = time.perf_counter()
        self.partes[nombre] = self.partes.get(nombre, 0.0) + ahora - self.anterior
        self.anterior = ahora

    def total(self):
        return time.perf_counter() - self.inicio


# ---------------------------------------------------------------- equipo

def _registro(ruta, valor):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ruta) as clave:
            return str(winreg.QueryValueEx(clave, valor)[0]).strip()
    except (OSError, ImportError):
        return ""


def _ram_gb():
    if sys.platform == "win32":
        kb = ctypes.c_ulonglong(0)
        if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kb)):
            return round(kb.value / 2 ** 20)
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30, 1)
    except (ValueError, OSError, AttributeError):
        return None


def _nucleos_fisicos():
    if sys.platform != "win32":
        return None

    class Info(ctypes.Structure):
        _fields_ = [("mascara", ctypes.c_size_t), ("relacion", ctypes.c_int), ("datos", ctypes.c_ulonglong * 2)]

    largo = ctypes.c_ulong(0)
    ctypes.windll.kernel32.GetLogicalProcessorInformation(None, ctypes.byref(largo))
    lista = (Info * (largo.value // ctypes.sizeof(Info)))()
    if not ctypes.windll.kernel32.GetLogicalProcessorInformation(lista, ctypes.byref(largo)):
        return None
    return sum(1 for i in lista if i.relacion == 0)


def _version(libreria):
    try:
        return version(libreria)
    except PackageNotFoundError:
        return "-"


def equipo():
    datos = {
        "equipo": platform.node(),
        "procesador": platform.processor(),
        "nucleos": None,
        "hilos": os.cpu_count(),
        "ram_gb": _ram_gb(),
        "sistema": f"{platform.system()} {platform.release()} (versión {platform.version()})",
        "python": platform.python_version(),
    }
    if sys.platform == "win32":
        bios = r"HARDWARE\DESCRIPTION\System\BIOS"
        marca = _registro(bios, "SystemManufacturer").title()
        modelo = _registro(bios, "SystemVersion") or _registro(bios, "SystemProductName")
        codigo = _registro(bios, "SystemProductName")
        datos["equipo"] = f"{marca} {modelo}" + (f" ({codigo})" if codigo and codigo != modelo else "")
        datos["procesador"] = _registro(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                                        "ProcessorNameString") or datos["procesador"]
        datos["nucleos"] = _nucleos_fisicos()
    datos["librerias"] = {nombre: _version(nombre) for nombre in LIBRERIAS}
    return datos


def memoria_maxima_mb():
    """Memoria RAM más alta que usó el programa."""
    try:
        if sys.platform == "win32":
            from ctypes import wintypes

            class Contadores(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("fallos", wintypes.DWORD), ("pico", ctypes.c_size_t)] + \
                           [(f"otro{i}", ctypes.c_size_t) for i in range(7)]

            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            c = Contadores()
            c.cb = ctypes.sizeof(Contadores)
            kernel.K32GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb)
            return round(c.pico / 2 ** 20)
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except Exception:
        return None


# ---------------------------------------------------------------- guardar y mostrar

def guardar(paso, cronometro, rutas, **extra):
    """Guarda el JSON del paso, actualiza el REPORTE.md y muestra el resumen en pantalla."""
    datos = {
        "paso": paso,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_s": round(cronometro.total(), 2),
        "partes_s": {k: round(v, 2) for k, v in cronometro.partes.items()},
        "memoria_max_mb": memoria_maxima_mb(),
        "rutas": rutas,
        **extra,
        "equipo": equipo(),
    }
    CARPETA.mkdir(parents=True, exist_ok=True)
    (CARPETA / f"{paso}.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    escribir_reporte()
    imprimir(datos)


def describir_equipo(e):
    nucleos = f"{e['nucleos']} núcleos / {e['hilos']} hilos" if e.get("nucleos") else f"{e['hilos']} hilos"
    return f"{e['equipo']} · {e['procesador']} · {nucleos} · {e['ram_gb']} GB de RAM"


def imprimir(datos):
    e = datos["equipo"]
    print("\nTiempo de ejecución")
    print(f"  Equipo:  {describir_equipo(e)}")
    print(f"  Sistema: {e['sistema']} · Python {e['python']}")
    for parte, segundos in datos["partes_s"].items():
        print(f"  {mayuscula(PARTES.get(parte, parte)):<20}{segundos:>8.2f} s")
    if datos["rutas"]:
        print(f"  {'Ruta':<26}{'Tiempo':>9}{'Puntos':>9}{'Puntos/s':>10}")
        for r in datos["rutas"]:
            print(f"  {r['ruta']:<26}{r['segundos']:>7.2f} s{r['puntos']:>9}{r['puntos'] / r['segundos']:>10.0f}")
    memoria = f" · memoria máxima {datos['memoria_max_mb']} MB" if datos["memoria_max_mb"] else ""
    print(f"  Total: {datos['total_s']:.2f} s{memoria}")


def leer_todo():
    resultados = {}
    for paso in PASOS:
        archivo = CARPETA / f"{paso}.json"
        if archivo.exists():
            resultados[paso] = json.loads(archivo.read_text(encoding="utf-8"))
    return resultados


def escribir_reporte():
    """REPORTE.md con el equipo y los tiempos de todos los pasos que ya se ejecutaron."""
    resultados = leer_todo()
    if not resultados:
        return
    e = list(resultados.values())[-1]["equipo"]
    lineas = [
        "# Resultados de ejecución",
        "",
        "## Equipo",
        "",
        "| | |",
        "|---|---|",
        f"| Equipo | {e['equipo']} |",
        f"| Procesador | {e['procesador']} |",
        f"| Núcleos / hilos | {e.get('nucleos') or '-'} / {e['hilos']} |",
        f"| Memoria RAM | {e['ram_gb']} GB |",
        f"| Sistema operativo | {e['sistema']} |",
        f"| Python | {e['python']} |",
        f"| Librerías | {', '.join(f'{k} {v}' for k, v in e['librerias'].items())} |",
        "",
        "## Tiempo total por paso",
        "",
        "| Paso | Fecha | Tiempo total | Memoria máxima |",
        "|---|---|---|---|",
    ]
    for paso, d in resultados.items():
        lineas.append(f"| {PASOS[paso]} | {d['fecha']} | {d['total_s']:.2f} s | {d.get('memoria_max_mb') or '-'} MB |")

    for paso, d in resultados.items():
        if not d["rutas"]:
            continue
        partes = [p for p in PARTES if any(p in r for r in d["rutas"])]
        lineas += ["", f"## {PASOS[paso]}: tiempo por ruta (segundos)", ""]
        if "carga_mapa" in d["partes_s"]:
            lineas += [f"Carga del mapa: {d['partes_s']['carga_mapa']:.2f} s", ""]
        lineas.append("| Ruta | Puntos | " + " | ".join(mayuscula(PARTES[p]) for p in partes) + " | Total | Puntos/s |")
        lineas.append("|---" * (len(partes) + 4) + "|")
        for r in d["rutas"]:
            valores = " | ".join(f"{r.get(p, 0):.2f}" for p in partes)
            lineas.append(f"| {r['ruta']} | {r['puntos']:,} | {valores} | **{r['segundos']:.2f}** | "
                          f"{r['puntos'] / r['segundos']:,.0f} |")
        tiempos = [r["segundos"] for r in d["rutas"]]
        lineas += ["", f"Promedio por ruta: {sum(tiempos) / len(tiempos):.2f} s · "
                       f"mínimo {min(tiempos):.2f} s · máximo {max(tiempos):.2f} s"]
    lineas.append("")
    (CARPETA / "REPORTE.md").write_text("\n".join(lineas), encoding="utf-8")
