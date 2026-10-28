package com.shreejee.fashion

import android.Manifest
import android.annotation.SuppressLint
import android.content.ClipData
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.webkit.GeolocationPermissions
import android.webkit.JavascriptInterface
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.MimeTypeMap
import android.webkit.PermissionRequest
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.result.contract.ActivityResultContracts
import android.widget.EditText
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * Opens the Shreejee Fashion shop.
 *  - Starts instantly from the last known address, then checks the Gist in the background and switches if the address changed.
 *  - If the shop cannot be reached it shows offline.html in the customer's chosen look.
 *  - Photo upload: gallery picker + real camera (for <input capture>) + camera for getUserMedia (QR scanner).
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var prefs: SharedPreferences
    private val io = Executors.newSingleThreadExecutor()
    private val copier = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())

    private var shopHost: String = ""
    private var currentBase: String? = null
    private var offlineShown = false
    private var clearHistoryPending = false
    private val autoRetry = Runnable { connect() }

    // file chooser / camera state
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var cameraFile: File? = null
    private var cameraUri: Uri? = null

    // permission state
    private var afterCameraPermission: ((Boolean) -> Unit)? = null
    private var geoOrigin: String? = null
    private var geoCallback: GeolocationPermissions.Callback? = null

    private val chooserLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val cb = fileCallback
        fileCallback = null
        val camFile = cameraFile
        val camUri = cameraUri
        cameraFile = null
        cameraUri = null
        val parsed = if (result.resultCode == RESULT_OK) {
            WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        } else null
        if (parsed != null && parsed.isNotEmpty()) {
            // Copy the picked photos into the app's own storage (proper file names, always readable by the page).
            copier.execute {
                val copied = copyPicked(parsed)
                ui.post { cb?.onReceiveValue(if (copied.isEmpty()) parsed else copied) }
            }
        } else if (result.resultCode == RESULT_OK && camUri != null && camFile != null && camFile.exists() && camFile.length() > 0) {
            cb?.onReceiveValue(arrayOf(camUri)) // photo taken with the camera app
        } else {
            cb?.onReceiveValue(null)
        }
    }

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val next = afterCameraPermission
        afterCameraPermission = null
        next?.invoke(granted)
    }

    private val locationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        geoCallback?.invoke(geoOrigin, granted, false)
        geoCallback = null
    }

    @SuppressLint("SetJavaScriptEnabled", "AddJavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("sf", MODE_PRIVATE)
        try {
            File(cacheDir, "camera").deleteRecursively()
        } catch (e: Exception) {
            // ignore
        }
        web = WebView(this)
        setContentView(web)

        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
            setSupportMultipleWindows(false)
        }
        web.addJavascriptInterface(Bridge(), "SFApp")
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView?, callback: ValueCallback<Array<Uri>>?, params: FileChooserParams?
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                val p = params
                val types = p?.acceptTypes?.filter { it.isNotBlank() } ?: emptyList()
                val imagesWanted = types.isEmpty() || types.any { it.startsWith("image") || it.startsWith(".") }
                if (p != null && p.isCaptureEnabled && imagesWanted) {
                    // <input capture>: open the real camera. Falls back to the gallery if that is not possible.
                    withCameraPermission { granted ->
                        if (!(granted && launchCamera())) launchGallery(p)
                    }
                } else {
                    launchGallery(p)
                }
                return true
            }

            // Nice native dialogs instead of Android's "The page at https://..." boxes.
            override fun onJsAlert(view: WebView?, url: String?, message: String?, result: JsResult?): Boolean {
                val r = result ?: return false
                showJsDialog(message, false, r)
                return true
            }

            override fun onJsConfirm(view: WebView?, url: String?, message: String?, result: JsResult?): Boolean {
                val r = result ?: return false
                showJsDialog(message, true, r)
                return true
            }

            override fun onJsPrompt(view: WebView?, url: String?, message: String?, defaultValue: String?, result: JsPromptResult?): Boolean {
                val r = result ?: return false
                if (isFinishing) { r.cancel(); return true }
                val input = EditText(this@MainActivity)
                input.setText(defaultValue.orEmpty())
                AlertDialog.Builder(this@MainActivity)
                    .setTitle(R.string.app_name)
                    .setMessage(message.orEmpty())
                    .setView(input)
                    .setPositiveButton("OK") { _, _ -> r.confirm(input.text.toString()) }
                    .setNegativeButton("Cancel") { _, _ -> r.cancel() }
                    .setOnCancelListener { r.cancel() }
                    .show()
                return true
            }

            // Camera for the QR scanner page (getUserMedia).
            override fun onPermissionRequest(request: PermissionRequest?) {
                val req = request ?: return
                runOnUiThread {
                    val wantsVideo = req.resources.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)
                    val fromShop = req.origin?.host == shopHost
                    if (wantsVideo && fromShop) {
                        withCameraPermission { granted ->
                            if (granted) req.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) else req.deny()
                        }
                    } else {
                        req.deny()
                    }
                }
            }

            override fun onGeolocationPermissionsShowPrompt(origin: String?, callback: GeolocationPermissions.Callback?) {
                if (ContextCompat.checkSelfPermission(this@MainActivity, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                    callback?.invoke(origin, true, false)
                } else {
                    geoOrigin = origin
                    geoCallback = callback
                    locationPermission.launch(Manifest.permission.ACCESS_FINE_LOCATION)
                }
            }
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val uri = request.url
                if (uri.scheme == "file") return false
                if (uri.scheme == "https" && uri.host == shopHost) return false
                // WhatsApp, phone, UPI, maps and other sites open outside the app.
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, uri)); true
                } catch (e: Exception) { true }
            }

            override fun onPageFinished(view: WebView, url: String?) {
                if (clearHistoryPending && url != null && Uri.parse(url).host == shopHost) {
                    clearHistoryPending = false
                    view.clearHistory() // back button never returns to the loading / offline page
                }
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                // Ignore errors from an address we already replaced with a newer one.
                if (request.isForMainFrame && !offlineShown && request.url.host == shopHost) showOffline()
            }

            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                val code = response.statusCode
                // 502-504 and Cloudflare 520-530 (530 = tunnel is gone) mean the shop PC is not answering.
                if (request.isForMainFrame && !offlineShown && request.url.host == shopHost &&
                    (code in 502..504 || code in 520..530)) showOffline()
            }
        }
        connect()
    }

    override fun onResume() {
        super.onResume()
        if (offlineShown) connect()
    }

    override fun onDestroy() {
        ui.removeCallbacks(autoRetry)
        super.onDestroy()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (!offlineShown && web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    // ---------------------------------------------------------------- opening the shop (fast start)

    /** Open the last known address immediately, then look up the newest one in the background. */
    private fun connect() {
        ui.removeCallbacks(autoRetry)
        val fixed = BuildConfig.FIXED_URL.trim().trimEnd('/')
        if (fixed.isNotEmpty()) {
            loadShop(fixed)
            return
        }
        val cached = prefs.getString("url", null)
        if (cached != null) {
            if (offlineShown || currentBase == null) loadShop(cached)
        } else if (currentBase == null) {
            showLoading()
        }
        io.execute {
            val fresh = fetchGistUrl()
            ui.post {
                if (fresh != null) {
                    if (fresh != currentBase) loadShop(fresh)
                } else if (currentBase == null) {
                    showOffline() // nothing known and nothing found
                }
            }
        }
    }

    private fun loadShop(base: String) {
        ui.removeCallbacks(autoRetry)
        currentBase = base
        shopHost = Uri.parse(base).host ?: ""
        offlineShown = false
        clearHistoryPending = true
        web.loadUrl(base)
    }

    private fun fetchGistUrl(): String? {
        return try {
            val text = httpGet("https://api.github.com/gists/" + BuildConfig.GIST_ID, 5000)
            val files = JSONObject(text).getJSONObject("files")
            val content = files.getJSONObject("shop.json").getString("content")
            val url = JSONObject(content).getString("url").trim().trimEnd('/')
            if (url.startsWith("https://")) {
                prefs.edit().putString("url", url).apply()
                url
            } else null
        } catch (e: Exception) {
            null // no internet or GitHub unreachable: keep using the last known address
        }
    }

    private fun httpGet(address: String, timeoutMs: Int): String {
        val conn = URL(address).openConnection() as HttpURLConnection
        conn.connectTimeout = timeoutMs
        conn.readTimeout = timeoutMs
        conn.setRequestProperty("User-Agent", "ShreejeeFashionApp")
        conn.setRequestProperty("Accept", "application/json")
        try {
            if (conn.responseCode != 200) throw RuntimeException("HTTP " + conn.responseCode)
            return conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }

    private fun showLoading() {
        val html = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>" +
            "<body style='margin:0;height:100vh;display:flex;align-items:center;justify-content:center;" +
            "background:#F6F1E7;color:#1C3D31;font-family:sans-serif;font-size:18px'>Opening Shreejee Fashion&hellip;</body></html>"
        web.loadDataWithBaseURL("file:///android_asset/", html, "text/html", "UTF-8", null)
    }

    private fun showOffline() {
        offlineShown = true
        val theme = prefs.getString("theme", "classic") ?: "classic"
        web.loadUrl("file:///android_asset/offline.html?theme=" + Uri.encode(theme) + "&phone=" + Uri.encode(BuildConfig.SHOP_PHONE))
        ui.removeCallbacks(autoRetry)
        ui.postDelayed(autoRetry, 20000)
    }

    // ---------------------------------------------------------------- camera + gallery

    private fun showJsDialog(message: String?, withCancel: Boolean, result: JsResult) {
        if (isFinishing) {
            result.cancel()
            return
        }
        val builder = AlertDialog.Builder(this)
            .setTitle(R.string.app_name)
            .setMessage(message.orEmpty())
            .setPositiveButton("OK") { _, _ -> result.confirm() }
            .setOnCancelListener { result.cancel() }
        if (withCancel) builder.setNegativeButton("Cancel") { _, _ -> result.cancel() }
        builder.show()
    }

    /** Copies picked gallery photos to app storage with a proper name + extension; returns file:// URIs. */
    private fun copyPicked(uris: Array<Uri>): Array<Uri> {
        val root = File(cacheDir, "camera")
        val out = ArrayList<Uri>()
        val stamp = System.currentTimeMillis()
        for ((i, u) in uris.withIndex()) {
            try {
                var display: String? = null
                try {
                    contentResolver.query(u, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                        if (c.moveToFirst()) display = c.getString(0)
                    }
                } catch (e: Exception) {
                    // name unknown
                }
                val mime = contentResolver.getType(u)
                var ext: String? = if (mime != null) MimeTypeMap.getSingleton().getExtensionFromMimeType(mime) else null
                if (ext.isNullOrBlank()) {
                    val fromName = display?.substringAfterLast('.', "") ?: ""
                    ext = if (fromName.length in 2..5) fromName.lowercase() else "jpg"
                }
                val base = (display?.substringBeforeLast('.') ?: "photo").replace(Regex("[^A-Za-z0-9._-]"), "_").take(60).ifBlank { "photo" }
                val dir = File(root, "p${stamp}_$i")
                dir.mkdirs()
                val file = File(dir, "$base.$ext")
                val input = contentResolver.openInputStream(u) ?: continue
                input.use { ins -> file.outputStream().use { ins.copyTo(it) } }
                if (file.length() > 0) out.add(Uri.fromFile(file))
            } catch (e: Exception) {
                // skip a photo that cannot be read
            }
        }
        return out.toTypedArray()
    }

    private fun withCameraPermission(block: (Boolean) -> Unit) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            block(true)
        } else {
            afterCameraPermission = block
            cameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    /** Opens the phone's camera app and returns the photo to the website. Returns false if it could not start. */
    private fun launchCamera(): Boolean {
        return try {
            val dir = File(cacheDir, "camera")
            dir.mkdirs()
            val file = File.createTempFile("photo_", ".jpg", dir)
            val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
            val intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
                putExtra(MediaStore.EXTRA_OUTPUT, uri)
                addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
                clipData = ClipData.newRawUri("", uri)
            }
            cameraFile = file
            cameraUri = uri
            chooserLauncher.launch(intent)
            true
        } catch (e: Exception) {
            cameraFile = null
            cameraUri = null
            false
        }
    }

    /** Opens the gallery / file picker. */
    private fun launchGallery(p: WebChromeClient.FileChooserParams?) {
        val types = p?.acceptTypes?.filter { it.isNotBlank() } ?: emptyList()
        val imageOnly = types.isNotEmpty() && types.all { it.startsWith("image") || it.startsWith(".") }
        val own: Intent? = if (imageOnly) null else try {
            p?.createIntent()
        } catch (e: Exception) {
            null
        }
        val intent: Intent = own ?: Intent(Intent.ACTION_GET_CONTENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = if (imageOnly || types.isEmpty()) "image/*" else "*/*"
            if (p != null && p.mode == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE) {
                putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
            }
        }
        try {
            chooserLauncher.launch(intent)
        } catch (e: Exception) {
            fileCallback?.onReceiveValue(null)
            fileCallback = null
        }
    }

    inner class Bridge {
        /** Called by the website on every page so the offline page can match the customer's look. */
        @JavascriptInterface
        fun setTheme(theme: String?) {
            val t = theme.orEmpty()
            if (t in setOf("classic", "modern", "boutique", "noir", "fresh", "editorial")) {
                prefs.edit().putString("theme", t).apply()
            }
        }

        /** Called by the "Try again" button on the offline page. */
        @JavascriptInterface
        fun retry() {
            ui.post { connect() }
        }
    }
}
