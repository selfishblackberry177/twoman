package com.twoman.android

import android.content.res.ColorStateList
import android.util.Log
import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.color.MaterialColors
import com.twoman.android.databinding.ItemProfileBinding

class ProfileAdapter(
    private val onSelectMode: (ClientProfile, String) -> Unit,
    private val onEdit: (ClientProfile) -> Unit,
    private val onShare: (ClientProfile) -> Unit,
    private val onDelete: (ClientProfile) -> Unit,
) : RecyclerView.Adapter<ProfileAdapter.ProfileViewHolder>() {
    private val loggerTag = "TwomanUi"

    private val profiles = mutableListOf<ClientProfile>()
    private var runtimeStatus = RuntimeStatus()
    private var selectedProfileId = ""
    private var selectedMode = ProxyService.MODE_PROXY

    fun submit(items: List<ClientProfile>) {
        profiles.clear()
        profiles.addAll(items)
        notifyDataSetChanged()
    }

    fun setRuntimeStatus(status: RuntimeStatus) {
        runtimeStatus = status
        notifyDataSetChanged()
    }

    fun setSelection(profileId: String, mode: String) {
        selectedProfileId = profileId
        selectedMode = mode
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ProfileViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return ProfileViewHolder(ItemProfileBinding.inflate(inflater, parent, false))
    }

    override fun onBindViewHolder(holder: ProfileViewHolder, position: Int) {
        holder.bind(profiles[position])
    }

    override fun getItemCount(): Int = profiles.size

    inner class ProfileViewHolder(
        private val binding: ItemProfileBinding,
    ) : RecyclerView.ViewHolder(binding.root) {
        fun bind(profile: ClientProfile) {
            val context = binding.root.context
            val isActiveProfile = runtimeStatus.running && runtimeStatus.profileId == profile.id
            val isActiveProxy = isActiveProfile && runtimeStatus.mode == ProxyService.MODE_PROXY
            val isActiveVpn = isActiveProfile && runtimeStatus.mode == ProxyService.MODE_VPN
            val isSelectedProfile = selectedProfileId == profile.id
            val isSelectedProxy = isSelectedProfile && selectedMode == ProxyService.MODE_PROXY
            val isSelectedVpn = isSelectedProfile && selectedMode == ProxyService.MODE_VPN
            val primaryColor = MaterialColors.getColor(binding.root, com.google.android.material.R.attr.colorPrimary)
            val onPrimaryColor = MaterialColors.getColor(binding.root, com.google.android.material.R.attr.colorOnPrimary)
            val surfaceVariantColor = MaterialColors.getColor(binding.root, com.google.android.material.R.attr.colorSurfaceVariant)
            val onSurfaceColor = MaterialColors.getColor(binding.root, com.google.android.material.R.attr.colorOnSurface)
            val outlineColor = MaterialColors.getColor(binding.root, com.google.android.material.R.attr.colorOutline)
            val secondaryColor = MaterialColors.getColor(binding.root, com.google.android.material.R.attr.colorSecondary)

            binding.profileName.text = profile.name
            binding.profileBroker.text = profile.brokerBaseUrl
            val shareAddress = if (profile.shareLanSocks) LanShareInfo.displayAddress(profile.socksPort) else null
            binding.profilePorts.text = listOfNotNull(
                "HTTP ${profile.httpPort}   SOCKS ${profile.socksPort}",
                shareAddress?.let { "LAN $it" },
            ).joinToString("\n")
            binding.profileModeState.visibility =
                if (isActiveProfile || isSelectedProfile) android.view.View.VISIBLE else android.view.View.GONE
            binding.profileModeState.text = when {
                isActiveProxy && shareAddress != null -> context.getString(R.string.profile_running_proxy_shared, shareAddress)
                isActiveVpn -> context.getString(R.string.profile_running_vpn)
                isActiveProxy -> context.getString(R.string.profile_running_proxy)
                isSelectedVpn -> context.getString(R.string.profile_selected_vpn)
                isSelectedProxy -> context.getString(R.string.profile_selected_proxy)
                else -> ""
            }
            binding.profileModeState.setTextColor(if (isActiveProfile) secondaryColor else primaryColor)
            binding.root.strokeWidth = if (isActiveProfile || isSelectedProfile) 4 else 0
            binding.root.strokeColor = when {
                isActiveProfile -> secondaryColor
                isSelectedProfile -> primaryColor
                else -> outlineColor
            }

            bindModeButton(
                active = isSelectedProxy,
                primaryColor = primaryColor,
                onPrimaryColor = onPrimaryColor,
                neutralColor = surfaceVariantColor,
                neutralTextColor = onSurfaceColor,
                button = binding.proxyButton,
                activeLabel = context.getString(R.string.action_selected_proxy),
                idleLabel = context.getString(R.string.action_proxy),
            )
            bindModeButton(
                active = isSelectedVpn,
                primaryColor = primaryColor,
                onPrimaryColor = onPrimaryColor,
                neutralColor = surfaceVariantColor,
                neutralTextColor = onSurfaceColor,
                button = binding.vpnButton,
                activeLabel = context.getString(R.string.action_selected_vpn),
                idleLabel = context.getString(R.string.action_vpn),
            )
            binding.proxyButton.setOnClickListener {
                Log.i(loggerTag, "select proxy profile=${profile.name} id=${profile.id}")
                onSelectMode(profile, ProxyService.MODE_PROXY)
            }
            binding.vpnButton.setOnClickListener {
                Log.i(loggerTag, "select vpn profile=${profile.name} id=${profile.id}")
                onSelectMode(profile, ProxyService.MODE_VPN)
            }
            binding.editButton.setOnClickListener {
                Log.i(loggerTag, "tap edit profile=${profile.name} id=${profile.id}")
                onEdit(profile)
            }
            binding.shareButton.setOnClickListener {
                Log.i(loggerTag, "tap share profile=${profile.name} id=${profile.id}")
                onShare(profile)
            }
            binding.deleteButton.setOnClickListener {
                Log.i(loggerTag, "tap delete profile=${profile.name} id=${profile.id}")
                onDelete(profile)
            }
        }

        private fun bindModeButton(
            active: Boolean,
            primaryColor: Int,
            onPrimaryColor: Int,
            neutralColor: Int,
            neutralTextColor: Int,
            button: com.google.android.material.button.MaterialButton,
            activeLabel: String,
            idleLabel: String,
        ) {
            button.text = if (active) activeLabel else idleLabel
            button.backgroundTintList = ColorStateList.valueOf(if (active) primaryColor else neutralColor)
            button.setTextColor(if (active) onPrimaryColor else neutralTextColor)
            button.iconTint = ColorStateList.valueOf(if (active) onPrimaryColor else neutralTextColor)
            button.strokeWidth = if (active) 0 else 1
        }
    }
}
