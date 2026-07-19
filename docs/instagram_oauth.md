# Instagram API Setup

## Prerequisites

- A **Facebook Developer** account at [developers.facebook.com](https://developers.facebook.com)
- An **Instagram Professional account** (Business or Creator) linked to a **Facebook Page**

## 1. Create a Meta App

1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps) → **Create App**.
2. Choose **Business** as the app type.
3. Add the **Instagram** product from the products list.

## 2. Configure OAuth

1. Under **Facebook Login → Settings**, add `http://localhost:8080/callback` as a valid OAuth redirect URI.
2. Note your **App ID** and **App Secret** from **App Settings → Basic**.

## 3. Required Permissions

Your app needs these permissions (request in App Review if publishing to others):
- `instagram_basic`
- `instagram_content_publish`
- `pages_read_engagement`

> For your own account during development these work without App Review via the test user flow.

## 4. Add App Credentials to .env

```
INSTAGRAM_APP_ID=your_app_id
INSTAGRAM_APP_SECRET=your_app_secret
```

## 5. Run the OAuth Flow

```bash
cutter auth instagram
```

This opens a browser for Meta login, exchanges the code for a **long-lived token** (valid ~60 days), and writes `INSTAGRAM_ACCESS_TOKEN` and `INSTAGRAM_ACCOUNT_ID` to `.env`.

## Token Refresh

Long-lived tokens expire after ~60 days. Refresh before expiry:

```bash
cutter auth instagram --refresh
```
