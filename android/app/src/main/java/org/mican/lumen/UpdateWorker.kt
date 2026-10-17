package org.mican.lumen

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

/**
 * Kontrola aktualizace na pozadí (i když Lumen neběží): server Lumenu je zapnutý jen při vydání nové verze,
 * proto se aplikace ptá každých pár hodin. Když server neodpovídá, nic se neděje. Nová verze → oznámení,
 * klepnutí otevře Lumen na stránce aktualizace (stažení s ukazatelem průběhu a instalace).
 */
class UpdateWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {

    override fun doWork(): Result {
        val latest = try {
            val c = URL("${BuildConfig.LUMEN_SERVER}/api/app").openConnection() as HttpURLConnection
            c.connectTimeout = 8000
            c.readTimeout = 8000
            c.setRequestProperty("User-Agent", "LumenAndroid/${BuildConfig.VERSION_NAME}")
            try {
                if (c.responseCode != 200) return Result.success()
                JSONObject(c.inputStream.bufferedReader().readText()).optJSONObject("android")?.optString("version")
            } finally {
                c.disconnect()
            }
        } catch (e: Exception) {
            return Result.success()  // server je většinou vypnutý, to je v pořádku
        } ?: return Result.success()
        if (!newer(latest, BuildConfig.VERSION_NAME)) return Result.success()

        val prefs = applicationContext.getSharedPreferences("lumen", Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        if (prefs.getString("updateNotified", "") == latest && now - prefs.getLong("updateNotifiedAt", 0) < 12 * 3600_000L) {
            return Result.success()
        }
        notify(applicationContext, latest)
        prefs.edit().putString("updateNotified", latest).putLong("updateNotifiedAt", now).apply()
        return Result.success()
    }

    companion object {
        private const val CHANNEL = "lumen-update"
        const val ACTION_UPDATE = "org.mican.lumen.UPDATE"

        fun schedule(ctx: Context) {
            val req = PeriodicWorkRequestBuilder<UpdateWorker>(4, TimeUnit.HOURS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("lumen-update", ExistingPeriodicWorkPolicy.KEEP, req)
        }

        fun newer(a: String, b: String): Boolean {
            val x = Regex("\\d+").findAll(a).map { it.value.toInt() }.toList()
            val y = Regex("\\d+").findAll(b).map { it.value.toInt() }.toList()
            for (i in 0 until maxOf(x.size, y.size)) {
                val p = x.getOrElse(i) { 0 }
                val q = y.getOrElse(i) { 0 }
                if (p != q) return p > q
            }
            return false
        }

        private fun notify(ctx: Context, version: String) {
            if (Build.VERSION.SDK_INT >= 33 &&
                ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Aktualizace Lumenu", NotificationManager.IMPORTANCE_HIGH)
                .apply { description = "Upozornění na novou verzi Lumenu." })
            val open = PendingIntent.getActivity(ctx, 2,
                Intent(ctx, MainActivity::class.java).setAction(ACTION_UPDATE).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val n = NotificationCompat.Builder(ctx, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle("Je dostupná aktualizace Lumenu")
                .setContentText("Verze $version. Doporučujeme aktualizovat hned.")
                .setContentIntent(open)
                .setAutoCancel(true)
                .addAction(0, "Aktualizovat", open)
                .build()
            NotificationManagerCompat.from(ctx).notify(3, n)
        }
    }
}
