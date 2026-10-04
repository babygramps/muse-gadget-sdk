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

// Commands the enclosing project adds with noise_ctrl_add_commands(). The
// table is filled before app_run() and only read afterwards, by the session
// task, so it needs no lock. Calling noise_ctrl_add_commands() after app_run()
// is unsupported: a session already registered would not advertise the new
// commands until it reconnects.

#include "command_registry.h"

#include <string.h>

#include "cJSON.h"
#include "esp_log.h"

// Every command the SDK itself handles or advertises, in any build.
static const char *const SDK_COMMANDS[] = {
    "bug.report",
    "camera.capture",
    "device.discover",
    "device.health",
    "device.list_vms",
    "device.ota",
    "device.reset_vm",
    "device.set_vm",
    "device.unpair",
    "display.draw_url",
    "display.show_animation",
    "sensors.read",
    "voice.configure",
};

static noise_ctrl_command_def_t s_commands[NOISE_CTRL_MAX_COMMANDS];
static size_t s_count;

static bool valid_name(const char *name) {
    size_t len = name ? strlen(name) : 0;
    if (len == 0 || len > NOISE_CTRL_COMMAND_NAME_MAX) return false;
    for (const char *c = name; *c; c++) {
        bool ok = (*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z')
                  || (*c >= '0' && *c <= '9') || *c == '.' || *c == '_' || *c == '-';
        if (!ok) return false;
    }
    return true;
}

static bool sdk_command(const char *name) {
    for (size_t i = 0; i < sizeof(SDK_COMMANDS) / sizeof(SDK_COMMANDS[0]); i++) {
        if (strcmp(SDK_COMMANDS[i], name) == 0) return true;
    }
    return false;
}

static bool valid_param_group(const cJSON *group) {
    if (!cJSON_IsObject(group)) return false;
    const cJSON *param;
    cJSON_ArrayForEach(param, group) {
        if (!cJSON_IsObject(param)) return false;
        const cJSON *type = cJSON_GetObjectItem(param, "type");
        if (!cJSON_IsString(type) || !type->valuestring) return false;
        const char *t = type->valuestring;
        if (strcmp(t, "string") != 0 && strcmp(t, "integer") != 0
            && strcmp(t, "number") != 0 && strcmp(t, "boolean") != 0) {
            return false;
        }
        const cJSON *description = cJSON_GetObjectItem(param, "description");
        if (description && !cJSON_IsString(description)) return false;
    }
    return true;
}

static bool valid_params(const char *params_json) {
    if (!params_json) return true;
    cJSON *root = cJSON_ParseWithOpts(params_json, NULL, true);
    bool ok = cJSON_IsObject(root);
    const cJSON *group;
    if (ok) {
        cJSON_ArrayForEach(group, root) {
            if (!group->string
                || (strcmp(group->string, "required") != 0
                    && strcmp(group->string, "optional") != 0)
                || !valid_param_group(group)) {
                ok = false;
                break;
            }
        }
    }
    cJSON_Delete(root);
    return ok;
}

const noise_ctrl_command_def_t *command_registry_find(const char *name) {
    if (!name) return NULL;
    for (size_t i = 0; i < s_count; i++) {
        if (strcmp(s_commands[i].name, name) == 0) return &s_commands[i];
    }
    return NULL;
}

static bool valid_command(const noise_ctrl_command_def_t *defs, size_t index) {
    const noise_ctrl_command_def_t *def = &defs[index];
    if (!valid_name(def->name) || !def->description || !def->description[0]
        || !def->handler || !valid_params(def->params_json)) {
        return false;
    }
    if (sdk_command(def->name) || command_registry_find(def->name)) return false;
    for (size_t i = 0; i < index; i++) {
        if (strcmp(defs[i].name, def->name) == 0) return false;
    }
    return true;
}

bool noise_ctrl_add_commands(const noise_ctrl_command_def_t *defs, size_t count) {
    if (count == 0) return true;  // a conditionally built, empty batch
    if (!defs || count > NOISE_CTRL_MAX_COMMANDS - s_count) {
        ESP_LOGE("link.commands", "command table full");
        return false;
    }
    for (size_t i = 0; i < count; i++) {
        if (!valid_command(defs, i)) {
            ESP_LOGE("link.commands", "command %s rejected",
                     defs[i].name ? defs[i].name : "(null)");
            return false;
        }
    }
    memcpy(&s_commands[s_count], defs, count * sizeof(*defs));
    s_count += count;
    return true;
}

void command_registry_append(cJSON *commands) {
    for (size_t i = 0; i < s_count; i++) {
        const noise_ctrl_command_def_t *def = &s_commands[i];
        cJSON *command = def->params_json
            ? cJSON_ParseWithLength(def->params_json, strlen(def->params_json))
            : cJSON_CreateObject();
        if (!command) {
            // Out of memory. The command stays invocable but is not advertised.
            ESP_LOGE("link.commands", "command %s not advertised", def->name);
            continue;
        }
        if (!cJSON_GetObjectItem(command, "required")) {
            cJSON_AddItemToObject(command, "required", cJSON_CreateObject());
        }
        if (!cJSON_GetObjectItem(command, "optional")) {
            cJSON_AddItemToObject(command, "optional", cJSON_CreateObject());
        }
        cJSON_AddStringToObject(command, "description", def->description);
        if (def->timeout_ms) {
            cJSON_AddNumberToObject(command, "timeout_ms", def->timeout_ms);
        }
        cJSON_AddItemToObject(commands, def->name, command);
    }
}
