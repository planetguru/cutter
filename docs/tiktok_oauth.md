# TikTok API Setup

> **Important:** TikTok does not approve production API access for personal or
> internal-use apps — a production audit submission will be rejected with
> "TikTok for Developers currently does not support personal or internal company
> use." For a personal tool like cutter, the supported route is **sandbox mode**,
> which restricts what the API can do:
>
> - Only TikTok accounts added as sandbox **target users** can authenticate.
> - Content posted by unaudited apps is forced to private (`SELF_ONLY`)
>   visibility when using Direct Post.
>
> cutter therefore defaults to **inbox upload mode** (`TIKTOK_POST_MODE=inbox`):
> the clip is uploaded to your TikTok inbox as a draft, you get an in-app
> notification, and you publish it through TikTok's normal creation flow — where
> public visibility is still available. The caption is sent to you on WhatsApp
> to paste in. Set `TIKTOK_POST_MODE=direct` only if the app ever passes
> TikTok's audit.

## 1. Register a Developer Account

1. Go to [developer.tiktok.com](https://developer.tiktok.com) and sign in with your TikTok account.
2. Click **Manage apps** → **Create app**.
3. Fill in app name, description, and category (e.g. "Content Tools").

## 2. Create a Sandbox

1. In your app's page, switch the environment toggle from **Production** to **Sandbox** and create a sandbox.
2. Under **Sandbox settings → Target users**, add the TikTok account you post from (up to 10 accounts per sandbox).
3. Note the **sandbox Client key** and **Client secret** — these are different from the production ones.

## 3. Configure the App

1. Under **Products**, add **Content Posting API** (in the sandbox environment).
2. Under **Redirect URIs**, add: `http://localhost:8080/callback`

## 4. Request Required Scopes

In **Scopes**, enable:
- `video.upload` (required for inbox mode)
- `video.publish` (only used by direct mode)

Sandbox scopes work immediately without review.

## 5. Add Credentials to .env

```
TIKTOK_CLIENT_KEY=your_sandbox_client_key
TIKTOK_CLIENT_SECRET=your_sandbox_client_secret
# optional, defaults to inbox; "direct" requires an audited production app
TIKTOK_POST_MODE=inbox
```

## 6. Run the OAuth Flow

```bash
cutter auth tiktok
```

This opens your browser, prompts you to log in (with an account listed as a
sandbox target user), and writes `TIKTOK_ACCESS_TOKEN`, `TIKTOK_REFRESH_TOKEN`,
and `TIKTOK_OPEN_ID` to `.env` automatically.

## Posting Flow (inbox mode)

1. `cutter daily` uploads the approved clip to TikTok.
2. TikTok sends you an in-app inbox notification; cutter sends the caption via WhatsApp.
3. Open the notification, paste the caption, choose visibility, and post.

## Token Refresh

Access tokens expire after 24 hours. The tool refreshes automatically on the next run using the stored refresh token. If the refresh token also expires (after 365 days), re-run `cutter auth tiktok`.
