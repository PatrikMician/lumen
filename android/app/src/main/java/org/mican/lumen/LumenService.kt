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

/** Drží server Lumenu naživu i s aplikací na pozadí (stahování, hudba). Ukončí ho tlačítko v oznámení. */
class LumenService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            LumenRuntime.stop()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            sendBroadcast(Intent(ACTION_STOPPED).setPackage(packageName))
            return START_NOT_STICKY
        }
        startForeground(1, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        LumenRuntime.start(this)
        return START_STICKY
    }

    override fun onDestroy() {
        LumenRuntime.stop()
        super.onDestroy()
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Lumen běží", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Oznámení, že Lumen běží na pozadí (stahování a hudba)."
            setShowBadge(false)
        })
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1, Intent(this, LumenService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle("Lumen běží")
            .setContentText("Stahování a hudba fungují i na pozadí.")
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Ukončit", stop).build())
            .build()
    }

    companion object {
        private const val CHANNEL = "lumen"
        const val ACTION_STOP = "org.mican.lumen.STOP"
        const val ACTION_STOPPED = "org.mican.lumen.STOPPED"

        fun start(ctx: Context) = ctx.startForegroundService(Intent(ctx, LumenService::class.java))
    }
}
