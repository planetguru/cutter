# WhatsApp Approval Setup (Twilio)

The approval flow sends clip details to your WhatsApp number and waits for your reply before posting. It uses Twilio's WhatsApp API with polling — no web server or public URL required.

## 1. Create a Twilio Account

1. Sign up at [twilio.com](https://twilio.com) (free trial is sufficient for personal use).
2. From the Console Dashboard, note your **Account SID** and **Auth Token**.

## 2. Enable the WhatsApp Sandbox

Twilio's sandbox lets you test with your personal WhatsApp number immediately, without any business approval.

1. In the Twilio Console, go to **Messaging → Try it out → Send a WhatsApp message**.
2. Follow the instructions to join the sandbox — you'll send a code like `join <word>-<word>` to the sandbox number (e.g. `+1 415 523 8886`) from your WhatsApp.
3. Note the **sandbox number** — it's your `TWILIO_WHATSAPP_FROM` value.

> For production use (sending from your own branded number), you'd need to apply for a WhatsApp Business API number, but the sandbox works indefinitely for personal automation.

## 3. Add Credentials to .env

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
TWILIO_WHATSAPP_TO=whatsapp:+447700900000   # your number in E.164 format
```

`TWILIO_WHATSAPP_TO` is your personal WhatsApp number — the one you joined the sandbox from.

## 4. Usage

```bash
# Process clips and ask for approval before each post
cutter run --url "https://youtube.com/watch?v=..." --post both --approve
```

When a clip is ready, you'll receive a WhatsApp message like:

> 📹 *Clip 1/5* — clip_000.mp4
>
> *TikTok caption:*
> Have you ever seen a sunset this dramatic? 🌅 ...
>
> *Instagram caption:*
> Golden hour magic over the hills...
>
> *Hashtags:* #sunset #goldenHour #nature #fyp (+3 more)
>
> Reply:
>   *yes* — post this clip
>   *no* — skip (move to withheld)
>   *no more today* — stop until tomorrow
>   *title: ...*  |  *desc: ...*  |  *tiktok: ...*  |  *instagram: ...*  |  *tags: #a #b*

## Reply Commands

| Reply | Effect |
|-------|--------|
| `yes` | Post the clip immediately |
| `no` | Move clip to `withheld/` folder, offer next clip |
| `no more today` | Stop processing for today; remaining clips resume on next run |
| `title: new title` | Replace both TikTok and Instagram captions |
| `tiktok: caption text` | Update TikTok caption only |
| `instagram: caption text` | Update Instagram caption only |
| `desc: caption text` | Alias for `title:` |
| `tags: #food #travel` | Replace all hashtags |

After editing, the bot shows you the updated version. Keep editing or reply `yes`/`no` when ready.

## Resuming After "No More Today"

The tool stores state locally in `approval_state.json` inside the working directory. When you run `cutter run` again the next day with the same `--url`, it picks up where you left off — skipping already-posted clips and offering the remaining pending ones.

Withheld clips are stored in `{workdir}/{video_id}/withheld/` and listed with `cutter withheld`.
