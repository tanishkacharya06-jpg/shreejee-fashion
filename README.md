# Shreejee Fashion v1.0.10

Shreejee Fashion v1.0.10 adds an application health check and clearer diagnostics for unsupported HTTP methods, alongside the v1.0.8 order and gallery improvements. Admin screens poll for new or changed orders, customer order pages refresh when a delivery quote is sent, and larger original photos are resized in the browser before the server's per-image upload limit is applied. The storefront still includes six selectable website looks, editable categories, coupons, returns, notifications and the regular Admin Dashboard. Back up `store.db` and `static/uploads/` before upgrading; setup steps are in START_HERE.txt.


## Version 1.0.9 diagnostics

- Open `/health` on the same host to check whether Flask is responding; a healthy app returns JSON with `ok: true` and version `1.0.10`.
- Requests that use an unsupported HTTP method now show a clear page or structured JSON including the path, method and supported methods.
- This app does not call the Cloudflare REST API. For Quick Tunnel, use the `https://…trycloudflare.com/` URL printed by `app.py`, not a Cloudflare API endpoint.

## Version 1.0.8 updates

- Admin pages and order queues auto-refresh when customer orders are placed or updated; the order list/dashboard polls every 2.5 seconds.
- Customer order pages refresh when a new delivery charge is sent, accepted or declined; charges apply to both COD and online delivery orders.
- Gallery uploads now permit larger original phone photos for browser-side resizing before the 8 MB-per-image upload cap; supported HEIC/HEIF/AVIF photos are converted when the browser can decode them. Clearer errors appear if conversion or upload fails.

## Version 1.0.7 updates

- Global sound effects trigger on actionable links, buttons, category tiles, category summaries, dropdown controls and mobile taskbar links; volume mapping is stronger and remains adjustable from 0–100%.
- Replaced the cart symbol with a detailed inline SVG cart icon in the header, navigation and Fresh Pop taskbar.
- Improved Fresh Pop phone taskbar sizing, touch target height, safe-area spacing, and touch-direction hide/show behavior.
- Removed the standalone Admin Studio page, its routes, and its chooser/navigation entries. The regular Admin Dashboard, notifications, coupons, returns, and Website Editor remain available.
- SQLite uses WAL journaling, a busy timeout and normal synchronous mode; every `db()` write transaction still commits before its request returns.
- Admins can publish a default storefront theme from the appearance page or select it in Website Editor; saved visitor preferences remain personalized.
- Password fields on login, register and reset pages have an accessible eye control to show/hide the password.
- Admin My Account is now available in both the desktop header menu and the mobile slide-out menu.
- Classic, Boutique and Editorial now show a thumb-friendly bottom taskbar on phones; Modern, Noir and Fresh Pop use tuned safe-area-aware layouts.

## Version 1.0.3 updates

- Sound effects now default to full intensity and can be adjusted from 0–100% in “Choose how the store looks”; eight browser-generated sound styles remain available with a preview button.
- Menu button position can be changed to any corner from the appearance chooser. The preference is stored on the current device.
- Admins can edit coupon code/discount/limits, activate or deactivate codes, or delete them. Deleting a coupon does not rewrite old order history.
- The previous release included a separate admin-only Studio page with income metrics and a read-only draft preview. That standalone page and its routes have been removed in v1.0.5; use the regular Admin Dashboard and Website Editor instead.

## Version 1.0.1 updates

- Admins can open “Choose how the store looks” from the header or slide-out menu and preview themes just like customers. The admin preview is remembered in that browser.
- Eight selectable button sounds: Soft click, Pop, Chime, Bubble, Bell, Sparkle, Arcade and Wood tap. Sound effects can be turned off, and the style can be previewed.
- Customer Notifications and Returns & Exchanges are nested under My Account. The separate move-menu option has been removed; placement follows each selected look, with Fresh Pop at top-left.

## Version 1.0.0 updates

- Fresh Pop menu button defaults to the top-left; menu placement now follows the selected look.
- Sound effects can be switched on or off from “Choose how the store looks”; the preference and chosen sound style are saved locally on the current device.
- Women / Girl expands to Chania-choli, Lehenga Choli, Jewelry / Accessories, Lehenga and Blouse.
- Customers see Boys / Men and Kids Wear as Coming soon; administrators can open these categories. Customer search results and direct product/cart/save actions are also restricted for Coming Soon categories.
- Admin > Website Editor includes a structured menu editor to add, rename, change behavior, and remove categories/subcategories before saving a draft or publishing.

## Version 17 updates

- **Forgot password:** customer asks on `/forgot-password` (email + phone on file), the owner opens **Admin > Password resets**, presses *Generate code* and sends it on WhatsApp, then the customer sets a new password at `/reset-password`. Code: 6 digits, 30 minutes, single use, locked after 5 wrong tries.
- **WhatsApp:** no floating button on the home page. On a product page it shows only after tapping the photo.
- **Three looks:** Classic, Modern, Festive Boutique. Customers choose with "Look" in the header / menu / My account. Saved to the account and a small `sf_ui` cookie.

## Version 14 updates

- Database-backed customer carts persist across refreshes and logout/login.
- UPI QR checkout uses the store payee details and exact order total. The customer immediately sees “I have paid” and “Not yet” options, with “Change my decision” while the store has not verified payment; no timer is used. Customer payment reports must be independently verified by an admin before order registration.
- Online orders then follow the packing, dispatch, and delivery-code flow. Store QR orders require both payment and customer handover confirmation before delivery is recorded.
- Delivered orders hide QR, delivery code, cancel controls, and customer-facing order token.
- Separate Gallery and Camera controls support multiple product photos with upload compression/progress.

## Start on Windows

1. Install Python 3.10+ and enable **Add Python to PATH**.
2. Extract the ZIP.
3. Open a terminal in the extracted folder.
4. Install dependencies once:

   ```powershell
   py -m pip install -r requirements.txt
   ```

5. Run `py app.py` or double-click `app.py`.
6. Open http://127.0.0.1:5000. The app also attempts to start a temporary Cloudflare Quick Tunnel and prints its public URL in the terminal. Keep the window open.

No `.bat` file is needed. If `py` is unavailable, use `python -m pip install -r requirements.txt` and `python app.py`. Cloudflare requires internet access; install the official `cloudflared` tool manually if its automatic first-run download is blocked.

## Starter admin credentials

- Admin 1: `admin` / `admin`
- Admin 2: `rajveer853982@gmail.com` / `rajveer_001`

They are seeded when the database is first created. Existing admin password hashes are retained at later starts. Change these starter passwords before exposing the site publicly.

## Customer features

- Guests can browse and inspect product pages, but **Add to Cart** requires login or registration.
- The selected product, Buy/Rent mode, quantity and size (when supplied) are held in the signed session and added once after authentication; the customer returns to the same local page.
- Add-to-cart uses AJAX, updates the cart count, shows a toast, and animates toward the cart without reloading the page. Reduced-motion preferences are respected.
- Buy/Rent, product search, custom product categories, sizes/quantities, coupons, product details and responsive product cards.
- Checkout payment choices: store QR/order reference, Cash on Delivery (COD), or online payment. A delivery address is required for COD and online payments; pay-at-store QR orders do not require an address. A saved address is reused until changed.
- Cart lists Ongoing Orders. Logged-in customers see Last Order, order status, payment status, and order history. Customer-visible statuses refresh periodically without a full-page reload.
- Clickable store announcements and notification cards expand to show their full text. Customers open Notifications from the My Account menu; direct action links lead to the related order or next step.
- Animated app drawer includes Home, Buy, Rent, categories, account, About, and legal documents. A successful add-to-cart displays a compact white tick on a black square.

## Admin features

- Add, edit, hide, publish and permanently delete products; choose Buy, Rent, or both; upload up to eight verified product photos through separate Gallery and Camera controls. Image uploads are compressed client-side, show progress/time, and block duplicate submissions.
- View, verify and block customers; create custom categories; generate reports as CSV; inspect QR/cart order data.
- Generate and verify unique, order-scoped 6-digit confirmation codes. Invalid code attempts do not confirm an order; attempts are capped and codes can only be used once.
- COD orders move through **COD requested → Preparing item → Ready for dispatch → On the way → Awaiting delivery code → Delivered**. The six-digit code is customer-only; the admin enters the code the customer reads at handover. COD payment must also be confirmed before Delivered.
- Store QR orders have only Cancel and Mark Delivered controls in the admin workflow; both payment received and item handover must be checked.
- Online UPI QR orders show the exact total and payee, then wait for the customer payment report. A customer report is not proof: the admin must verify the transfer in the actual UPI/bank account before registration. Verified online orders then follow the same preparation, dispatch, and delivery-code workflow.
- Delivered/Completed status is locked; each status/payment transition is timestamped in `order_status_history`. Customer views hide the QR, cancel control, delivery code and full order token after Delivered.
- **Website Editor** supports persistent drafts and publishing for branding/logo, hero banner/text, announcements, story text, store contacts/address/hours, navigation labels and safe local destinations, categories/order, typography, colors, gradients, buttons and card spacing. Published settings, catalog changes and product details synchronize with open customer pages by polling.
- Live preview, Save Draft, Publish Changes, Cancel unsaved changes, Restore Defaults and image previews.

## Security and payments

- `SHREEJEE_SECRET_KEY` can be set in the environment. Otherwise, the app generates a private `.secret_key` file for persistent sessions; it is excluded from source control and should not be shared.
- `store.db` contains products, customer accounts, orders, notifications, coupons and website settings. Photos are under `static/uploads/`. Back up both before upgrades. The ZIP intentionally does not contain customer data or private keys. To upgrade an existing v10–v13 installation, back up then copy its `store.db` and `static/uploads/` folder into the extracted v14 folder before the first start; the startup migration adds new columns/tables without deleting existing rows.
- The main Online checkout uses a UPI intent QR for `7909033393@fam` and the exact order total. This does not verify a transfer automatically; the admin must independently match it in the merchant account. A separate legacy Razorpay verification endpoint remains optional and only works after real credentials are configured in environment variables:

  ```powershell
  $env:RAZORPAY_KEY_ID="your_key_id"
  $env:RAZORPAY_KEY_SECRET="your_secret"
  $env:SHREEJEE_SECRET_KEY="use-a-long-random-secret"
  $env:COOKIE_SECURE="1"
  py app.py
  ```

  Never treat an order QR as proof of payment. Do not put real credentials in this project ZIP.
- For same-Wi-Fi mobile access, launch with `HOST=0.0.0.0` and visit `http://YOUR-PC-IP:5000` from a phone on the same trusted Wi-Fi. Camera permission may require HTTPS. A public Cloudflare tunnel exposes the site to the internet, so change admin passwords first.
- For real public sales, use HTTPS, a production WSGI server, strong credentials, current server packages, backups, and reviewed privacy/return/delivery policies. Do not use Flask's development server as a permanent production host.

## Test status

In the build environment, Python compilation, JavaScript syntax checks, Jinja template parsing, ZIP validation and focused SQLite/order-state logic tests passed. A live Flask server/browser test could not be run there because Flask/Werkzeug were unavailable and package downloads were blocked. Install `requirements.txt` and test a sample customer checkout, QR scan, admin verification, website publish, and mobile view on your own machine before public launch.



### COD delivery flow
COD requested → Preparing item → Ready for dispatch → On the way → admin starts doorstep confirmation → customer sees a six-digit code in their order page/notifications → admin enters the customer-provided code and confirms payment/handover → Delivered. The code is not shown in the admin order detail view. Delivered orders are locked.

Admin product editor supports multiple photos per product (up to 8). Camera scanning requires the HTTPS Cloudflare URL or localhost plus browser camera permission; a manual full-token lookup remains available as fallback.




The native QR camera needs HTTPS (Cloudflare URL) or `localhost`, and the user must click Start camera and grant permission. Manual token lookup remains available. Product editor supports multi-image upload (up to eight files per product).

## v14 interface updates

- Public login/help pages do not advertise the internal default customer account. The default account identity fields are maintained server-side and are not editable in the customer interface.
- Customer navigation uses “Notification”; notifications link directly to the next relevant order action. Admin navigation retains its own alert label.
- Pay-online checkout requires a delivery address. The UPI QR page immediately offers “I have paid” and “Not yet” (and “Change my decision” after reporting); there is no timer. Payment reports remain unverified until an admin confirms the real transaction.
- The animated app drawer links to About, Privacy Policy, Terms & Conditions, and Cookies & Tracking. Add-to-cart success uses a compact white tick on a black square.


### v1.0.5
- Open **Choose how the store looks** as a dedicated page at `/appearance`. Choose a look, preview it live, adjust menu location and sound, then press **Apply changes** to return home.
- Admins can open the private Admin Control Center from the appearance page; Admin Dashboard includes recent notifications and a notification shortcut.
- Fresh Pop bottom taskbar now responds to small scroll deltas and button sounds include taskbar/navigation buttons.

## v1.0.5 order workflow updates

COD checkout now waits for an admin-set delivery-fee quote and customer approval before preparation. Order QR codes lead authorized admins directly to the order workflow. Admin checkout is blocked.


### v1.0.7
- Admins can publish a chosen theme as the default storefront look from **Choose how the store looks**, or set the default in **Admin → Website Editor → Appearance**. Existing visitors' personal theme preferences remain intact.
- Added accessible show/hide password eye buttons to login, registration, and reset-password fields.
- Restored **My account** to the admin header and slide-out menu, with profile, notifications, storefront and logout shortcuts.
- Added safe-area-aware mobile taskbars for Classic, Boutique and Editorial themes and tuned existing Modern, Noir and Fresh Pop docks, including bottom padding so the dock doesn't cover content.


## Version 1.0.10 — mobile logout visibility

- Added a first-level **Log out** action to the mobile slide-out menu for both admin and customer accounts. It is no longer hidden inside the collapsed My Account submenu.
- Styled the action as a distinct row and added asset cache-busting so the updated menu CSS and JavaScript load after upgrade.
- The existing `/logout` route clears the session and redirects to the homepage.
