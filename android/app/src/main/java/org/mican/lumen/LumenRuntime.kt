package org.mican.lumen

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import java.io.File
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.ServerSocket
import java.net.URL
import java.util.zip.ZipInputStream

/**
 * Server Lumenu v telefonu: Python z knihovny youtubedl-android spustí backend Lumenu (FastAPI) na 127.0.0.1.
 * Python, ffmpeg a QuickJS jsou v APK jako nativní knihovny, yt-dlp jako zipapp, který se umí aktualizovat.
 */
object LumenRuntime {
    private const val TAG = "Lumen"

    sealed class State {
        data class Starting(val step: String) : State()
        data class Ready(val port: Int) : State()
        data class Failed(val message: String) : State()
    }

    @Volatile var state: State = State.Starting("Spouštím Lumen…")
        private set

    private val listeners = mutableListOf<(State) -> Unit>()
    private val main = Handler(Looper.getMainLooper())
    private var process: Process? = null
    private var thread: Thread? = null

    fun observe(listener: (State) -> Unit) {
        synchronized(listeners) { listeners += listener }
        main.post { listener(state) }
    }

    fun forget(listener: (State) -> Unit) {
        synchronized(listeners) { listeners -= listener }
    }

    private fun set(s: State) {
        state = s
        val copy = synchronized(listeners) { listeners.toList() }
        main.post { copy.forEach { it(s) } }
    }

    @Synchronized
    fun start(ctx: Context) {
        val app = ctx.applicationContext
        if (thread?.isAlive == true || process?.isAlive == true) return
        set(State.Starting("Spouštím Lumen…"))
        thread = Thread({ run(app) }, "lumen-start").apply { start() }
    }

    @Synchronized
    fun stop() {
        process?.destroy()
        process = null
    }

    fun logFile(ctx: Context) = File(ctx.filesDir, "lumen.log")

    fun updateDir(ctx: Context) = File(ctx.externalCacheDir ?: ctx.cacheDir, "update")

    private fun run(ctx: Context) {
        try {
            set(State.Starting("Připravuji Python a ffmpeg (poprvé to chvíli trvá)…"))
            YoutubeDL.getInstance().init(ctx)
            FFmpeg.getInstance().init(ctx)
            set(State.Starting("Připravuji Lumen…"))
            val appDir = extractApp(ctx)
            val port = freePort()
            val proc = launch(ctx, appDir, port)
            process = proc
            set(State.Starting("Spouštím server…"))
            val deadline = System.currentTimeMillis() + 120_000
            while (System.currentTimeMillis() < deadline) {
                if (!proc.isAlive) throw IllegalStateException("Server Lumenu skončil (kód ${proc.exitValue()}).\n${tail(logFile(ctx))}")
                if (alive(port)) {
                    set(State.Ready(port))
                    Thread({ watch(ctx, proc) }, "lumen-watch").start()
                    Thread({ updateYtdlp(ctx) }, "lumen-ytdlp").start()
                    return
                }
                Thread.sleep(300)
            }
            proc.destroy()
            throw IllegalStateException("Server Lumenu se nespustil včas.\n${tail(logFile(ctx))}")
        } catch (e: Throwable) {
            Log.e(TAG, "start", e)
            set(State.Failed(e.message ?: e.toString()))
        }
    }

    /** Backend, web a Python balíčky (assets/lumen.zip) rozbalí do files/lumen, jen po instalaci nebo aktualizaci. */
    private fun extractApp(ctx: Context): File {
        val dir = File(ctx.filesDir, "lumen")
        val info = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        val stamp = "${info.longVersionCode}-${info.lastUpdateTime}"
        val stampFile = File(dir, ".stamp")
        if (stampFile.exists() && stampFile.readText() == stamp) return dir
        dir.deleteRecursively()
        dir.mkdirs()
        ZipInputStream(ctx.assets.open("lumen.zip").buffered()).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                val out = File(dir, entry.name)
                if (!out.canonicalPath.startsWith(dir.canonicalPath + File.separator)) continue
                if (entry.isDirectory) out.mkdirs() else {
                    out.parentFile?.mkdirs()
                    out.outputStream().use { zip.copyTo(it) }
                }
            }
        }
        stampFile.writeText(stamp)
        return dir
    }

    private fun freePort(): Int = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.localPort }

    private fun launch(ctx: Context, appDir: File, port: Int): Process {
        val native = ctx.applicationInfo.nativeLibraryDir
        val base = File(ctx.noBackupFilesDir, "youtubedl-android")
        val python = File(base, "packages/python")
        val ffmpeg = File(base, "packages/ffmpeg")
        // yt-dlp: kopie zipappu, ať ho aktualizace během běhu nepřepíše pod rukama (platí od dalšího spuštění)
        val ytdlp = File(appDir, "yt-dlp.zip")
        File(base, "yt-dlp/yt-dlp").copyTo(ytdlp, overwrite = true)

        val log = logFile(ctx)
        if (log.length() > 512 * 1024) log.delete()
        val pb = ProcessBuilder(File(native, "libpython.so").absolutePath, "-m", "lumen_android")
            .directory(appDir)
            .redirectErrorStream(true)
            .redirectOutput(ProcessBuilder.Redirect.appendTo(log))
        val info = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        pb.environment().apply {
            put("LD_LIBRARY_PATH", "${python.absolutePath}/usr/lib:${ffmpeg.absolutePath}/usr/lib")
            put("SSL_CERT_FILE", "${python.absolutePath}/usr/etc/tls/cert.pem")
            put("PYTHONHOME", "${python.absolutePath}/usr")
            put("PYTHONPATH", listOf(appDir, File(appDir, "pydeps"), ytdlp).joinToString(":") { it.absolutePath })
            put("PYTHONUNBUFFERED", "1")
            put("HOME", ctx.filesDir.absolutePath)
            put("TMPDIR", ctx.cacheDir.absolutePath)
            put("LUMEN_PORT", port.toString())
            put("LUMEN_LOG", logFile(ctx).absolutePath)
            // knihovna hudby (stažená + import) a stažené aktualizace; obojí bez oprávnění k úložišti
            ctx.getExternalFilesDir(android.os.Environment.DIRECTORY_MUSIC)?.let { put("MUSIC_LIBRARY_DIR", it.absolutePath) }
            put("UPDATE_DIR", updateDir(ctx).absolutePath)
            put("LUMEN_APP_VERSION", info.versionName ?: "")
            put("DATA_DIR", File(ctx.filesDir, "data").absolutePath)
            put("DOWNLOAD_DIR", File(ctx.filesDir, "downloads").absolutePath)
            put("FFMPEG_PATH", File(native, "libffmpeg.so").absolutePath)
            put("JS_RUNTIMES", "quickjs:" + File(native, "libqjs.so").absolutePath)
            // zkušební sestavení: KLÍČ=hodnota;KLÍČ=hodnota (třeba MUSIC_REQUIRE_KEY=0)
            BuildConfig.TEST_ENV.split(";").filter { "=" in it }.forEach { put(it.substringBefore("="), it.substringAfter("=")) }
        }
        return pb.start()
    }

    private fun alive(port: Int): Boolean = try {
        val c = URL("http://127.0.0.1:$port/api/health").openConnection() as HttpURLConnection
        c.connectTimeout = 1000
        c.readTimeout = 2000
        try { c.responseCode == 200 } finally { c.disconnect() }
    } catch (e: Exception) {
        false
    }

    private fun watch(ctx: Context, proc: Process) {
        val code = proc.waitFor()
        if (process === proc) set(State.Failed("Server Lumenu nečekaně skončil (kód $code).\n${tail(logFile(ctx))}"))
    }

    /** Nejvýš jednou denně: novější yt-dlp z GitHubu, použije se při dalším spuštění. */
    private fun updateYtdlp(ctx: Context) {
        val prefs = ctx.getSharedPreferences("lumen", Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        if (now - prefs.getLong("ytdlpChecked", 0) < 24 * 3600_000L) return
        try {
            val status = YoutubeDL.getInstance().updateYoutubeDL(ctx, YoutubeDL.UpdateChannel.STABLE)
            Log.i(TAG, "yt-dlp: $status")
            prefs.edit().putLong("ytdlpChecked", now).apply()
        } catch (e: Exception) {
            Log.w(TAG, "yt-dlp update", e)
        }
    }

    fun tail(file: File, bytes: Int = 1500): String = try {
        RandomAccessFile(file, "r").use { f ->
            val start = maxOf(0L, f.length() - bytes)
            f.seek(start)
            val buf = ByteArray((f.length() - start).toInt())
            f.readFully(buf)
            String(buf).trim()
        }
    } catch (e: Exception) {
        ""
    }
}
