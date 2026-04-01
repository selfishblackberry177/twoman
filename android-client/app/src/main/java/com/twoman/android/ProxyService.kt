package com.twoman.android

import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Process
import android.os.IBinder
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import kotlin.concurrent.thread

class ProxyService : Service() {
    private val loggerTag = BuildConfig.RUNTIME_LOG_TAG
    private var helperThread: Thread? = null
    private var listenWatcherThread: Thread? = null
    private lateinit var stateStore: RuntimeStateStore
    @Volatile
    private var currentMode: String = MODE_PROXY
    @Volatile
    private var currentProfile: ClientProfile? = null
    @Volatile
    private var stopRequested = false

    override fun onCreate() {
        super.onCreate()
        stateStore = RuntimeStateStore(this)
        Log.i(loggerTag, "ProxyService onCreate")
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            Log.i(loggerTag, "ProxyService stop requested")
            requestStop()
            return START_NOT_STICKY
        }
        val profileJson = intent?.getStringExtra(EXTRA_PROFILE_JSON) ?: return START_NOT_STICKY
        val mode = intent.getStringExtra(EXTRA_MODE) ?: MODE_PROXY
        val profile = ClientProfile.fromJson(JSONObject(profileJson))
        Log.i(loggerTag, "ProxyService onStartCommand mode=$mode profile=${profile.name}")
        currentMode = mode
        currentProfile = profile
        stopRequested = false
        NotificationHelper.ensureChannel(this)
        startForeground(
            NotificationHelper.PROXY_NOTIFICATION_ID,
            NotificationHelper.build(
                this,
                getString(R.string.runtime_proxy_title),
                getString(R.string.status_starting_message),
            ),
        )
        if (helperThread == null) {
            helperThread = thread(name = "local-runtime-helper", start = true) {
                runHelper(profile, mode)
            }
            listenWatcherThread = thread(name = "local-runtime-listen-watch", start = true) {
                waitForListenState(profile, mode)
            }
        }
        return START_NOT_STICKY
    }

    private fun runHelper(profile: ClientProfile, mode: String) {
        val configFile = AppFiles.runtimeConfigFile(this, profile.id)
        val logFile = AppFiles.runtimeLogFile(this, profile.id)
        val listenStateFile = AppFiles.runtimeListenStateFile(this, profile.id)
        listenStateFile.delete()
        configFile.writeText(
            profile.toRuntimeConfig(logFile.absolutePath, listenStateFile.absolutePath).toString(2),
            Charsets.UTF_8,
        )
        stateStore.write(
            RuntimeStatus(
                running = true,
                mode = mode,
                profileId = profile.id,
                profileName = profile.name,
                brokerBaseUrl = profile.brokerBaseUrl,
                httpPort = 0,
                socksPort = 0,
                logPath = logFile.absolutePath,
                message = getString(R.string.status_starting_message),
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
                    httpPort = currentListenState(profile)?.httpPort ?: 0,
                    socksPort = currentListenState(profile)?.socksPort ?: 0,
                    logPath = logFile.absolutePath,
                    message = error.message ?: error.javaClass.simpleName,
                ),
            )
        } finally {
            helperThread = null
            listenWatcherThread = null
            stopSelf()
        }
    }

    private fun waitForListenState(profile: ClientProfile, mode: String) {
        repeat(75) {
            if (stopRequested || helperThread == null) {
                return
            }
            val listenState = currentListenState(profile)
            if (listenState != null && listenState.httpPort > 0 && listenState.socksPort > 0) {
                stateStore.write(
                    RuntimeStatus(
                        running = true,
                        mode = mode,
                        profileId = profile.id,
                        profileName = profile.name,
                        brokerBaseUrl = profile.brokerBaseUrl,
                        httpPort = listenState.httpPort,
                        socksPort = listenState.socksPort,
                        logPath = AppFiles.runtimeLogFile(this, profile.id).absolutePath,
                        message = "",
                    ),
                )
                return
            }
            Thread.sleep(200L)
        }
    }

    private fun currentListenState(profile: ClientProfile): RuntimeListenState? =
        AppFiles.readRuntimeListenState(this, profile.id)

    private fun requestStop() {
        stopRequested = true
        stopForeground(STOP_FOREGROUND_REMOVE)
        currentProfile?.let { profile ->
            val listenState = currentListenState(profile)
            stateStore.write(
                RuntimeStatus(
                    running = true,
                    mode = currentMode,
                    profileId = profile.id,
                    profileName = profile.name,
                    brokerBaseUrl = profile.brokerBaseUrl,
                    httpPort = listenState?.httpPort ?: 0,
                    socksPort = listenState?.socksPort ?: 0,
                    logPath = AppFiles.runtimeLogFile(this, profile.id).absolutePath,
                    message = getString(R.string.status_stopping_message),
                ),
            )
        }
        val threadToJoin = helperThread
        thread(name = "local-runtime-stop", start = true) {
            val stopped = runCatching {
                if (!Python.isStarted()) {
                    false
                } else {
                    Python.getInstance().getModule("android_entry").callAttr("stop_helper").toBoolean()
                }
            }.getOrElse { error ->
                Log.w(loggerTag, "ProxyService stop helper failed", error)
                false
            }
            Log.i(loggerTag, "ProxyService stop helper signalled=$stopped")
            if (threadToJoin != null) {
                runCatching { threadToJoin.join(2_500L) }
            }
            if (helperThread?.isAlive == true) {
                Log.w(loggerTag, "ProxyService helper thread still alive after stop timeout")
                currentProfile?.let { profile ->
                    stateStore.write(
                        RuntimeStatus(
                            running = false,
                            mode = "stopped",
                            profileId = profile.id,
                            profileName = profile.name,
                            brokerBaseUrl = profile.brokerBaseUrl,
                            httpPort = currentListenState(profile)?.httpPort ?: 0,
                            socksPort = currentListenState(profile)?.socksPort ?: 0,
                            logPath = AppFiles.runtimeLogFile(this, profile.id).absolutePath,
                            message = "",
                        ),
                    )
                }
                stopSelf()
                Process.killProcess(Process.myPid())
                return@thread
            }
            currentProfile?.let { profile ->
                stateStore.write(
                    RuntimeStatus(
                        running = false,
                        mode = "stopped",
                        profileId = profile.id,
                        profileName = profile.name,
                        brokerBaseUrl = profile.brokerBaseUrl,
                        httpPort = currentListenState(profile)?.httpPort ?: 0,
                        socksPort = currentListenState(profile)?.socksPort ?: 0,
                        logPath = AppFiles.runtimeLogFile(this, profile.id).absolutePath,
                        message = "",
                    ),
                )
            }
            stopSelf()
        }
    }

    override fun onDestroy() {
        Log.i(loggerTag, "ProxyService onDestroy")
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    companion object {
        private const val ACTION_START = "com.twoman.android.action.PROXY_START"
        private const val ACTION_STOP = "com.twoman.android.action.PROXY_STOP"
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
            context.startService(
                Intent(context, ProxyService::class.java).apply {
                    action = ACTION_STOP
                },
            )
        }
    }
}
