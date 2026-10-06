# Resultados de ejecución

## Equipo

| | |
|---|---|
| Equipo | Lenovo LOQ 15APH8 (82XT) |
| Procesador | AMD Ryzen 7 7840HS w/ Radeon 780M Graphics |
| Núcleos / hilos | 8 / 16 |
| Memoria RAM | 16 GB |
| Sistema operativo | Windows 11 (versión 10.0.26200) |
| Python | 3.13.13 |
| Librerías | osmnx 2.1.1, networkx 3.7, shapely 2.1.2, geopandas 1.2.0, numpy 2.5.3, pandas 3.0.6, gpxpy 1.6.2, folium 0.20.0, streamlit 1.65.0 |

## Tiempo total por paso

| Paso | Fecha | Tiempo total | Memoria máxima |
|---|---|---|---|
| 2. Generar recorridos GPS | 2026-10-06 16:37:30 | 2.09 s | 356 MB |
| 3. Procesar rutas | 2026-10-06 16:37:36 | 4.42 s | 366 MB |

## 2. Generar recorridos GPS: tiempo por ruta (segundos)

Carga del mapa: 1.21 s

| Ruta | Puntos | Lectura | Esquinas | Movimiento y GPS | Guardado | Total | Puntos/s |
|---|---|---|---|---|---|---|---|
| Ayabacas-Bosque-LM-3 | 2,401 | 0.05 | 0.10 | 0.05 | 0.03 | **0.23** | 10,671 |
| Bosque-Ayabacas-LM-3 | 2,071 | 0.04 | 0.09 | 0.03 | 0.03 | **0.18** | 11,255 |
| ESCURI-MOLINA-L23 | 1,831 | 0.04 | 0.06 | 0.02 | 0.02 | **0.14** | 12,715 |
| GUADALUPE-RINCONADA-L1 | 1,441 | 0.04 | 0.02 | 0.01 | 0.01 | **0.09** | 15,663 |
| MOLINA-ESCURI-L23 | 1,621 | 0.04 | 0.06 | 0.02 | 0.02 | **0.13** | 12,188 |
| RINCONADA-GUADALUPE-L1 | 1,651 | 0.04 | 0.03 | 0.01 | 0.02 | **0.10** | 16,847 |

Promedio por ruta: 0.15 s · mínimo 0.09 s · máximo 0.23 s

## 3. Procesar rutas: tiempo por ruta (segundos)

Carga del mapa: 2.56 s

| Ruta | Puntos | Lectura | Ajuste a las calles | Ruta y paraderos | Horarios | Guardado | Total | Puntos/s |
|---|---|---|---|---|---|---|---|---|
| Ayabacas-Bosque-LM-3 | 2,401 | 0.07 | 0.15 | 0.07 | 0.04 | 0.11 | **0.45** | 5,336 |
| Bosque-Ayabacas-LM-3 | 2,071 | 0.06 | 0.07 | 0.07 | 0.03 | 0.10 | **0.32** | 6,472 |
| ESCURI-MOLINA-L23 | 1,831 | 0.05 | 0.14 | 0.05 | 0.03 | 0.08 | **0.34** | 5,385 |
| GUADALUPE-RINCONADA-L1 | 1,441 | 0.04 | 0.07 | 0.03 | 0.02 | 0.06 | **0.23** | 6,265 |
| MOLINA-ESCURI-L23 | 1,621 | 0.05 | 0.06 | 0.05 | 0.03 | 0.07 | **0.26** | 6,235 |
| RINCONADA-GUADALUPE-L1 | 1,651 | 0.04 | 0.04 | 0.03 | 0.03 | 0.12 | **0.25** | 6,604 |

Promedio por ruta: 0.31 s · mínimo 0.23 s · máximo 0.45 s
