package com.twoman.android

import java.net.Inet4Address
import java.net.NetworkInterface

object LanShareInfo {
    fun currentIpv4Address(): String? {
        val interfaces = NetworkInterface.getNetworkInterfaces() ?: return null
        while (interfaces.hasMoreElements()) {
            val networkInterface = interfaces.nextElement()
            if (!networkInterface.isUp || networkInterface.isLoopback) {
                continue
            }
            val addresses = networkInterface.inetAddresses
            while (addresses.hasMoreElements()) {
                val address = addresses.nextElement()
                if (address !is Inet4Address || address.isLoopbackAddress) {
                    continue
                }
                val hostAddress = address.hostAddress ?: continue
                if (hostAddress.startsWith("169.254.")) {
                    continue
                }
                return hostAddress
            }
        }
        return null
    }

    fun displayAddress(port: Int): String? {
        val host = currentIpv4Address() ?: return null
        return "$host:$port"
    }
}
