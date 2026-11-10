# src/

Código fuente del paquete `corolla_monitor`. Un subpaquete por etapa del workflow:

```
recoleccion  ->  actualizacion  ->  analisis  ->  correo
                      |                              ^
                 base_datos                          |
        logs (transversal)      automatizacion (orquesta todo)
```

Flujo diario: 8:00 `recoleccion` + `actualizacion` + `analisis` → 11:00 `correo`.
