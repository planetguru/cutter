# Facebook Reels Setup

cutter posts Reels to a **Facebook Page** via the Graph API. Unlike the
Instagram integration (which is Page-free), Facebook Reels require a Facebook
Page and a Page access token — obtained once with Facebook Login. Page tokens
derived from a long-lived user token do **not** expire, so there's no refresh
cycle.

## Prerequisites

- The same **Meta app** used for Instagram (App ID `1705388223947175`). Facebook
  Login uses the app's *main* App ID/secret (App Settings → Basic), **not** the
  Instagram-specific ones.
- A **Facebook Page** you administer (e.g. Hut 181).
- The Facebook account you log in with must have a **role on the Meta app**
  (Admin/Developer/Tester) while the app is in development mode — otherwise the
  permissions can't be granted. Add it under **App roles → Roles**.

## 1. Add Facebook Login to the app

1. In the app dashboard, add the **Facebook Login for Business** (or **Facebook
   Login**) product / use case.
2. Under its **Settings**, add the OAuth redirect URI:
   `http://localhost:8080/callback` (Facebook Login permits `localhost`, so no
   HTTPS bounce page is needed).

## 2. Permissions

The flow requests `pages_show_list`, `pages_read_engagement`, and
`pages_manage_posts`. In development mode these work for an account with a role
on the app posting to its **own** Page — no App Review required. (App Review is
only needed to post to Pages you don't administer.)

## 3. Add Credentials to .env

```
FACEBOOK_APP_ID=1705388223947175
FACEBOOK_APP_SECRET=<main Meta app secret from App Settings → Basic>
# FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN are filled in by `cutter auth facebook`
# FACEBOOK_PAGE_NAME=Hut 181   # optional — pick a specific Page if you manage several
```

## 4. Run the OAuth Flow

```bash
cutter auth facebook
```

Log in as the account that manages the Page and approve the permissions. The
command exchanges the code for a long-lived user token, reads the Page's own
(non-expiring) token from `/me/accounts`, and writes `FACEBOOK_PAGE_ID` and
`FACEBOOK_PAGE_TOKEN` to `.env`, printing the Page name so you can confirm. If
you manage multiple Pages, set `FACEBOOK_PAGE_NAME` to choose.

## Posting

Facebook is included in `--post all` (and `cutter daily`). A Reel is published
in three phases: start → upload the clip bytes to `rupload.facebook.com` →
finish (publish with the caption). It's skipped with a warning until the token
is configured.
