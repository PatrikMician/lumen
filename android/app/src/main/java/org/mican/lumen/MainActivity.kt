package org.mican.lumen

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.BroadcastReceiver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.provider.Settings
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.MimeTypeMap
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLDecoder

/** Okno Lumenu: stejné rozhraní jako web, jen běží proti serveru v telefonu (LumenRuntime). */
class MainActivity : ComponentActivity() {

    private lateinit var web: WebView
    private lateinit var splash: LinearLayout
    private lateinit var status: TextView
    private lateinit var progress: ProgressBar
    private lateinit var retry: Button
    private var loadedPort = 0
    private var sharedUrl: String? = null
    private var openUpdate = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    private val listener: (LumenRuntime.State) -> Unit = { render(it) }

    private val pickFile = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        val uris = WebChromeClient.FileChooserParams.parseResult(res.resultCode, res.data)
        fileCallback?.onReceiveValue(if (res.resultCode == Activity.RESULT_OK) uris else null)
        fileCallback = null
    }

    private val stopped = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) = finishAndRemoveTask()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildUi()
        sharedUrl = sharedText(intent)
        openUpdate = intent?.action == UpdateWorker.ACTION_UPDATE
        UpdateWorker.schedule(this)
        // povely z lišty, zamykací obrazovky a sluchátek → přehrávač v okně
        LumenMedia.init(this)
        LumenMedia.controlHandler = { action, value ->
            web.evaluateJavascript("window.LumenMedia && LumenMedia.control('$action', $value)", null)
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        registerReceiver(stopped, IntentFilter(LumenService.ACTION_STOPPED), Context.RECEIVER_NOT_EXPORTED)
        LumenService.start(this)
        LumenRuntime.observe(listener)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else moveTaskToBack(true)  // Lumen běží dál na pozadí
            }
        })
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        sharedText(intent)?.let { url -> if (loadedPort != 0) fillUrl(url) else sharedUrl = url }
        if (intent.action == UpdateWorker.ACTION_UPDATE) {
            if (loadedPort != 0) web.loadUrl("http://127.0.0.1:$loadedPort/update.html") else openUpdate = true
        }
    }

    override fun onDestroy() {
        LumenMedia.controlHandler = null
        LumenMedia.clear()  // WebView končí, hudba s ním
        LumenRuntime.forget(listener)
        unregisterReceiver(stopped)
        web.destroy()
        super.onDestroy()
    }

    // ----------------------------------------------------------------------
    @SuppressLint("SetJavaScriptEnabled")
    private fun buildUi() {
        val bg = Color.parseColor("#1B1730")
        web = WebView(this).apply {
            setBackgroundColor(bg)
            visibility = View.INVISIBLE
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.userAgentString = settings.userAgentString + " LumenAndroid/" + BuildConfig.VERSION_NAME
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val uri = request.url
                    if (uri.host == "127.0.0.1" && uri.port == loadedPort) return false
                    openExternal(uri)
                    return true
                }

                override fun onPageFinished(view: WebView, url: String) {
                    view.visibility = View.VISIBLE
                    splash.visibility = View.GONE
                    sharedUrl?.let { fillUrl(it); sharedUrl = null }
                }
            }
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>,
                                               params: FileChooserParams): Boolean {
                    fileCallback?.onReceiveValue(null)
                    fileCallback = callback
                    return try {
                        pickFile.launch(params.createIntent()); true
                    } catch (e: Exception) {
                        fileCallback = null; false
                    }
                }
            }
            setDownloadListener { url, _, disposition, mime, _ -> save(url, disposition, mime) }
            addJavascriptInterface(Bridge(), "LumenAndroid")
        }
        status = TextView(this).apply {
            setTextColor(Color.parseColor("#C9C3E0"))
            textSize = 15f
            gravity = Gravity.CENTER
            setPadding(48, 32, 48, 16)
        }
        progress = ProgressBar(this)
        retry = Button(this).apply {
            text = "Zkusit znovu"
            visibility = View.GONE
            setOnClickListener { LumenService.start(this@MainActivity) }
        }
        val title = TextView(this).apply {
            text = "lumen"
            textSize = 44f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
        }
        splash = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(bg)
            addView(title)
            addView(progress)
            addView(status)
            addView(retry, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { gravity = Gravity.CENTER_HORIZONTAL })
        }
        setContentView(FrameLayout(this).apply {
            setBackgroundColor(bg)
            fitsSystemWindows = true
            addView(web, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            addView(splash, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        })
    }

    private fun render(s: LumenRuntime.State) {
        when (s) {
            is LumenRuntime.State.Starting -> {
                if (loadedPort == 0) splash.visibility = View.VISIBLE
                progress.visibility = View.VISIBLE
                retry.visibility = View.GONE
                status.text = s.step
            }
            is LumenRuntime.State.Ready -> if (s.port != loadedPort) {
                loadedPort = s.port
                web.loadUrl("http://127.0.0.1:${s.port}/" + if (openUpdate) "update.html" else "")
                openUpdate = false
            }
            is LumenRuntime.State.Failed -> {
                loadedPort = 0
                web.visibility = View.INVISIBLE
                splash.visibility = View.VISIBLE
                progress.visibility = View.GONE
                retry.visibility = View.VISIBLE
                status.text = "Lumen se nepodařilo spustit.\n\n${s.message.take(1200)}"
            }
        }
    }

    /** Pro stránku aktualizace: instalace stažené verze (APK z UPDATE_DIR). */
    inner class Bridge {
        @JavascriptInterface
        fun installUpdate(path: String) = runOnUiThread { installApk(path) }

        /** Stav přehrávače z music.js pro lištu, zamykací obrazovku a sluchátka. */
        @JavascriptInterface
        fun mediaState(json: String) = LumenMedia.update(json)
    }

    private fun installApk(path: String) {
        val dir = LumenRuntime.updateDir(this).canonicalFile
        val apk = File(path).canonicalFile
        if (apk.parentFile != dir || !apk.isFile || !apk.name.endsWith(".apk")) return
        if (!packageManager.canRequestPackageInstalls()) {
            Toast.makeText(this, "Povol Lumenu instalovat aplikace a pak klepni na Instalovat.", Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
            return
        }
        val uri = FileProvider.getUriForFile(this, "$packageName.files", apk)
        startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    // ----------------------------------------------------------------------
    private fun sharedText(intent: Intent?): String? {
        if (intent?.action != Intent.ACTION_SEND) return null
        val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return null
        return Regex("https?://\\S+").find(text)?.value
    }

    /** Odkaz sdílený z jiné aplikace: vloží ho do pole na stránce Stáhnout a načte. */
    private fun fillUrl(url: String) {
        val js = "location.hash='#/download';setTimeout(function(){var i=document.getElementById('urlInput');" +
            "if(!i)return;i.value=${org.json.JSONObject.quote(url)};i.dispatchEvent(new Event('input',{bubbles:true}));" +
            "var f=i.closest('form');if(f&&f.requestSubmit)f.requestSubmit();},300);"
        web.evaluateJavascript(js, null)
    }

    private fun openExternal(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (e: Exception) {
            Toast.makeText(this, "Odkaz nejde otevřít: $uri", Toast.LENGTH_SHORT).show()
        }
    }

    /** „Uložit“ v Lumenu: soubor ze serveru v telefonu zkopíruje do Stažené soubory/Lumen. */
    private fun save(url: String, disposition: String?, mime: String?) {
        val uri = Uri.parse(url)
        if (uri.host != "127.0.0.1") { openExternal(uri); return }
        val name = fileName(url, disposition, mime)
        Toast.makeText(this, "Ukládám $name…", Toast.LENGTH_SHORT).show()
        Thread {
            val msg = try {
                val conn = URL(url).openConnection() as HttpURLConnection
                CookieManager.getInstance().getCookie(url)?.let { conn.setRequestProperty("Cookie", it) }
                conn.connect()
                if (conn.responseCode != 200) throw IllegalStateException("server vrátil ${conn.responseCode}")
                val type = mime?.takeIf { it.isNotBlank() && it != "application/octet-stream" }
                    ?: MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substringAfterLast('.', "").lowercase())
                    ?: "application/octet-stream"
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, name)
                    put(MediaStore.Downloads.MIME_TYPE, type)
                    put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Lumen")
                    put(MediaStore.Downloads.IS_PENDING, 1)
                }
                val target = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: throw IllegalStateException("úložiště odmítlo soubor")
                try {
                    conn.inputStream.use { input ->
                        contentResolver.openOutputStream(target)!!.use { input.copyTo(it, 256 * 1024) }
                    }
                } catch (e: Exception) {
                    contentResolver.delete(target, null, null)
                    throw e
                } finally {
                    conn.disconnect()
                }
                contentResolver.update(target, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
                "Uloženo do Stažené soubory/Lumen: $name"
            } catch (e: Exception) {
                "Uložení se nepovedlo: ${e.message}"
            }
            runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }
        }.start()
    }

    private fun fileName(url: String, disposition: String?, mime: String?): String {
        // Content-Disposition: filename*=UTF-8''… má přednost (české názvy), jinak klasický odhad
        disposition?.let { d ->
            Regex("filename\\*=UTF-8''([^;]+)", RegexOption.IGNORE_CASE).find(d)?.let {
                return URLDecoder.decode(it.groupValues[1].trim(), "UTF-8").replace('/', '_')
            }
        }
        return URLUtil.guessFileName(url, disposition, mime)
    }
}
