# Instagram API Setup (Instagram Login)

cutter uses Meta's **Instagram API with Instagram login** (graph.instagram.com):
you authenticate as the Instagram professional account itself — **no Facebook
Page or Page linking required**. (The older Facebook-Login flavour is dead for
many accounts: Meta shows "Linking isn't available at the moment" when trying
to connect a Page to Instagram.)

## Prerequisites

- A **Facebook Developer** account at [developers.facebook.com](https://developers.facebook.com)
- An **Instagram Professional account** (Business or Creator)
- A **media staging server** (`MEDIA_*` in `.env`): graph.instagram.com has no
  direct byte upload — it ingests each video from a public HTTPS URL. Clips are
  scp'd up before posting and deleted immediately after.

## 1. Create a Meta App

1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps) → **Create App**.
2. Choose **Business** as the app type. No business portfolio needed.
3. Under **Use cases**, make sure **"Manage messaging & content on Instagram"** is present.

## 2. Configure Instagram Business Login

1. Open the use case → **Customize** → find the **Instagram business login** / API-setup section.
2. Note the **Instagram App ID** and **Instagram App Secret** shown there —
   these are *different* from the Meta app's ID/secret on App Settings → Basic,
   and they are the ones that go in `.env`.
3. In the business login settings, add the OAuth redirect URI:
   `https://cutter.chris.uk.com/instagram/callback`
   (Instagram requires HTTPS; that page instantly bounces the auth code back
   to the CLI's local listener on `localhost:8080`. Source: `site/instagram/callback/`.)

## 3. Permissions

The flow requests `instagram_business_basic` and
`instagram_business_content_publish`. In development mode these work for
accounts with a role on the app — if login fails with a permissions error, add
your Instagram account under **App roles → Roles → Add people → Instagram
tester**, then accept the invite in the Instagram app (Settings → Website
permissions / Apps and websites → Tester invites).

## 4. Add Credentials to .env

```
INSTAGRAM_APP_ID=instagram_app_id_from_business_login_section
INSTAGRAM_APP_SECRET=instagram_app_secret_from_business_login_section

MEDIA_HOST=chris.uk.com
MEDIA_USER=root
MEDIA_WEBROOT=/var/www/cutter/media
MEDIA_BASE_URL=https://chris.uk.com/cutter/media
```

## 5. Run the OAuth Flow

```bash
cutter auth instagram
```

Log in as the Instagram account. The command exchanges the code for a
**long-lived token** (~60 days) and writes `INSTAGRAM_ACCESS_TOKEN` and
`INSTAGRAM_ACCOUNT_ID` to `.env`, printing the @username so you can confirm
the right account.

## Token Refresh

Long-lived tokens expire after ~60 days. Refresh before expiry:

```bash
cutter auth instagram --refresh
```
