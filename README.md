# iPhone Duo pickup watcher

Revisa cada ~45 segundos si hay **iPhone Duo** (por defecto 256GB; 512GB, 1TB y 2TB se activan desde Telegram; Star White o Night Sky) disponible para **retiro en tienda** en los Apple Store a ≤120 millas de Delray Beach, FL, y avisa por WhatsApp y/o Telegram.

Usa el mismo servicio que consulta la página de Apple al apretar "Check availability" (`/shop/retail/pickup-message`). No compra ni reserva nada.

## Comandos de Telegram

Mientras corre, el bot responde en segundos (solo a tu chat):

- `busca duo`: iPhone Duo 256/512GB, Star White / Night Sky
- `busca 18`: iPhone 18 Pro y Pro Max 256GB, todos los colores
- `estado`: si está andando, cuántos chequeos lleva y el resultado del último
- `set memoria 256` / `512` / `1tb` / `2tb` / `todos`: elige qué memorias del Duo busca la automática (por defecto: `watch_storage` en `config.json`)
- `set millas 100`: descarta tiendas a más de esa distancia (5–120), en la automática y en `busca`. Responde qué tiendas quedan adentro y afuera
- `set segundos 45`: cada cuántos segundos busca la automática (20–600; menos de 45 = más riesgo de bloqueo). De noche nunca busca más seguido que de día
- `set`: muestra la configuración actual
- `pausa` / `reanuda`: frena o retoma la búsqueda automática
- La pausa, la memoria, las millas y los segundos se guardan en el mensaje fijado del chat, así sobreviven a los reinicios. No lo desfijes.
- `prueba compra`: simula un aviso para practicar
- `menu`: lista de comandos (también desde el botón ☰ Menú)
- cualquier otro texto: "No entendí, escribí menu"

Todos los días a las 9 (Argentina) manda un "sigo vivo" con el estado.

Primero responde qué modelos va a buscar y después el resultado. Los productos se definen en `config.json` → `products`. Con `"watch": true` también entran en la búsqueda automática; hoy solo el Duo.

## Cómo comprar cuando llega el aviso (compra asistida)

No se automatiza login, carrito ni pago, y no se guarda ningún dato sensible. El aviso llega así:

1. **🚨 HAY STOCK: modelo · color · memoria**: corto, sin link (es el que suena y se ve en la pantalla bloqueada).
2. **Un mensaje por tienda** (📍 nombre, millas, cuándo se retira), de la más cercana a la más lejana, con cada variante disponible ahí y su link directo (unlocked). Como máximo 4 (`max_store_messages`); el resto va en "➕ También en".

Si aparece stock en una tienda nueva mientras otra sigue con stock, solo llega el aviso de la nueva. `busca duo` / `busca 18` responden con el mismo formato.

Pasos: tocar el link → Add to Bag → Check Out → elegir **pickup** en la tienda del aviso → pagar con la **tarjeta guardada** en la cuenta Apple (puede pedir CVV). El link es el mismo para todas las tiendas: la tienda se elige en el checkout.

Apple **no reserva** la unidad en la bolsa: se asigna recién al tocar *Place Order*. Si alguien paga antes, sale *"The delivery and pickup option you have chosen is no longer available"*. En ese caso volvé un paso atrás y probá otro horario u otra de las tiendas del aviso.

Cuando el stock desaparece llega `⌛ Se agotó … duró ~X` (precisión ± el intervalo de chequeo). Sirve para saber cuánto tiempo hay de verdad.

Consejos: desde la compu suele ser más rápido; AppleCare → "No", Trade-in → "No" sin leer; CVV a mano.

Preparación (una vez): sesión de Apple iniciada en Chrome del celular (apple.com/shop/account), con "mantener sesión" marcado; notificaciones de Telegram con sonido. Para practicar, mandale `prueba compra` al bot: simula un aviso con un 18 Pro en stock. Recorré el flujo hasta "Place Order" **sin confirmar**.

## Estructura

- `services/apple_checker.py`: consulta Apple y filtra por distancia
- `services/notifier.py`: WhatsApp (CallMeBot) y Telegram
- `main.py`: loop; avisa solo cuando un modelo *aparece* en una tienda
- `config.json`: modelos, ZIPs, radio, intervalo
- `.github/workflows/check.yml`: corre en GitHub Actions en tandas de ~5,5 h, sin cortes

## Secrets (Settings → Secrets and variables → Actions)

| Secret | Para qué |
|---|---|
| `CALLMEBOT_PHONE` | tu número con código de país, ej. `+5491112345678` |
| `CALLMEBOT_APIKEY` | la apikey que te manda CallMeBot |
| `TELEGRAM_BOT_TOKEN` | opcional, token del bot de @BotFather |
| `TELEGRAM_CHAT_ID` | opcional, tu chat id |

## Uso local

```bash
python3 main.py --once          # un chequeo
python3 main.py --test-notify   # manda mensaje de prueba
python3 main.py --duration 60   # loop 60 min
```

## Ajustes

La distancia es en **línea recta** desde Delray Beach; manejando suele ser un 10–25% más. Se consulta a Apple con un ZIP (`search_zips`), que devuelve las tiendas hasta ~120 mi, y después se filtra por `max_miles`.


- Agregar Orlando (~2h45 manejando): `max_miles` → `165`
