/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// wifi_mgr for firmware whose enclosing project already runs Wi-Fi
// (CONFIG_HOMEHUB_WIFI_EXTERNAL). The SDK never starts, joins, scans or leaves
// a network here: it reads the connection through the provider the project
// registers, and pairing offers that connection as the only network, the way
// the Linux gadget pairs.

#include "wifi_mgr.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define CONNECT_POLL_MS 100
// Shown for the current network when the provider has no reading.
#define FALLBACK_RSSI (-40)

static const char *TAG = "link.wifi";
static const wifi_provider_t *s_provider;

void wifi_mgr_set_provider(const wifi_provider_t *provider) {
    s_provider = provider;
}

// The project created the event loop, netif and driver before app_run().
void wifi_mgr_init(void) {
    if (!s_provider) {
        ESP_LOGE(TAG, "no Wi-Fi provider registered; staying offline");
    }
}

esp_netif_t *wifi_mgr_get_netif(void) {
    return s_provider && s_provider->get_netif ? s_provider->get_netif() : NULL;
}

bool wifi_mgr_is_connected(void) {
    return s_provider && s_provider->is_connected && s_provider->is_connected();
}

bool wifi_mgr_ap_active(void) {
    return s_provider && s_provider->ap_active && s_provider->ap_active();
}

bool wifi_mgr_current_ssid(char *out, size_t len) {
    if (!out || len == 0) return false;
    out[0] = '\0';
    if (!wifi_mgr_is_connected() || !s_provider->get_ssid
        || !s_provider->get_ssid(out, len)) {
        out[0] = '\0';
        return false;
    }
    out[len - 1] = '\0';
    return out[0] != '\0';
}

// Never joins: waits up to timeout_ms for the project's own connection.
bool wifi_mgr_connect(const char *ssid, const char *password, int timeout_ms) {
    (void)ssid;
    (void)password;
    for (int waited = 0; !wifi_mgr_is_connected(); waited += CONNECT_POLL_MS) {
        if (waited >= timeout_ms) return false;
        vTaskDelay(pdMS_TO_TICKS(CONNECT_POLL_MS));
    }
    return true;
}

bool wifi_mgr_seed_channel_hint(const char *ssid, uint8_t channel) {
    (void)ssid;
    (void)channel;
    return false;
}

bool wifi_mgr_get_connected_channel(uint8_t *channel) {
    (void)channel;
    return false;
}

void wifi_mgr_disconnect(void) {
}

bool wifi_mgr_joining(char *ssid, size_t cap) {
    (void)ssid;
    (void)cap;
    return false;
}

// The current connection, shown as open so the app sends an empty password;
// offline, there is no network to offer.
static int current_network(wifi_scan_entry_t *out, int max_entries) {
    if (!out || max_entries < 1) return 0;
    memset(out, 0, sizeof(*out));
    if (!wifi_mgr_current_ssid(out->ssid, sizeof(out->ssid))) return 0;
    int rssi = s_provider->get_rssi ? s_provider->get_rssi() : 0;
    out->rssi = (int8_t)(rssi < 0 && rssi >= -127 ? rssi : FALLBACK_RSSI);
    out->auth_mode = 0;
    return 1;
}

int wifi_mgr_scan(wifi_scan_entry_t *out, int max_entries, uint8_t channel,
                  const char *target_ssid) {
    (void)channel;
    (void)target_ssid;
    return current_network(out, max_entries);
}

int wifi_mgr_scan_and_cache(void) {
    wifi_scan_entry_t entry;
    return current_network(&entry, 1);
}

int wifi_mgr_scan_and_merge_cache(void) {
    wifi_scan_entry_t entry;
    return current_network(&entry, 1);
}

int wifi_mgr_get_cached_scan(wifi_scan_entry_t *out, int max_entries) {
    return current_network(out, max_entries);
}

// Every network counts as broadcasting its name; the SDK saves none anyway.
bool wifi_mgr_cached_scan_has(const char *ssid) {
    (void)ssid;
    return true;
}
