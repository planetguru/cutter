# TikTok API Setup

## 1. Register a Developer Account

1. Go to [developer.tiktok.com](https://developer.tiktok.com) and sign in with your TikTok account.
2. Click **Manage apps** → **Create app**.
3. Fill in app name, description, and category (e.g. "Content Tools").

## 2. Configure the App

1. Under **Products**, add **Content Posting API**.
2. Under **Redirect URIs**, add: `http://localhost:8080/callback`
3. Note your **Client key** and **Client secret** from the app settings.

## 3. Request Required Scopes

In **Scopes**, request:
- `video.upload`
- `video.publish`

> For a sandbox/personal app these work immediately. Production publishing to others' accounts requires TikTok approval.

## 4. Add Credentials to .env

```
TIKTOK_CLIENT_KEY=your_client_key
TIKTOK_CLIENT_SECRET=your_client_secret
```

## 5. Run the OAuth Flow

```bash
cutter auth tiktok
```

This opens your browser, prompts you to log in, and writes `TIKTOK_ACCESS_TOKEN`, `TIKTOK_REFRESH_TOKEN`, and `TIKTOK_OPEN_ID` to `.env` automatically.

## Token Refresh

Access tokens expire after 24 hours. The tool refreshes automatically on the next run using the stored refresh token. If the refresh token also expires (after 365 days), re-run `cutter auth tiktok`.
