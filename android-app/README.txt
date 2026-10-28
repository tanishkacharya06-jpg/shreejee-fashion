SHREEJEE FASHION - ANDROID APP (opens your newest Quick Tunnel address)
======================================================================
How it works
  1. When your PC starts the shop, app.py gets a new trycloudflare.com address and saves it to a private GitHub Gist.
  2. The phone app reads that Gist on every start, opens the shop at the newest address, and keeps the last address as a backup.
  3. If the PC is off, the internet is down or the tunnel has expired, the app shows a "shop is taking a short rest" page in the
     look (Classic / Modern / Festive Boutique / Midnight Luxe / Fresh Pop / Editorial) the customer last chose. It retries
     by itself every 20 seconds and has WhatsApp and Call buttons.
  4. The app can only show the shop while your PC and internet are working. Quick Tunnel cannot work without your PC.

One-time setup (about 10 minutes)
  A. Make the Gist (free GitHub account needed)
     - github.com > your profile > Gists > "+" . File name: shop.json   Content: {"url": "https://example.trycloudflare.com"}
     - Create SECRET gist. Copy the long code at the end of its address = GIST_ID.
  B. Make a token
     - github.com > Settings > Developer settings > Personal access tokens > Tokens (classic) > Generate new token.
     - Tick ONLY the "gist" box. Copy the token.
  C. Edit the website's .env file (next to app.py):
         SF_GITHUB_TOKEN=paste-token-here
         SF_GIST_ID=paste-gist-id-here
     Restart the shop. You should see "[App link] Android app address updated."
  D. Build the APK
     - Install Android Studio (free). File > Open > choose this "android-app" folder. Let it download what it needs.
     - Open app/build.gradle.kts and replace PUT_YOUR_GIST_ID_HERE with your GIST_ID (same code as above).
     - Build > Build Bundle(s) / APK(s) > Build APK(s). Click "locate" when it finishes: app/build/outputs/apk/debug/app-debug.apk
     - Send that file to phones (WhatsApp / Drive) and tap it to install. Android may ask to allow "Install unknown apps".

Later, if you get a fixed domain (so the address stops changing), put it in FIXED_URL in app/build.gradle.kts and the Gist is no longer needed.

Notes
  - The app is a debug build, fine for sharing directly. Putting it on the Play Store needs a signed release build.
  - Photo upload (admin) and "use my location" work inside the app.
  - The offline page uses the look the customer last saw on the website; new installs show Classic until the shop has opened once.
