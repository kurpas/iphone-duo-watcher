# iPhone Duo pickup watcher

Revisa cada ~75 segundos si hay **iPhone Duo** (256GB o 512GB, Star White o Night Sky) disponible para **retiro en tienda** en los Apple Store a ≤120 millas de Delray Beach, FL, y avisa por WhatsApp y/o Telegram.

Usa el mismo servicio que consulta la página de Apple al apretar "Check availability" (`/shop/retail/pickup-message`). No compra ni reserva nada.

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
