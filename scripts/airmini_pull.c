/* airmini_pull.c — 連 ResMed AirMini（Bluetooth Classic RFCOMM ch5），SRP 配對後
 * 拉取 version/datetime/metrics/settings/history + 一分鐘時序與呼吸事件。
 * 依 libairmini 公共 API（https://github.com/psychoticbeef/libairmini）。
 * 建置/執行/狀態說明：見 references/airmini-access.md。
 * 已驗證：RFCOMM ch5 連線。待端到端驗證：配對→資料拉取（Pull/*.json）。
 */
#include "airmini/airmini.h"
#include "airmini/crypto.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <sys/socket.h>

#ifndef AF_BLUETOOTH
#define AF_BLUETOOTH 31
#endif
#ifndef BTPROTO_RFCOMM
#define BTPROTO_RFCOMM 3
#endif
/* Linux BlueZ sockaddr_rc，避免依賴 <bluetooth/rfcomm.h> */
struct sockaddr_rc { sa_family_t rc_family; unsigned char rc_bdaddr[6]; unsigned char rc_channel; };

static int fd = -1;
static int stop = 0;
static int pending = 0;
static time_t last_activity;
static airmini_t *g_am;

static int host_send(void *u, const uint8_t *data, size_t len) {
    (void)u;
    size_t off = 0;
    while (off < len) {
        ssize_t n = write(fd, data + off, len - off);
        if (n <= 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                struct pollfd p = {.fd = fd, .events = POLLOUT};
                poll(&p, 1, 3000);
                continue;
            }
            return -1;
        }
        off += (size_t)n;
    }
    return 0;
}

static void out(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    vprintf(fmt, ap); va_end(ap);
    fflush(stdout);
}

static void out_file(const char *tag, const char *json) {
    char path[256];
    snprintf(path, sizeof path, "pull/%s.json", tag);
    FILE *f = fopen(path, "a");
    if (f) { fputs(json, f); fputc('\n', f); fclose(f); }
}

static void issue_logged_batch(void);

static void on_result(void *user, airmini_status st, const char *result, const char *error) {
    const char *tag = (const char *)user;
    pending--;
    last_activity = time(NULL);

    if (st == AIRMINI_OK) {
        out("[ok:%s] %.200s%s\n", tag, result, strlen(result) > 200 ? "…" : "");
        out_file(tag, result ? result : "{}");
    } else if (st == AIRMINI_ERR_DEVICE) {
        out("[device-err:%s] %s\n", tag, error);
        out_file(tag, error ? error : "{}");
    } else {
        out("[%s] %s\n", tag, airmini_strerror(st));
        return;
    }

    if (st != AIRMINI_OK)
        return;

    if (strcmp(tag, "pair") == 0) {
        /* result: {"masterPairKey":"<64hex>","sessionKey":"<64hex>"} */
        const char *k = strstr(result ? result : "", "\"masterPairKey\"");
        if (!k) { out("pair 回應中沒有 masterPairKey\n"); stop = 1; return; }
        const char *q1 = strchr(k + 15, '"');
        const char *q2 = q1 ? strchr(q1 + 1, '"') : NULL;
        if (!q1 || !q2) { stop = 1; return; }
        char key[65];
        size_t n = (size_t)(q2 - q1 - 1); if (n > 64) n = 64;
        memcpy(key, q1 + 1, n); key[n] = 0;
        out("[chain] masterPairKey=%s…\n", key);
        out_file("master_pair_key", key);   /* 持久化：下次 open_session 免 PIN */
        if (airmini_open_session(g_am, key, on_result, "session") != AIRMINI_OK)
            stop = 1;
        else
            pending++;
    } else if (strcmp(tag, "session") == 0) {
        if (airmini_get_version(g_am, on_result, "version") == AIRMINI_OK) pending++;
        if (airmini_get_datetime(g_am, on_result, "datetime") == AIRMINI_OK) pending++;
        if (airmini_get_metrics(g_am, on_result, "metrics") == AIRMINI_OK) pending++;
        if (airmini_get_settings(g_am, on_result, "settings") == AIRMINI_OK) pending++;
        if (airmini_get_history(g_am, AIRMINI_TIME_EPOCH, on_result, "history") == AIRMINI_OK) pending++;
        out("[chain] 已發出 version/datetime/metrics/settings/history\n");
        {
            static const char *const sigs[] = {
                AIRMINI_SIG_LEAK,
                AIRMINI_SIG_INSP_PRESSURE,
                AIRMINI_SIG_THERAPY_STATUS,
                AIRMINI_SIG_RESPIRATORY_EVENT,
                AIRMINI_SIG_ACTIVITY_FREQUENT,
                AIRMINI_SIG_ACTIVITY_SPORADIC,
            };
            size_t n = sizeof sigs / sizeof sigs[0];
            if (airmini_get_logged_data(g_am, sigs, n, AIRMINI_TIME_EPOCH,
                                        on_result, "logged") == AIRMINI_OK)
                pending++;
            out("[chain] 已發出 GetLoggedData（%zu 訊號，自 %s）\n", n, AIRMINI_TIME_EPOCH);
        }
    }
}

static void on_notify(void *u, const char *method, const char *json) {
    (void)u;
    last_activity = time(NULL);
    out("[notify] %s (%zu B)\n", method, strlen(json));
    if (strstr(method, "LoggedData")) out_file("logged_data", json);
    else if (strstr(method, "Stream")) out_file("stream_data", json);
}

int main(int argc, char **argv) {
    const char *addr = argc > 1 ? argv[1] : "<BT_ADDR>";
    const char *pin  = argc > 2 ? argv[2] : getenv("AIRMINI_PIN", "");

    struct sockaddr_rc sa = {0};
    sa.rc_family = AF_BLUETOOTH;
    { unsigned b[6];
      sscanf(addr, "%2x:%2x:%2x:%2x:%2x:%2x",
             &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]);
      for (int i = 0; i < 6; i++) sa.rc_bdaddr[i] = (unsigned char)b[i]; }
    sa.rc_channel = 5;

    fd = socket(AF_BLUETOOTH, SOCK_STREAM, BTPROTO_RFCOMM);
    if (fd < 0) { perror("socket"); return 1; }
    int fl = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, fl | O_NONBLOCK);
    if (connect(fd, (struct sockaddr *)&sa, sizeof sa) < 0 && errno != EINPROGRESS) {
        perror("connect"); return 1;
    }
    {
        struct pollfd p = {.fd = fd, .events = POLLOUT};
        poll(&p, 1, 8000);
        int err = 0; socklen_t el = sizeof err;
        getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el);
        if (err) { out("連線失敗 errno=%d %s\n", err, strerror(err)); return 1; }
    }
    out("RFCOMM 已連線 %s ch5\n", addr);

    mkdir("pull", 0755);
    airmini_config cfg = {0};
    cfg.send = host_send;
    cfg.crypto = airmini_crypto_openssl();
    g_am = airmini_create(&cfg);
    if (!g_am) { out("airmini_create 失敗（crypto 後端？）\n"); return 1; }
    airmini_set_notify(g_am, on_notify, NULL);

    last_activity = time(NULL);
    out("→ pair(PIN=%s)\n", pin);
    if (airmini_pair(g_am, pin, on_result, "pair") != AIRMINI_OK) {
        out("pair 呼叫失敗\n");
        return 1;
    }
    pending = 1;

    struct pollfd pf = {.fd = fd, .events = POLLIN};
    uint8_t buf[2048];
    time_t t0 = time(NULL);
    while (!stop) {
        int r = poll(&pf, 1, 200);
        if (r > 0 && (pf.revents & POLLIN)) {
            ssize_t nread;
            last_activity = time(NULL);
            while ((nread = read(fd, buf, sizeof buf)) > 0)
                airmini_feed(g_am, buf, (size_t)nread);
            if (nread < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
                out("read 錯誤：%s\n", strerror(errno));
                break;
            }
        } else if (r < 0) {
            out("poll 錯誤 %s\n", strerror(errno));
            break;
        }
        if (pending == 0 && time(NULL) - last_activity > 12) break;
        if (time(NULL) - t0 > 300) { out("總時限逾時\n"); break; }
    }
    out("— 連線結束（pending=%d）\n", pending);
    close(fd);
    return 0;
}
