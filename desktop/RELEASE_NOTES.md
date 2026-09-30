## Instalar

- **Mac con Apple Silicon (M1 o posterior):** `Snapcut_*_aarch64.dmg`
- **Mac con Intel:** `Snapcut_*_x64.dmg`
- **Windows 10/11:** `Snapcut_*_x64-setup.exe`

Abre el `.dmg` y arrastra Snapcut a Aplicaciones. En Windows, ejecuta el instalador.

La app aún no está firmada, así que el sistema avisa la primera vez:

- **macOS:** al abrirla dice que no puede comprobar la app. Ve a *Ajustes del Sistema → Privacidad y seguridad*, baja hasta el aviso de Snapcut y pulsa *Abrir igualmente*. Solo hace falta una vez.
- **Windows:** SmartScreen muestra "Windows protegió su PC". Pulsa *Más información* → *Ejecutar de todas formas*.

## Código fuente

Snapcut es software libre (GPL-3.0 o posterior). `ffmpeg-sources-*.tar.gz` contiene el código fuente exacto de ffmpeg y de las bibliotecas que lleva dentro cada versión, junto con su configuración de compilación.
