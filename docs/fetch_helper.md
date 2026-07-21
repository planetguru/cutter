# Residential download helper (`cutter fetch`)

YouTube blocks the server's datacenter IP from actual video streams (it returns
only storyboards — the PO-token/SABR restriction), even with valid cookies. A
normal residential connection is not blocked. So downloads are performed on a
home/residential machine and the files are pushed to the server; the server's
downloader then finds `source.mp4` + `metadata.json` already present and skips
its own (blocked) network fetch.

## How it works

- **Server**: `cutter pending-downloads` prints (JSON) the pending queue videos
  whose `source.mp4`/`metadata.json` are missing from its data dir.
- **Residential machine**: `cutter fetch --server root@chris.uk.com` runs that
  over SSH, downloads each missing video locally, and pushes `source.mp4` +
  `metadata.json` back to `/root/.local/share/cutter/<video_id>/` — written to
  `.part` files and atomically renamed so the server never sees a half file.

No double-posting is possible: the helper only moves files. Posting happens
solely on the server's daily run, already guarded by the `posted` list and
`mark_used`. Running two helpers at once (e.g. laptop + Mac mini) is harmless —
identical files, atomic overwrite; whichever pushes first, `pending-downloads`
then omits that video so the other skips it.

## Set up on a machine (launchd, macOS)

Prereqs: clone the repo, `bash setup.sh` (venv + deps), `ffmpeg` installed
(`brew install ffmpeg`), and passwordless SSH to the server (the key already
used for `root@chris.uk.com`).

1. `fetch.sh` (in the repo root) is the launchd entry point — it sets PATH for
   Homebrew ffmpeg, activates the venv, and runs `cutter fetch`. Override the
   target with `FETCH_SERVER` if needed.

2. Install the LaunchAgent (adjust the paths to this machine's home/clone):

   `~/Library/LaunchAgents/com.cutter.fetch.plist`:
   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
       <key>Label</key><string>com.cutter.fetch</string>
       <key>ProgramArguments</key>
       <array>
           <string>/bin/bash</string>
           <string>/Users/<you>/Development/cutter/fetch.sh</string>
       </array>
       <key>StartInterval</key><integer>1800</integer>
       <key>RunAtLoad</key><true/>
       <key>StandardOutPath</key><string>/Users/<you>/Development/cutter/fetch.log</string>
       <key>StandardErrorPath</key><string>/Users/<you>/Development/cutter/fetch.log</string>
   </dict>
   </plist>
   ```

3. Load it:
   ```bash
   launchctl load -w ~/Library/LaunchAgents/com.cutter.fetch.plist
   launchctl list | grep cutter      # confirm loaded
   tail -f ~/Development/cutter/fetch.log
   ```

Runs every 30 min while the machine is awake. On the always-on Mac mini this is
effectively continuous; on a laptop it only runs while the lid's open.

To stop: `launchctl unload -w ~/Library/LaunchAgents/com.cutter.fetch.plist`.

Note: with the helper in place the server never downloads, so a cookies file on
the server (`youtube_cookies.txt`) is no longer needed — harmless if left.
