package com.twoman.android

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.recyclerview.widget.LinearLayoutManager
import com.twoman.android.databinding.ActivityMainBinding
import com.twoman.android.databinding.DialogProfileBinding

class MainActivity : AppCompatActivity() {
    private val loggerTag = "TwomanUi"
    private lateinit var binding: ActivityMainBinding
    private lateinit var profileStore: ProfileStore
    private lateinit var stateStore: RuntimeStateStore
    private lateinit var adapter: ProfileAdapter
    private val uiHandler = Handler(Looper.getMainLooper())
    private var pendingVpnProfile: ClientProfile? = null

    private val statusTicker = object : Runnable {
        override fun run() {
            renderStatus()
            uiHandler.postDelayed(this, 1000)
        }
    }

    private val vpnPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
            val profile = pendingVpnProfile ?: return@registerForActivityResult
            pendingVpnProfile = null
            if (it.resultCode != Activity.RESULT_OK) {
                Toast.makeText(this, getString(R.string.vpn_permission_denied), Toast.LENGTH_SHORT).show()
                renderStatus()
                return@registerForActivityResult
            }
            stopEverything()
            markRuntimeStarting(profile, ProxyService.MODE_VPN)
            TunnelVpnService.start(this, profile)
            renderStatus()
        }

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        applyInsets()

        profileStore = ProfileStore(this)
        stateStore = RuntimeStateStore(this)

        adapter = ProfileAdapter(
            onProxy = { startProxy(it) },
            onVpn = { startVpn(it) },
            onEdit = { showProfileDialog(it) },
            onShare = { shareProfile(it) },
            onDelete = { deleteProfile(it) },
        )
        binding.profileList.layoutManager = LinearLayoutManager(this)
        binding.profileList.adapter = adapter
        binding.addButton.setOnClickListener { showProfileDialog(null) }
        binding.stopButton.setOnClickListener { stopEverything() }
        binding.logsButton.setOnClickListener {
            startActivity(Intent(this, LogActivity::class.java))
        }

        requestNotificationPermissionIfNeeded()
        reloadProfiles()
        renderStatus()
    }

    override fun onStart() {
        super.onStart()
        uiHandler.post(statusTicker)
    }

    override fun onStop() {
        uiHandler.removeCallbacks(statusTicker)
        super.onStop()
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun reloadProfiles() {
        adapter.submit(profileStore.loadProfiles().sortedBy { it.name.lowercase() })
        adapter.setRuntimeStatus(resolveRuntimeStatus())
    }

    private fun renderStatus() {
        val status = resolveRuntimeStatus()
        adapter.setRuntimeStatus(status)
        binding.stopButton.isEnabled = status.running
        binding.stopButton.text = getString(
            if (status.running) R.string.action_stop else R.string.action_stopped,
        )
        when {
            !status.running -> {
                binding.statusText.text = getString(R.string.status_stopped)
                binding.portsText.text = status.message.ifBlank { getString(R.string.status_idle_message) }
            }
            status.mode == ProxyService.MODE_VPN -> {
                binding.statusText.text = "${getString(R.string.status_vpn)}  ${status.profileName}"
                binding.portsText.text = "HTTP ${status.httpPort}   SOCKS ${status.socksPort}"
            }
            else -> {
                binding.statusText.text = "${getString(R.string.status_proxy)}  ${status.profileName}"
                binding.portsText.text = "HTTP ${status.httpPort}   SOCKS ${status.socksPort}"
            }
        }
    }

    private fun resolveRuntimeStatus(): RuntimeStatus {
        val rawStatus = stateStore.read()
        val resolvedStatus = RuntimeHealth.resolve(this, rawStatus)
        if (resolvedStatus != rawStatus) {
            stateStore.write(resolvedStatus)
        }
        return resolvedStatus
    }

    private fun applyInsets() {
        val initialTop = binding.root.paddingTop
        val initialBottom = binding.root.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.updatePadding(
                top = initialTop + systemBars.top,
                bottom = initialBottom + systemBars.bottom,
            )
            insets
        }
        ViewCompat.requestApplyInsets(binding.root)
    }

    private fun startProxy(profile: ClientProfile) {
        Log.i(loggerTag, "startProxy profile=${profile.name} id=${profile.id}")
        stopEverything()
        markRuntimeStarting(profile, ProxyService.MODE_PROXY)
        ProxyService.start(this, profile, ProxyService.MODE_PROXY)
        Toast.makeText(this, getString(R.string.toast_proxy_starting), Toast.LENGTH_SHORT).show()
    }

    private fun startVpn(profile: ClientProfile) {
        Log.i(loggerTag, "startVpn profile=${profile.name} id=${profile.id}")
        val intent = VpnService.prepare(this)
        if (intent != null) {
            pendingVpnProfile = profile
            vpnPermissionLauncher.launch(intent)
            return
        }
        stopEverything()
        markRuntimeStarting(profile, ProxyService.MODE_VPN)
        TunnelVpnService.start(this, profile)
        Toast.makeText(this, getString(R.string.toast_vpn_starting), Toast.LENGTH_SHORT).show()
    }

    private fun stopEverything() {
        Log.i(loggerTag, "stopEverything")
        TunnelVpnService.stop(this)
        ProxyService.stop(this)
        stateStore.write(RuntimeStatus())
        renderStatus()
    }

    private fun markRuntimeStarting(profile: ClientProfile, mode: String) {
        stateStore.write(
            RuntimeStatus(
                running = true,
                mode = mode,
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

    private fun deleteProfile(profile: ClientProfile) {
        val updated = profileStore.loadProfiles().filterNot { it.id == profile.id }
        profileStore.saveProfiles(updated)
        reloadProfiles()
    }

    private fun showProfileDialog(existing: ClientProfile?) {
        Log.i(loggerTag, "showProfileDialog existing=${existing?.name ?: "new"}")
        val dialogBinding = DialogProfileBinding.inflate(layoutInflater)
        val profile = existing ?: ClientProfile(
            name = "",
            brokerBaseUrl = "",
            clientToken = "",
        )
        val importedProfileHolder = arrayOfNulls<ClientProfile>(1)
        importedProfileHolder[0] = existing

        dialogBinding.nameInput.setText(profile.name)
        dialogBinding.brokerInput.setText(profile.brokerBaseUrl)
        dialogBinding.tokenInput.setText(profile.clientToken)
        dialogBinding.httpPortInput.setText(profile.httpPort.toString())
        dialogBinding.socksPortInput.setText(profile.socksPort.toString())
        dialogBinding.verifyTlsSwitch.isChecked = profile.verifyTls
        dialogBinding.http2CtlSwitch.isChecked = profile.http2Ctl
        dialogBinding.http2DataSwitch.isChecked = profile.http2Data
        dialogBinding.importButton.setOnClickListener {
            importedProfileHolder[0] = applyImportedProfile(dialogBinding)
        }

        AlertDialog.Builder(this)
            .setView(dialogBinding.root)
            .setPositiveButton(R.string.action_save) { _, _ ->
                saveProfile(existing, importedProfileHolder[0], dialogBinding)
            }
            .setNegativeButton(R.string.action_cancel, null)
            .show()
    }

    private fun applyImportedProfile(dialogBinding: DialogProfileBinding): ClientProfile? {
        val imported = runCatching {
            ClientProfile.fromShareText(dialogBinding.importInput.text?.toString().orEmpty())
        }.getOrElse {
            Toast.makeText(this, getString(R.string.import_failed), Toast.LENGTH_SHORT).show()
            return null
        }
        dialogBinding.nameInput.setText(imported.name)
        dialogBinding.brokerInput.setText(imported.brokerBaseUrl)
        dialogBinding.tokenInput.setText(imported.clientToken)
        dialogBinding.httpPortInput.setText(imported.httpPort.toString())
        dialogBinding.socksPortInput.setText(imported.socksPort.toString())
        dialogBinding.verifyTlsSwitch.isChecked = imported.verifyTls
        dialogBinding.http2CtlSwitch.isChecked = imported.http2Ctl
        dialogBinding.http2DataSwitch.isChecked = imported.http2Data
        Toast.makeText(this, getString(R.string.import_applied), Toast.LENGTH_SHORT).show()
        return imported
    }

    private fun saveProfile(
        existing: ClientProfile?,
        importedProfile: ClientProfile?,
        dialogBinding: DialogProfileBinding,
    ) {
        val name = dialogBinding.nameInput.text.toString().trim()
        val broker = dialogBinding.brokerInput.text.toString().trim()
        val token = dialogBinding.tokenInput.text.toString().trim()
        val httpPort = dialogBinding.httpPortInput.text.toString().toIntOrNull() ?: 28167
        val socksPort = dialogBinding.socksPortInput.text.toString().toIntOrNull() ?: 21167
        if (name.isBlank() || broker.isBlank() || token.isBlank()) {
            Toast.makeText(this, getString(R.string.save_requires_fields), Toast.LENGTH_SHORT).show()
            return
        }
        val baseProfile = importedProfile ?: existing
        val profile = ClientProfile(
            id = existing?.id ?: java.util.UUID.randomUUID().toString(),
            name = name,
            brokerBaseUrl = broker,
            clientToken = token,
            verifyTls = dialogBinding.verifyTlsSwitch.isChecked,
            http2Ctl = dialogBinding.http2CtlSwitch.isChecked,
            http2Data = dialogBinding.http2DataSwitch.isChecked,
            httpPort = httpPort,
            socksPort = socksPort,
            httpTimeoutSeconds = baseProfile?.httpTimeoutSeconds ?: 30,
            flushDelaySeconds = baseProfile?.flushDelaySeconds ?: 0.01,
            maxBatchBytes = baseProfile?.maxBatchBytes ?: 65536,
            dataUploadMaxBatchBytes = baseProfile?.dataUploadMaxBatchBytes ?: 65536,
            dataUploadFlushDelaySeconds = baseProfile?.dataUploadFlushDelaySeconds ?: 0.004,
            vpnDnsServers = baseProfile?.vpnDnsServers ?: listOf("1.1.1.1", "8.8.8.8"),
            idleRepollCtlSeconds = baseProfile?.idleRepollCtlSeconds ?: 0.05,
            idleRepollDataSeconds = baseProfile?.idleRepollDataSeconds ?: 0.1,
            traceEnabled = baseProfile?.traceEnabled ?: false,
        )
        val updated = profileStore.loadProfiles()
            .filterNot { it.id == profile.id }
            .plus(profile)
        profileStore.saveProfiles(updated)
        reloadProfiles()
    }

    private fun shareProfile(profile: ClientProfile) {
        val shareIntent = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, profile.toShareText())
        }
        startActivity(Intent.createChooser(shareIntent, getString(R.string.share_chooser_title)))
    }
}
