package com.twoman.android

import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.Process
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import kotlin.concurrent.thread

class ProxyService : Service() {
    private val loggerTag = "TwomanSvc"
    private var helperThread: Thread? = null
    private lateinit var stateStore: RuntimeStateStore

    override fun onCreate() {
        super.onCreate()
        stateStore = RuntimeStateStore(this)
        Log.i(loggerTag, "ProxyService onCreate")
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val profileJson = intent?.getStringExtra(EXTRA_PROFILE_JSON) ?: return START_NOT_STICKY
        val mode = intent.getStringExtra(EXTRA_MODE) ?: MODE_PROXY
        val profile = ClientProfile.fromJson(JSONObject(profileJson))
        Log.i(loggerTag, "ProxyService onStartCommand mode=$mode profile=${profile.name}")
        NotificationHelper.ensureChannel(this)
        startForeground(
            NotificationHelper.PROXY_NOTIFICATION_ID,
            NotificationHelper.build(
                this,
                getString(R.string.notification_proxy_title),
                "${profile.name}  ${profile.socksPort}/${profile.httpPort}",
            ),
        )
        if (helperThread == null) {
            helperThread = thread(name = "twoman-python-helper", start = true) {
                runHelper(profile, mode)
            }
        }
        return START_NOT_STICKY
    }

    private fun runHelper(profile: ClientProfile, mode: String) {
        val configFile = AppFiles.runtimeConfigFile(this, profile.id)
        val logFile = AppFiles.runtimeLogFile(this, profile.id)
        configFile.writeText(profile.toRuntimeConfig(logFile.absolutePath).toString(2), Charsets.UTF_8)
        stateStore.write(
            RuntimeStatus(
                running = true,
                mode = mode,
                profileId = profile.id,
                profileName = profile.name,
                brokerBaseUrl = profile.brokerBaseUrl,
                httpPort = profile.httpPort,
                socksPort = profile.socksPort,
                logPath = logFile.absolutePath,
                message = "",
            ),
        )
        try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(applicationContext))
            }
            val module = Python.getInstance().getModule("android_entry")
            module.callAttr("run_helper", configFile.absolutePath)
        } catch (error: Throwable) {
            stateStore.write(
                RuntimeStatus(
                    running = false,
                    mode = "stopped",
                    profileId = profile.id,
                    profileName = profile.name,
                    brokerBaseUrl = profile.brokerBaseUrl,
                    httpPort = profile.httpPort,
                    socksPort = profile.socksPort,
                    logPath = logFile.absolutePath,
                    message = error.message ?: error.javaClass.simpleName,
                ),
            )
        } finally {
            stopSelf()
        }
    }

    override fun onDestroy() {
        Log.i(loggerTag, "ProxyService onDestroy")
        stateStore.write(stateStore.read().copy(running = false, mode = "stopped"))
        stopForeground(STOP_FOREGROUND_REMOVE)
        Process.killProcess(Process.myPid())
        super.onDestroy()
    }

    companion object {
        private const val ACTION_START = "com.twoman.android.action.PROXY_START"
        const val EXTRA_PROFILE_JSON = "profile_json"
        const val EXTRA_MODE = "mode"
        const val MODE_PROXY = "proxy"
        const val MODE_VPN = "vpn"

        fun start(context: Context, profile: ClientProfile, mode: String) {
            context.startForegroundService(
                Intent(context, ProxyService::class.java).apply {
                    action = ACTION_START
                    putExtra(EXTRA_PROFILE_JSON, profile.toJson().toString())
                    putExtra(EXTRA_MODE, mode)
                },
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, ProxyService::class.java))
        }
    }
}
