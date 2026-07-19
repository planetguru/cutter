# Telegram Setup

Telegram is the approval/notification channel for cutter: bots never expire,
messaging is free, and video files up to 50 MB can be sent straight into the
chat — so approval previews and TikTok hand-off clips arrive as playable
videos, no hosting involved.

## 1. Create a Bot

1. In Telegram, message **@BotFather**.
2. Send `/newbot`, pick a display name and a username (must end in `bot`).
3. BotFather replies with an **HTTP API token** — copy it.

## 2. Add the Token to .env

```
TELEGRAM_BOT_TOKEN=123456789:AAF...your-token...
```

## 3. Discover Your Chat ID

```bash
cutter auth telegram
```

Open the bot link it prints (`https://t.me/<yourbot>`) and send it any
message. The command detects your chat and writes `TELEGRAM_CHAT_ID` to `.env`.

## 4. Done

The conversation commands:

- approval replies: `yes` / `no` / `no more today`, edits via `title:` /
  `desc:` / `tiktok:` / `instagram:` / `tags:`
- `queue:https://www.youtube.com/watch?v=...` to queue a video
- `reset` to wipe state on the next daily run

The TikTok manual hand-off sends the full-quality clip file into the chat
(save to camera roll → post in the TikTok app) followed by the caption to
paste.

## Notes

- Telegram's servers keep unread bot updates for ~24 h; cutter journals every
  message it sees into `telegram_state.json` in the workdir, so `queue:` and
  `reset` messages survive longer gaps between runs than that.
- Group chats also work if you add the bot to a group and use that chat's ID,
  but the setup command expects a direct message.
