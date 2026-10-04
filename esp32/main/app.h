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

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// With CONFIG_HOMEHUB_NVS_FAIL_DISABLES_LINK, app_run() returns early when NVS
// cannot be initialized; every other app_* call below is then a no-op (false,
// or APP_WIFI_FAILED), and app_get_gadget_status() reports an unpaired gadget.
void app_run(void);

// How joining one of the saved Wi-Fi networks went.
typedef enum {
    APP_WIFI_JOINED,
    APP_WIFI_NOT_NEARBY,    // no saved network showed up in a scan
    APP_WIFI_FAILED,        // one did, but joining it failed
    APP_WIFI_BUSY,          // another setup operation had the radio
} app_wifi_join_t;

// ---- For a project that builds this SDK into its own firmware ----
// All are safe from any task.

// Decides when the SDK may reboot or reset setup: it returns false while the
// project is busy. NULL (the default) means always idle, which is upstream
// behavior. While busy, a server or host unpair and a revoked device token
// clear the pairing keys and close the session at once, and the reboot that
// reopens setup waits, checked once a second; the 5 s button hold is refused
// and the status LED blinks yellow three times.
void app_set_idle_check(bool (*idle)(void));

typedef struct {
    bool paired;            // setup_complete and tokens present
    bool connected;         // Muse control session up
    bool setup_window_open; // advertising for pairing right now
} app_gadget_status_t;
void app_get_gadget_status(app_gadget_status_t *out);

// Opens the pairing window like a short button press, without confirming a
// pending pairing (that still takes the button); no-op when paired.
void app_open_setup_window(void);

// Guarded unpair: clears the SDK's pairing keys and closes the session now;
// the reboot that reopens setup waits until the idle check passes. No-op when
// not paired.
void app_request_unpair(void);

#if CONFIG_MUSE_ENABLED
// Muse (muse_glue.c) drives setup through these; all are safe from any task.

// Remembers a Wi-Fi network first among the saved ones (hidden: it doesn't
// broadcast its name), or with an empty ssid forgets them all.
void app_wifi_set_credentials(const char *ssid, const char *password, bool hidden);
// Forgets one saved network; the next takes its place if it was first.
bool app_wifi_forget(const char *ssid);

// Joins a saved network under the setup operation gate: scans, then tries the
// saved networks that showed up, strongest first, and probes for the hidden
// ones. first_only joins just the first saved network, without scanning (the
// one just chosen). If paired, the heartbeat then reaches the VM (retrying
// until the session runs). Nothing here wakes the screen.
app_wifi_join_t app_wifi_join_saved(int timeout_ms, bool first_only);
// When the last join that scanned, this one or the one at boot, found no saved
// network nearby (esp_timer_get_time()); 0 if it found one.
int64_t app_wifi_none_nearby_at(void);
// Drops the VM session and leaves Wi-Fi, so the radio can sleep;
// app_wifi_join_saved() brings both back. Returns false when busy.
bool app_wifi_nap(void);
// Starts the BLE server if needed and keeps it advertising (or not) for Muse's
// phone-setup service once setup is complete.
void app_ble_companion_set(bool advertise);
// A talk-button press. Returns true when it confirmed a pending pairing.
bool app_confirm_pairing_press(void);
// Full setup reset and reboot, from a worker task.
void app_reset_setup_async(void);
#endif

#if CONFIG_MUSE_ENABLED || CONFIG_HOMEHUB_VOICE
// Looks up a VM credential from the paired account (want_vm: a VM id, or empty
// for the preferred VM). *vm_token is heap; free() it.
bool app_hatch_vm_credentials(const char *want_vm, char *vm_id, size_t id_cap,
                              char *vm_name, size_t name_cap, char **vm_token);
#endif

#ifdef __cplusplus
}
#endif
