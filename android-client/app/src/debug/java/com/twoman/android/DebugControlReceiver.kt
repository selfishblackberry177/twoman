package com.twoman.android

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.util.Log

class DebugControlReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        val loggerTag = "TwomanDebug"
        val profile = ProfileStore(context).loadProfiles().firstOrNull()
        when (action) {
            ACTION_START_PROXY -> {
                if (profile == null) {
                    Log.w(loggerTag, "no profile available for debug proxy start")
                    return
                }
                Log.i(loggerTag, "debug start proxy profile=${profile.name}")
                ProxyService.start(context, profile, ProxyService.MODE_PROXY)
            }

            ACTION_START_VPN -> {
                if (profile == null) {
                    Log.w(loggerTag, "no profile available for debug vpn start")
                    return
                }
                if (VpnService.prepare(context) != null) {
                    Log.w(loggerTag, "vpn permission not granted")
                    return
                }
                Log.i(loggerTag, "debug start vpn profile=${profile.name}")
                TunnelVpnService.start(context, profile)
            }

            ACTION_STOP -> {
                Log.i(loggerTag, "debug stop request")
                val state = RuntimeStateStore(context).read()
                if (state.mode == ProxyService.MODE_VPN ||
                    RuntimeHealth.isServiceRunning(context, TunnelVpnService::class.java.name)
                ) {
                    TunnelVpnService.stop(context)
                }
                if (state.mode == ProxyService.MODE_PROXY ||
                    state.mode == ProxyService.MODE_VPN ||
                    RuntimeHealth.isServiceRunning(context, ProxyService::class.java.name)
                ) {
                    ProxyService.stop(context)
                }
            }
        }
    }

    companion object {
        const val ACTION_START_PROXY = "com.twoman.android.debug.START_PROXY"
        const val ACTION_START_VPN = "com.twoman.android.debug.START_VPN"
        const val ACTION_STOP = "com.twoman.android.debug.STOP"
    }
}
