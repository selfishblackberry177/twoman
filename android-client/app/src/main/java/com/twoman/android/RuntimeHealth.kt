package com.twoman.android

import android.app.ActivityManager
import android.content.Context
import java.net.InetSocketAddress
import java.net.Socket

object RuntimeHealth {
    private const val STARTUP_GRACE_MS = 12_000L

    fun resolve(context: Context, status: RuntimeStatus): RuntimeStatus {
        if (!status.running) {
            return status
        }
        val proxyServiceRunning = isServiceRunning(context, ProxyService::class.java.name)
        val vpnServiceRunning = isServiceRunning(context, TunnelVpnService::class.java.name)
        val anyServiceRunning = proxyServiceRunning || vpnServiceRunning
        val anyPortListening = isListening(status.socksPort) || isListening(status.httpPort)
        val statusAgeMs = System.currentTimeMillis() - status.updatedAtEpochMs

        if (!anyServiceRunning && !anyPortListening) {
            return status.copy(
                running = false,
                mode = "stopped",
                message = "",
            )
        }
        if (anyServiceRunning) {
            return status
        }
        if (status.updatedAtEpochMs != 0L && statusAgeMs <= STARTUP_GRACE_MS) {
            return status
        }
        if (anyPortListening) {
            return status
        }
        return status.copy(
            running = false,
            mode = "stopped",
            message = "",
        )
    }

    @Suppress("DEPRECATION")
    private fun isServiceRunning(context: Context, className: String): Boolean {
        val activityManager = context.getSystemService(ActivityManager::class.java) ?: return false
        return activityManager
            .getRunningServices(Int.MAX_VALUE)
            .any { it.service.className == className }
    }

    private fun isListening(port: Int): Boolean {
        if (port <= 0) {
            return false
        }
        return runCatching {
            Socket().use { socket ->
                socket.connect(InetSocketAddress("127.0.0.1", port), 250)
            }
        }.isSuccess
    }
}
