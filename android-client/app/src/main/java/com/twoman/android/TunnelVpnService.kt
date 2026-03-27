package com.twoman.android

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import engine.Engine
import engine.Key
import org.json.JSONObject
import java.net.InetSocketAddress
import java.net.Socket
import kotlin.concurrent.thread

class TunnelVpnService : VpnService() {
    private val loggerTag = "TwomanSvc"
    private var vpnInterface: ParcelFileDescriptor? = null
    private lateinit var stateStore: RuntimeStateStore
    private var activeProfile: ClientProfile? = null
    @Volatile
    private var workerStarted = false

    override fun onCreate() {
        super.onCreate()
        stateStore = RuntimeStateStore(this)
        Log.i(loggerTag, "TunnelVpnService onCreate")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val profileJson = intent?.getStringExtra(EXTRA_PROFILE_JSON) ?: return START_NOT_STICKY
        val profile = ClientProfile.fromJson(JSONObject(profileJson))
        Log.i(loggerTag, "TunnelVpnService onStartCommand profile=${profile.name}")
        activeProfile = profile
        NotificationHelper.ensureChannel(this)
        startForeground(
            NotificationHelper.VPN_NOTIFICATION_ID,
            NotificationHelper.build(
                this,
                getString(R.string.notification_vpn_title),
                profile.name,
            ),
        )
        if (!workerStarted) {
            workerStarted = true
            thread(name = "twoman-vpn-start", start = true) {
                runCatching { startTunnel(profile) }
                    .onFailure { error ->
                        stateStore.write(
                            RuntimeStatus(
                                running = false,
                                mode = "stopped",
                                profileId = profile.id,
                                profileName = profile.name,
                                brokerBaseUrl = profile.brokerBaseUrl,
                                httpPort = profile.httpPort,
                                socksPort = profile.socksPort,
                                logPath = AppFiles.runtimeLogFile(this, profile.id).absolutePath,
                                message = error.message?.takeIf { it.isNotBlank() }
                                    ?: error.javaClass.simpleName,
                            ),
                        )
                        stopSelf()
                    }
            }
        }
        return START_NOT_STICKY
    }

    private fun startTunnel(profile: ClientProfile) {
        ProxyService.start(this, profile, ProxyService.MODE_VPN)
        waitForLocalPort(profile.socksPort)

        val configureIntent = PendingIntent.getActivity(
            this,
            1,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = Builder()
            .setSession(profile.name)
            .setMtu(1500)
            .addAddress("198.18.0.1", 32)
            .addRoute("0.0.0.0", 0)
            .setConfigureIntent(configureIntent)

        profile.vpnDnsServers.forEach { dnsServer ->
            runCatching { builder.addDnsServer(dnsServer) }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            runCatching { builder.addDisallowedApplication(packageName) }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            builder.setMetered(false)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            builder.setBlocking(true)
        }

        vpnInterface = builder.establish() ?: error("failed to establish VPN interface")

        val key = Key().apply {
            mark = 0
            mtu = 1500
            device = "fd://${vpnInterface!!.fd}"
            proxy = "socks5://127.0.0.1:${profile.socksPort}"
            `interface` = ""
            logLevel = if (profile.traceEnabled) "debug" else "info"
            restAPI = ""
            tcpSendBufferSize = ""
            tcpReceiveBufferSize = ""
            tcpModerateReceiveBuffer = false
        }

        Engine.insert(key)
        Engine.start()
        stateStore.write(
            RuntimeStatus(
                running = true,
                mode = ProxyService.MODE_VPN,
                profileId = profile.id,
                profileName = profile.name,
                brokerBaseUrl = profile.brokerBaseUrl,
                httpPort = profile.httpPort,
                socksPort = profile.socksPort,
                logPath = AppFiles.runtimeLogFile(this, profile.id).absolutePath,
                message = "",
            ),
        )
    }

    private fun waitForLocalPort(port: Int) {
        repeat(50) {
            runCatching {
                Socket().use { socket ->
                    socket.connect(InetSocketAddress("127.0.0.1", port), 250)
                }
            }.onSuccess { return }
            Thread.sleep(200)
        }
        error("local SOCKS proxy did not start")
    }

    override fun onDestroy() {
        Log.i(loggerTag, "TunnelVpnService onDestroy")
        workerStarted = false
        runCatching { Engine.stop() }
        runCatching { vpnInterface?.close() }
        vpnInterface = null
        ProxyService.stop(this)
        activeProfile?.let { profile ->
            stateStore.write(
                RuntimeStatus(
                    running = false,
                    mode = "stopped",
                    profileId = profile.id,
                    profileName = profile.name,
                    brokerBaseUrl = profile.brokerBaseUrl,
                    httpPort = profile.httpPort,
                    socksPort = profile.socksPort,
                    logPath = AppFiles.runtimeLogFile(this, profile.id).absolutePath,
                    message = "",
                ),
            )
        }
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    companion object {
        private const val ACTION_START = "com.twoman.android.action.VPN_START"
        const val EXTRA_PROFILE_JSON = "profile_json"

        fun start(context: Context, profile: ClientProfile) {
            context.startForegroundService(
                Intent(context, TunnelVpnService::class.java).apply {
                    action = ACTION_START
                    putExtra(EXTRA_PROFILE_JSON, profile.toJson().toString())
                },
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, TunnelVpnService::class.java))
        }
    }
}
