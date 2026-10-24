package org.mican.lumen

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.media.app.NotificationCompat.MediaStyle

/**
 * Drží server Lumenu naživu i s aplikací na pozadí (stahování, hudba). Ukončí ho tlačítko v oznámení.
 * Když hraje hudba, je z oznámení přehrávač (obal, název, ⏮ ⏯ ⏭) napojený na MediaSession z [LumenMedia].
 */
class LumenService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                LumenMedia.control("pause")
                running = false
                LumenRuntime.stop()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                sendBroadcast(Intent(ACTION_STOPPED).setPackage(packageName))
                return START_NOT_STICKY
            }
            ACTION_PLAY_PAUSE -> { LumenMedia.control(if (LumenMedia.state?.playing == true) "pause" else "play"); return START_STICKY }
            ACTION_NEXT -> { LumenMedia.control("next"); return START_STICKY }
            ACTION_PREV -> { LumenMedia.control("prev"); return START_STICKY }
        }
        running = true
        startForeground(NOTIFICATION_ID, build(this), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        LumenRuntime.start(this)
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        LumenRuntime.stop()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "lumen"
        private const val NOTIFICATION_ID = 1
        @Volatile private var running = false
        const val ACTION_STOP = "org.mican.lumen.STOP"
        const val ACTION_STOPPED = "org.mican.lumen.STOPPED"
        private const val ACTION_PLAY_PAUSE = "org.mican.lumen.PLAY_PAUSE"
        private const val ACTION_NEXT = "org.mican.lumen.NEXT"
        private const val ACTION_PREV = "org.mican.lumen.PREV"

        fun start(ctx: Context) = ctx.startForegroundService(Intent(ctx, LumenService::class.java))

        /** Překreslí oznámení podle toho, co hraje (volá LumenMedia). */
        fun refresh(ctx: Context) {
            if (!running) return  // ukončený Lumen oznámení znovu neukazuje
            ctx.getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, build(ctx))
        }

        private fun action(ctx: Context, code: Int, action: String) =
            PendingIntent.getService(ctx, code, Intent(ctx, LumenService::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE)

        private fun build(ctx: Context): Notification {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Lumen běží", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Lumen na pozadí: přehrávač hudby a stahování."
                setShowBadge(false)
            })
            val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val stop = action(ctx, 1, ACTION_STOP)
            val s = LumenMedia.state
            val session = LumenMedia.session
            if (s == null || session == null) {
                return NotificationCompat.Builder(ctx, CHANNEL)
                    .setSmallIcon(android.R.drawable.stat_sys_download_done)
                    .setContentTitle("Lumen běží")
                    .setContentText("Stahování a hudba fungují i na pozadí.")
                    .setContentIntent(open)
                    .setOngoing(true)
                    .addAction(0, "Ukončit", stop)
                    .build()
            }
            return NotificationCompat.Builder(ctx, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(s.title)
                .setContentText(s.artist.ifEmpty { s.album })
                .setLargeIcon(LumenMedia.artBitmap)
                .setContentIntent(open)
                .setDeleteIntent(stop)
                .setOngoing(s.playing)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)  // i na zamykací obrazovce
                .setCategory(NotificationCompat.CATEGORY_TRANSPORT)
                .addAction(android.R.drawable.ic_media_previous, "Předchozí", action(ctx, 2, ACTION_PREV))
                .addAction(if (s.playing) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play,
                    if (s.playing) "Pozastavit" else "Přehrát", action(ctx, 3, ACTION_PLAY_PAUSE))
                .addAction(android.R.drawable.ic_media_next, "Další", action(ctx, 4, ACTION_NEXT))
                .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Ukončit", stop)
                .setStyle(MediaStyle().setMediaSession(session.sessionToken).setShowActionsInCompactView(0, 1, 2))
                .build()
        }
    }
}
