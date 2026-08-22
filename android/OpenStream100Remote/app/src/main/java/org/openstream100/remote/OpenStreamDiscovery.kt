package org.openstream100.remote

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import java.net.Inet6Address

data class DiscoveredServer(
    val name: String,
    val address: String,
    val fingerprint: String,
)

@Suppress("DEPRECATION")
class OpenStreamDiscovery(
    context: Context,
    private val onServer: (DiscoveredServer) -> Unit,
) : NsdManager.DiscoveryListener {
    private val applicationContext = context.applicationContext
    private val nsdManager = applicationContext.getSystemService(NsdManager::class.java)
    private val wifiManager = applicationContext.getSystemService(WifiManager::class.java)
    private val resolving = mutableSetOf<String>()
    private var multicastLock: WifiManager.MulticastLock? = null
    private var running = false

    fun start() {
        if (running) return
        multicastLock = wifiManager?.createMulticastLock("openstream100-discovery")?.apply {
            setReferenceCounted(false)
            acquire()
        }
        running = true
        nsdManager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, this)
    }

    fun stop() {
        if (running) {
            runCatching { nsdManager.stopServiceDiscovery(this) }
            running = false
        }
        multicastLock?.let { if (it.isHeld) it.release() }
        multicastLock = null
        resolving.clear()
    }

    override fun onDiscoveryStarted(serviceType: String) = Unit
    override fun onDiscoveryStopped(serviceType: String) { running = false }
    override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) { stop() }
    override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) { running = false }
    override fun onServiceLost(serviceInfo: NsdServiceInfo) {
        resolving.remove(serviceInfo.serviceName)
    }

    override fun onServiceFound(serviceInfo: NsdServiceInfo) {
        if (!serviceInfo.serviceType.startsWith("_openstream100._tcp")) return
        if (!resolving.add(serviceInfo.serviceName)) return
        nsdManager.resolveService(
            serviceInfo,
            object : NsdManager.ResolveListener {
                override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                    resolving.remove(info.serviceName)
                }

                override fun onServiceResolved(info: NsdServiceInfo) {
                    resolving.remove(info.serviceName)
                    val advertisedServer = info.attributes["server"]
                        ?.toString(Charsets.UTF_8)
                        ?.takeIf { it.isNotBlank() }
                    val addresses = if (Build.VERSION.SDK_INT >= 34) {
                        info.hostAddresses
                    } else {
                        listOfNotNull(info.host)
                    }
                    val host = addresses.firstOrNull { it !is Inet6Address }
                        ?: addresses.firstOrNull()
                        ?: return
                    val hostText = host.hostAddress?.substringBefore('%') ?: return
                    val formattedHost = if (host is Inet6Address) "[$hostText]" else hostText
                    val fingerprint = info.attributes["fingerprint"]
                        ?.toString(Charsets.UTF_8)
                        .orEmpty()
                    onServer(
                        DiscoveredServer(
                            name = info.serviceName,
                            address = advertisedServer ?: "$formattedHost:${info.port}",
                            fingerprint = fingerprint,
                        )
                    )
                }
            },
        )
    }

    companion object {
        private const val SERVICE_TYPE = "_openstream100._tcp."
    }
}
