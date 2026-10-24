package org.mican.lumen

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.Looper
import android.support.v4.media.MediaMetadataCompat
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Hudba Lumenu pro systém: WebView na Androidu (na rozdíl od Chromu) systému neohlásí, že hraje, takže by nebylo
 * ovládání v liště, na zamykací obrazovce ani na sluchátkách. Přehrávač v okně (music.js) posílá stav přes
 * LumenAndroid.mediaState, tady z něj vznikne MediaSession (tlačítka sluchátek, Bluetooth, zamykací obrazovka)
 * a LumenService ukáže mediální oznámení. Povely jdou zpátky do okna přes [controlHandler].
 */
object LumenMedia {
    data class State(
        val title: String, val artist: String, val album: String, val art: String,
        val playing: Boolean, val position: Long, val duration: Long,
    )

    @Volatile var state: State? = null
        private set
    @Volatile var artBitmap: Bitmap? = null
        private set
    var session: MediaSessionCompat? = null
        private set
    /** Spustí povel v přehrávači (nastavuje MainActivity, dokud má okno s WebView). */
    var controlHandler: ((String, Long) -> Unit)? = null

    private val main = Handler(Looper.getMainLooper())
    private var artUrl: String? = null
    private lateinit var appCtx: Context

    fun init(ctx: Context) {
        if (session != null) return
        appCtx = ctx.applicationContext
        session = MediaSessionCompat(appCtx, "Lumen").apply {
            setCallback(object : MediaSessionCompat.Callback() {
                override fun onPlay() = control("play")
                override fun onPause() = control("pause")
                override fun onStop() = control("pause")
                override fun onSkipToNext() = control("next")
                override fun onSkipToPrevious() = control("prev")
                override fun onSeekTo(pos: Long) = control("seek", pos)
            })
        }
    }

    fun control(action: String, value: Long = 0) {
        main.post { controlHandler?.invoke(action, value) }
    }

    /** Stav z přehrávače (JSON z music.js); prázdný stav = nic nehraje. */
    fun update(json: String) {
        val o = try { JSONObject(json) } catch (e: Exception) { return }
        main.post {
            if (o.optBoolean("stop") || o.optString("title").isEmpty()) {
                clear()
                return@post
            }
            val s = State(o.optString("title"), o.optString("artist"), o.optString("album"), o.optString("art"),
                o.optBoolean("playing"), o.optLong("position"), o.optLong("duration"))
            val trackChanged = state?.title != s.title || state?.artist != s.artist
            state = s
            if (s.art != artUrl) loadArt(s.art)
            apply(trackChanged)
        }
    }

    /** Okno se zavřelo (WebView pryč): oznámení zpátky na „Lumen běží“. */
    fun clear() {
        state = null
        session?.isActive = false
        LumenService.refresh(appCtx)
    }

    private fun apply(metadata: Boolean) {
        val s = state ?: return
        val ses = session ?: return
        if (metadata || ses.controller.metadata == null) {
            ses.setMetadata(MediaMetadataCompat.Builder()
                .putString(MediaMetadataCompat.METADATA_KEY_TITLE, s.title)
                .putString(MediaMetadataCompat.METADATA_KEY_ARTIST, s.artist)
                .putString(MediaMetadataCompat.METADATA_KEY_ALBUM, s.album)
                .putLong(MediaMetadataCompat.METADATA_KEY_DURATION, s.duration)
                .putBitmap(MediaMetadataCompat.METADATA_KEY_ALBUM_ART, artBitmap)
                .build())
        }
        ses.setPlaybackState(PlaybackStateCompat.Builder()
            .setActions(PlaybackStateCompat.ACTION_PLAY or PlaybackStateCompat.ACTION_PAUSE or
                PlaybackStateCompat.ACTION_PLAY_PAUSE or PlaybackStateCompat.ACTION_SKIP_TO_NEXT or
                PlaybackStateCompat.ACTION_SKIP_TO_PREVIOUS or PlaybackStateCompat.ACTION_SEEK_TO or
                PlaybackStateCompat.ACTION_STOP)
            .setState(if (s.playing) PlaybackStateCompat.STATE_PLAYING else PlaybackStateCompat.STATE_PAUSED,
                s.position, if (s.playing) 1f else 0f)
            .build())
        ses.isActive = true
        LumenService.refresh(appCtx)
    }

    private fun loadArt(url: String) {
        artUrl = url
        artBitmap = null
        if (url.isEmpty()) return
        Thread {
            val bmp = try {
                val c = URL(url).openConnection() as HttpURLConnection
                c.connectTimeout = 8000
                c.readTimeout = 8000
                try { c.inputStream.use { BitmapFactory.decodeStream(it) } } finally { c.disconnect() }
            } catch (e: Exception) { null }
            val scaled = bmp?.let { if (it.width > 512) Bitmap.createScaledBitmap(it, 512, 512 * it.height / it.width, true) else it }
            main.post {
                if (artUrl == url) {
                    artBitmap = scaled
                    apply(true)
                }
            }
        }.start()
    }
}
