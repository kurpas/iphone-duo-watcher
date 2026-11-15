# iPhone Duo pickup watcher

Revisa cada ~30 segundos si hay **iPhone Duo** (256GB o 512GB, Star White o Night Sky) disponible para **retiro en tienda** en los Apple Store a ≤120 millas de Delray Beach, FL, y avisa por WhatsApp y/o Telegram.

Usa el mismo servicio que consulta la página de Apple al apretar "Check availability" (`/shop/retail/pickup-message`). No compra ni reserva nada.

## Comandos de Telegram

Mientras corre, el bot responde en segundos (solo a tu chat):

- `busca duo`: iPhone Duo 256/512GB, Star White / Night Sky
- `busca 18`: iPhone 18 Pro y Pro Max 256GB, todos los colores
- cualquier otro texto: muestra la ayuda

Primero responde qué modelos va a buscar y después el resultado. Los productos se definen en `config.json` → `products`. Con `"watch": true` también entran en la búsqueda automática; hoy solo el Duo.

## Cómo comprar cuando llega el aviso (compra asistida)

No se automatiza login, carrito ni pago, y no se guarda ningún dato sensible. El aviso llega en 2 mensajes:

1. **Arriba:** modelo, tienda más cercana y link directo, con color y memoria ya elegidos (unlocked).
2. **Abajo:** detalle de todos los modelos y tiendas con stock, cada uno con su link.

Pasos: tocar el link → Add to Bag → Check Out → elegir **pickup** en la tienda del aviso → pagar con la **tarjeta guardada** en la cuenta Apple (puede pedir CVV).

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

- Agregar Orlando (~2h45 manejando): `max_miles` → `165`
- Agregar 1TB: sumar `MK1J4LL/A` y `MK1L4LL/A` en `parts`
