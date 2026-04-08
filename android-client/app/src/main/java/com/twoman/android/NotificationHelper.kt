package com.twoman.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat

object NotificationHelper {
    const val CHANNEL_ID = "twoman-runtime"
    const val PROXY_NOTIFICATION_ID = 2001
    const val VPN_NOTIFICATION_ID = 2002

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return
        }
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) != null) {
            return
        }
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.runtime_channel_name),
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
    }

    fun build(
        context: Context,
        title: String,
        text: String,
    ): Notification {
        val intent = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(intent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }
}
