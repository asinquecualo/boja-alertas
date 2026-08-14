# Alertas BOJA – Auxiliar Administrativo

Vigila el feed oficial del BOJA de la sección 2.2 y envía a Telegram las publicaciones que coincidan con términos relacionados con Auxiliar Administrativo / Administrativo C2.

## 1. Crear el bot

En Telegram abre `@BotFather`, usa `/newbot` y guarda el token **solo como secreto de GitHub**.

Después abre tu bot y pulsa **Start**.

## 2. Obtener CHAT_ID

Abre en el navegador:

`https://api.telegram.org/bot<TU_TOKEN>/getUpdates`

Busca `"chat":{"id": ...}`. Ese número es tu `TELEGRAM_CHAT_ID`.

No publiques el token.

## 3. Crear el repositorio

Sube estos archivos a un repositorio de GitHub.

## 4. Secrets

En `Settings → Secrets and variables → Actions → New repository secret` crea:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 5. Probar

En `Actions → Vigilar BOJA → Run workflow`.

La primera ejecución marca como vistos los elementos existentes y **no envía una avalancha de avisos antiguos**. Las publicaciones nuevas que coincidan con el filtro sí generan un mensaje.

## Fuente

Fuente oficial Atom del BOJA, sección 2.2:

`https://www.juntadeandalucia.es/boja/distribucion/s53.xml`

El Portal de Datos Abiertos de la Junta también ofrece datos y API del BOJA.

## Personalizar el filtro

Edita `INCLUDE` y `EXCLUDE` en `src/boja_alert.py`.

El filtro es deliberadamente amplio para reducir el riesgo de perder una convocatoria por una diferencia de redacción.
