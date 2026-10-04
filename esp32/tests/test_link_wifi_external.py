# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_C = ROOT / "main" / "app.c"

# Type-checks log arguments without printing them.
ESP_LOG_H = (
    "#include <stdio.h>\n"
    "#define ESP_LOGI(tag, ...) ((void)(tag), (void)sizeof(printf(__VA_ARGS__)))\n"
    "#define ESP_LOGW ESP_LOGI\n#define ESP_LOGE ESP_LOGI\n"
)

PROVIDER_HARNESS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "wifi_mgr.h"

static int now_ms, connect_at_ms = -1, rssi = -55;
static bool ap;
static const char *ssid = "SmokerNet";
static esp_netif_t *const station = (esp_netif_t *)0x1234;

void vTaskDelay(int ticks) { now_ms += ticks; }
static bool is_connected(void) { return connect_at_ms >= 0 && now_ms >= connect_at_ms; }
static bool get_ssid(char *out, size_t len) { snprintf(out, len, "%s", ssid); return true; }
static int get_rssi(void) { return rssi; }
static bool ap_active(void) { return ap; }
static esp_netif_t *get_netif(void) { return station; }
static const wifi_provider_t provider = {
    is_connected, get_ssid, get_rssi, ap_active, get_netif,
};

int main(void) {
    wifi_scan_entry_t scan[4];
    char name[33];

    // No provider: offline, and connect gives up after its timeout.
    wifi_mgr_init();
    assert(!wifi_mgr_is_connected() && !wifi_mgr_ap_active());
    assert(wifi_mgr_get_netif() == NULL);
    assert(!wifi_mgr_connect("x", "y", 300) && now_ms == 300);
    assert(wifi_mgr_get_cached_scan(scan, 4) == 0);
    assert(!wifi_mgr_current_ssid(name, sizeof(name)) && name[0] == '\0');

    wifi_mgr_set_provider(&provider);
    assert(wifi_mgr_get_netif() == station);

    // Offline: nothing to offer, and connect waits for the project to join.
    now_ms = 0;
    connect_at_ms = 250;
    assert(wifi_mgr_scan_and_cache() == 0);
    assert(wifi_mgr_connect("ignored", "", 60000) && now_ms == 300);

    // Online: the one entry is the current network, shown as open.
    assert(wifi_mgr_connect("other", "pw", 0));
    assert(wifi_mgr_scan(scan, 4, 0, NULL) == 1);
    assert(wifi_mgr_scan_and_merge_cache() == 1);
    assert(wifi_mgr_get_cached_scan(scan, 4) == 1);
    assert(strcmp(scan[0].ssid, "SmokerNet") == 0);
    assert(scan[0].rssi == -55 && scan[0].auth_mode == 0);
    assert(wifi_mgr_cached_scan_has("anything"));
    assert(wifi_mgr_current_ssid(name, sizeof(name)) && strcmp(name, "SmokerNet") == 0);

    // No reading falls back to a strong signal; long names are cut to fit.
    rssi = 0;
    ssid = "a-network-name-longer-than-thirty-two-bytes";
    assert(wifi_mgr_get_cached_scan(scan, 4) == 1);
    assert(scan[0].rssi == -40 && strlen(scan[0].ssid) == 32);

    // The radio stays the project's.
    uint8_t channel = 0;
    assert(!wifi_mgr_get_connected_channel(&channel) && channel == 0);
    assert(!wifi_mgr_seed_channel_hint("SmokerNet", 6));
    assert(!wifi_mgr_joining(name, sizeof(name)));
    wifi_mgr_disconnect();
    assert(wifi_mgr_is_connected());

    ap = true;
    assert(wifi_mgr_ap_active());
    puts("wifi_mgr_external: PASS");
    return 0;
}
"""

KEEPER_FAKES = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "esp_log.h"
#define CONFIG_HOMEHUB_WIFI_EXTERNAL 1
static const char *TAG = "test";
#define LED_STATE_AUTH_OK 1
#define LED_STATE_ERROR 2
static int64_t now_us;
static bool online = true, setup_done = true, provisioned = true, running, gate_free = true;
static bool token_ok = true, vm_ok;
static int attempts, gate_takes, gate_gives;
static const char *wifi_shown;
static int64_t esp_timer_get_time(void) { return now_us; }
static bool wifi_mgr_is_connected(void) { return online; }
static bool wifi_mgr_current_ssid(char *out, size_t len) {
    (void)len;
    strcpy(out, "SmokerNet");
    return true;
}
static bool config_setup_complete(void) { return setup_done; }
static bool config_is_provisioned(void) { return provisioned; }
static bool noise_ctrl_is_running(void) { return running; }
static bool operation_gate_take(int timeout, const char *label) {
    (void)label;
    assert(timeout == 0);
    gate_takes++;
    return gate_free;
}
static void operation_gate_give(void) { gate_gives++; }
static void ui_set_wifi(const char *ssid) { wifi_shown = ssid; }
static void ui_set_status(const char *s) { (void)s; }
static void led_status_set_state(int state) { (void)state; }
static bool ensure_access_token_ready_with_gate_held(bool force, bool allow) {
    (void)force;
    (void)allow;
    return token_ok;
}
static bool connect_preferred_vm_with_gate_held(uint32_t generation) {
    assert(generation == 0);
    attempts++;
    return vm_ok;
}
"""

KEEPER_MAIN = r"""
int main(void) {
    // Offline, unpaired or already running: no attempt, and backoff resets.
    online = false;
    keep_vm_session_external();
    online = true;
    setup_done = false;
    keep_vm_session_external();
    setup_done = true;
    running = true;
    keep_vm_session_external();
    running = false;
    assert(attempts == 0 && gate_takes == 0);

    // Failures back off 5 s, 10 s, 20 s ... up to 60 s.
    keep_vm_session_external();
    assert(attempts == 1 && gate_gives == 1);
    assert(wifi_shown && strcmp(wifi_shown, "SmokerNet") == 0);
    now_us = 4999999;
    keep_vm_session_external();
    assert(attempts == 1);
    now_us = 5000000;
    keep_vm_session_external();
    assert(attempts == 2);
    now_us += 10000000;
    keep_vm_session_external();
    assert(attempts == 3);
    for (int i = 0; i < 4; i++) {
        now_us += 60000000;
        keep_vm_session_external();
    }
    assert(attempts == 7);
    now_us += 59999999;
    keep_vm_session_external();
    assert(attempts == 7);

    // A busy gate skips the tick without counting a failure.
    now_us += 1;
    gate_free = false;
    keep_vm_session_external();
    assert(attempts == 7 && gate_takes == gate_gives + 1);
    gate_free = true;

    // Success stops retrying; the running session takes over.
    vm_ok = true;
    keep_vm_session_external();
    assert(attempts == 8);
    running = true;
    now_us += 1;
    keep_vm_session_external();
    assert(attempts == 8);
    return 0;
}
"""


def function_source(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for end in range(brace, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                return source[start : end + 1]
    raise AssertionError(f"Unclosed function: {signature}")


class LinkWifiExternalTest(unittest.TestCase):
    def compile_and_run(self, tmp: Path, sources: list[Path], *flags: str) -> None:
        compiler = shlex.split(os.environ.get("CC", "cc"))
        if not compiler or shutil.which(compiler[0]) is None:
            self.skipTest("C compiler unavailable")
        (tmp / "esp_log.h").write_text(ESP_LOG_H)
        binary = tmp / "wifi-external"
        compiled = subprocess.run(
            [*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", *flags,
             "-I", str(tmp), "-I", str(ROOT / "tests/link_fakes"), "-I", str(ROOT / "main"),
             *map(str, sources), "-o", str(binary)],
            capture_output=True, text=True,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_provider_backs_the_whole_wifi_mgr_api(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            (tmp / "harness.c").write_text(PROVIDER_HARNESS)
            self.compile_and_run(
                tmp, [ROOT / "main/wifi_mgr_external.c", tmp / "harness.c"],
                "-DLINK_FAKE_CUSTOM_TASKS=1",
            )

    def test_heartbeat_reaches_the_vm_once_the_project_is_online(self) -> None:
        app = APP_C.read_text()
        defines = "".join(
            line + "\n" for line in app.splitlines()
            if line.startswith("#define EXTERNAL_VM_RETRY_")
        )
        keeper = function_source(app, "static void keep_vm_session_external(")
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            source = tmp / "keeper.c"
            source.write_text(KEEPER_FAKES + defines + keeper + KEEPER_MAIN)
            self.compile_and_run(tmp, [source])

        app_run = function_source(app, "void app_run(")
        self.assertRegex(
            app_run,
            r"#if CONFIG_HOMEHUB_WIFI_EXTERNAL\s+keep_vm_session_external\(\);\s+#endif",
        )

    def test_setup_keeps_none_of_the_sdk_wifi_settings(self) -> None:
        app = APP_C.read_text()
        remember = function_source(app, "static void remember_joined_wifi(")
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            source = tmp / "remember.c"
            source.write_text(
                "#include <assert.h>\n#include <stdbool.h>\n"
                "static int saves;\n"
                "bool wifi_known_remember(const char *s, const char *p, bool h) {\n"
                "    (void)s; (void)p; (void)h; saves++; return true; }\n"
                "bool wifi_mgr_cached_scan_has(const char *s) { (void)s; return true; }\n"
                + remember
                + "\nint main(void) {\n"
                "    remember_joined_wifi(\"SmokerNet\", \"\");\n"
                "    assert(saves == 0);\n"
                "    return 0;\n}\n"
            )
            self.compile_and_run(tmp, [source], "-DCONFIG_HOMEHUB_WIFI_EXTERNAL=1")

        provision = function_source(app, "static void on_provision(")
        self.assertIn("wifi_mgr_connect(ssid, password, WIFI_CONNECT_TIMEOUT_MS)", provision)
        self.assertIn("wifi_mgr_current_ssid(current_ssid, sizeof(current_ssid))", provision)

    def test_external_mode_swaps_the_wifi_source_and_accepts_open_networks(self) -> None:
        cmake = (ROOT / "main/CMakeLists.txt").read_text()
        self.assertRegex(cmake, r'if\(CONFIG_HOMEHUB_WIFI_EXTERNAL\)\s*'
                                r'list\(APPEND GADGET_SRCS "wifi_mgr_external.c"\)\s*else\(\)\s*'
                                r'list\(APPEND GADGET_SRCS "wifi_mgr.c"\)')
        sources = cmake[cmake.index("set(GADGET_SRCS"):cmake.index(")", cmake.index("set(GADGET_SRCS"))]
        self.assertNotIn('"wifi_mgr.c"', sources)

        ble = (ROOT / "main/ble_server.c").read_text()
        self.assertIn("|| (!*p->valuestring && !PROVISION_ALLOWS_EMPTY_PASSWORD)", ble)
        self.assertRegex(ble, r"#if CONFIG_HOMEHUB_WIFI_EXTERNAL\s+"
                              r"#define PROVISION_ALLOWS_EMPTY_PASSWORD 1\s+#else\s+"
                              r"#define PROVISION_ALLOWS_EMPTY_PASSWORD 0\s+#endif")

        kconfig = (ROOT / "main/Kconfig.projbuild").read_text()
        option = kconfig[kconfig.index("config HOMEHUB_WIFI_EXTERNAL"):]
        self.assertRegex(option, r"depends on !MUSE_ENABLED\s+default n")


if __name__ == "__main__":
    unittest.main()
