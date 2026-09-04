"""Generate a demo event log: six weeks of debugging loops.

The arc is deliberate. The first two weeks are pre-protocol — long loops,
pings ignored, actions climbing while nothing gets ruled out. The last two
weeks are disciplined. `loop stats` reads the difference off the log, which
is the only claim the tool actually makes.

This writes DEMO DATA. It defaults to ~/.loop-demo and refuses to touch a
non-empty log without --force, because the real log is not reproducible.

    python tools/demo_seed.py
    LOOP_HOME=~/.loop-demo .venv/bin/loop stats
    LOOP_HOME=~/.loop-demo .venv/bin/python -m loop.gui

Timings are relative to --now, so re-run it right before recording: the
live loop is pinned so its next check-in is --lead minutes out, and it
ages in real time otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

DAY = 86400.0
MIN = 60.0

# Each loop is (open-offset in days before now, spec). `tl` is a timeline of
# (minute-since-open, op, *args). Ops mirror the event vocabulary:
#   try    action, because
#   hyp    text                     — a hypothesis that occurred to you later
#   kill   hyp_id                   — ruled out via `loop hyp kill`
#   ping   smaller, hyp_id|None     — the 20-minute check-in, answered
#   miss                            — the check-in fired and you ignored it
#   cp     kind, decision, on_track, *extra
#   cpmiss kind
#   pause  reason  /  resume
#   close  what_was_it, giveaway, five_min_path
#   abandon

LOOPS: list[tuple[float, dict]] = [
    # ---------------------------------------------------------------- pre-protocol
    (41.0, dict(
        q="staging deploy fails at startup",
        stop="service comes up clean twice in a row",
        budget_m=45, interval_m=20,
        hyps=["cert expiry", "env var missing in the new task def", "pg conn pool exhausted"],
        tl=[
            (4, "try", "restart the pg container", "the pool is exhausted and a restart clears it"),
            (9, "try", "redeploy the previous task definition", "the last deploy introduced it"),
            (14, "try", "bump the pool size to 40", "40 is more than 20"),
            (20, "miss"),
            (23, "try", "restart the pg container again", "the first restart did not take"),
            (31, "try", "tail the container logs with -f", "something will show up eventually"),
            (40, "ping", False, None),
            (44, "try", "roll the whole ECS service", "a clean slate will fix it"),
            (52, "try", "exec into the container and psql by hand", "I want to see it fail live"),
            (60, "miss"),
            (71, "kill", 3),
            (78, "try", "openssl s_client against the RDS endpoint", "psql by hand said certificate verify failed"),
            (86, "kill", 2),
            (103, "close",
             "the RDS CA bundle rotated and the new base image dropped the old root",
             "psql by hand printed 'certificate verify failed' in the first ten seconds",
             "read the actual startup error before touching anything — it was in the first log line"),
        ],
    )),
    (39.8, dict(
        q="test_checkout_applies_discount fails about 1 run in 20, CI only",
        stop="200 consecutive green runs on CI",
        budget_m=30, interval_m=20,
        hyps=["test ordering dependency", "frozen clock leaking between tests", "CI runner is slower and something times out"],
        tl=[
            (5, "try", "run the test 50x locally", "it will reproduce if I run it enough"),
            (12, "try", "run it 200x locally", "50 was not enough"),
            (20, "miss"),
            (24, "try", "add -p no:randomly", "ordering is the usual suspect"),
            (33, "try", "run 200x locally again with the flag", "that should settle it"),
            (40, "ping", False, None),
            (48, "try", "re-run the CI job", "maybe it was a blip"),
            (57, "try", "re-run the CI job again", "twice is data"),
            (60, "miss"),
            (69, "try", "add a print to the assertion", "I will see the value when it fails"),
            (95, "abandon"),
        ],
    )),
    (38.0, dict(
        q="prod p99 doubled after Tuesday's release",
        stop="p99 back under 400ms for a full hour",
        budget_m=60, interval_m=20,
        hyps=["the new N+1 in the orders serializer", "cache hit rate dropped", "the instance type changed"],
        tl=[
            (6, "try", "stare at the Grafana panel", "the shape will tell me something"),
            (13, "try", "compare last week's dashboard side by side", "the diff will be visible"),
            (20, "miss"),
            (25, "try", "restart the api pods", "it might just be a warm-up artifact"),
            (34, "try", "scale up to 8 replicas", "if it is load, more replicas fixes it"),
            (40, "ping", False, None),
            (48, "try", "bump the cache TTL to 10 minutes", "cache hit rate is the second hypothesis"),
            (45, "cp", "p75", "continue", True),
            (60, "cp", "p100", "extend", False, 120, "I have not isolated anything yet; I have been changing settings"),
            (68, "miss"),
            (74, "try", "pull the slow query log for the window", "I should look at what is actually slow"),
            (82, "kill", 2),
            (89, "kill", 3),
            (97, "try", "EXPLAIN the orders list query", "the slow log points at one statement"),
            (118, "close",
             "N+1 on order_items — the serializer lost its prefetch_related in the refactor",
             "the slow query log had the same statement 340 times per request",
             "open the slow query log first; it named the query in under a minute"),
        ],
    )),
    (36.5, dict(
        q="docker build cache misses on every CI run",
        stop="two consecutive CI builds under 3 minutes",
        budget_m=30, interval_m=20,
        hyps=["COPY . . too early in the Dockerfile", "the runner has no persistent cache", "buildkit is disabled"],
        tl=[
            (4, "try", "add --cache-from to the build", "the runner is not being told where the cache is"),
            (11, "try", "enable BUILDKIT=1", "buildkit caches better"),
            (18, "try", "push a cache image to ECR", "then --cache-from has something to read"),
            (20, "miss"),
            (27, "try", "re-run the build", "the cache should be warm now"),
            (36, "try", "re-run the build once more", "the second run is the real test"),
            (40, "ping", False, None),
            (49, "try", "print the layer hashes for both runs", "I want to see where they diverge"),
            (58, "kill", 3),
            (64, "kill", 2),
            (88, "close",
             "COPY . . sat above the pip install, so every commit invalidated the dependency layer",
             "the layer hash diff showed the break at the COPY, not at the install",
             "diff the layer hashes between two builds — it points at the exact line"),
        ],
    )),
    (34.2, dict(
        q="webhook signature verification fails for exactly one partner",
        stop="that partner's webhooks verify for 24h with no manual retries",
        budget_m=45, interval_m=20,
        hyps=["they sign the raw body, we verify the parsed body", "clock skew on their side", "wrong shared secret in our vault"],
        tl=[
            (7, "try", "rotate the shared secret with them", "the secret is the most common cause"),
            (16, "try", "log the computed and received signatures", "seeing both will make it obvious"),
            (20, "miss"),
            (26, "try", "ask them to re-send a sample payload", "I need a fresh one to compare"),
            (33, "kill", 3),
            (39, "try", "hexdump our raw body next to theirs", "the signatures differ so the inputs differ"),
            (40, "ping", True, 2),
            (47, "try", "verify against request.body instead of the parsed dict", "the hexdump showed a trailing newline we were stripping"),
            (62, "close",
             "our framework strips a trailing newline before we hash; they sign the byte-exact body",
             "hexdump showed one extra 0x0a on their side",
             "hexdump both payloads at minute one instead of rotating secrets"),
        ],
    )),
    (32.0, dict(
        q="memory climbs about 40MB/hour in the websocket server",
        stop="RSS flat for 6 hours under normal load",
        budget_m=60, interval_m=20,
        hyps=["connections never removed from the registry dict", "a logging handler holding references", "it is just glibc not returning pages"],
        tl=[
            (8, "try", "add a periodic gc.collect()", "if gc clears it, it is not a real leak"),
            (17, "try", "graph RSS against connection count", "they should correlate"),
            (20, "miss"),
            (26, "try", "restart the pod nightly as a stopgap", "buy time while I think"),
            (35, "try", "add a Prometheus gauge for the registry size", "I want the number visible"),
            (40, "ping", False, None),
            (44, "try", "read the registry cleanup code again", "I have read it twice, maybe a third time"),
            (53, "try", "bump the pod memory limit to 2Gi", "it will at least stop the OOMKills"),
            (60, "miss"),
            (68, "try", "run tracemalloc in staging", "staging does not reproduce it but worth a try"),
            (150, "abandon"),
        ],
    )),
    (30.4, dict(
        q="why is the stack trace empty on 500s?",
        stop="a real caller frame appears in Sentry for a deliberate error",
        budget_m=30, interval_m=20,
        hyps=["the async wrapper swallows the frame", "logging config filters it", "Sentry SDK installed after the app object"],
        tl=[
            (5, "try", "raise a deliberate error in a view", "I need a reproducible case"),
            (13, "try", "add exc_info=True to the logger call", "that usually restores the trace"),
            (20, "miss"),
            (24, "try", "reorder the sentry_sdk.init above create_app", "init order matters for instrumentation"),
            (32, "try", "raise the deliberate error again", "check whether the reorder helped"),
            (40, "ping", False, None),
            (46, "kill", 3),
            (52, "try", "print traceback.format_exc() inside the middleware", "I want to know if the frame exists before Sentry sees it"),
            (61, "kill", 2),
            (74, "close",
             "the async error middleware re-raised a bare exception, dropping __traceback__",
             "format_exc() inside the middleware already showed one frame, so it was lost above Sentry",
             "print the traceback at the innermost layer first and walk outward"),
        ],
    )),

    # ---------------------------------------------------------------- transition
    (28.1, dict(
        q="ingest pod gets SIGKILLed with nothing in the logs",
        stop="a pod restart with a recorded reason I can name",
        budget_m=45, interval_m=20,
        hyps=["OOMKill with the event not surfaced", "liveness probe timing out", "node pressure eviction"],
        tl=[
            (6, "try", "kubectl describe the pod after a kill", "the event list records the reason"),
            (14, "kill", 3),
            (20, "ping", True, 3),
            (23, "try", "check the liveness probe timeout against p99 startup", "probe timing is hypothesis 2"),
            (31, "kill", 2),
            (34, "miss"),
            (40, "try", "read the kubelet log on the node", "the OOM event should be there even if the pod event is gone"),
            (34, "cp", "p75", "cut", False, "confirm the kill reason is OOM — fixing the limit is a separate loop"),
            (70, "close",
             "cgroup OOM — the container limit was 512Mi and the parser buffers whole files",
             "kubelet log had the OOM line with the exact cgroup at the right second",
             "go to the kubelet log first; the pod's own events had already been rotated out"),
        ],
    )),
    (26.3, dict(
        q="OAuth redirect loops after the domain move",
        stop="a full login round trip on the new domain, twice",
        budget_m=30, interval_m=20,
        hyps=["redirect_uri still on the old host in the provider config", "cookie domain is too specific", "state param lost across the redirect"],
        tl=[
            (4, "try", "log the redirect_uri we send and the one they echo back", "if they differ the provider config is stale"),
            (11, "kill", 1),
            (16, "try", "inspect Set-Cookie on the callback response", "the loop smells like the session not surviving"),
            (20, "ping", True, 1),
            (24, "kill", 3),
            (38, "close",
             "session cookie Domain was pinned to the old host, so the callback saw no state",
             "the callback response set a cookie for a domain the browser then refused to send",
             "look at Set-Cookie on the callback first — a redirect loop is almost always a dropped session"),
        ],
    )),
    (24.0, dict(
        q="migration 0043 locks the users table in prod, fine in staging",
        stop="the migration runs against a prod-sized copy in under 5s",
        budget_m=60, interval_m=20,
        hyps=["ADD COLUMN with a default rewrites the table", "staging has 1/1000th the rows", "a long-running query holds a conflicting lock"],
        tl=[
            (7, "try", "run it against a prod snapshot", "staging is not a fair test"),
            (18, "try", "watch pg_locks while it runs", "I want to see who blocks whom"),
            (20, "miss"),
            (27, "kill", 3),
            (33, "try", "check the postgres version's ADD COLUMN behaviour", "11+ does not rewrite for constant defaults"),
            (40, "ping", False, None),
            (45, "cp", "p75", "continue", True),
            (52, "try", "read the generated SQL, not the migration file", "the ORM may not emit what I think"),
            (60, "cp", "p100", "extend", False, 90, "the generated SQL has a NOT VALID clause I did not expect"),
            (71, "kill", 1),
            (95, "close",
             "the ORM emitted ADD COLUMN + UPDATE in one transaction, holding ACCESS EXCLUSIVE for the backfill",
             "pg_locks showed ACCESS EXCLUSIVE held for the whole UPDATE, not just the DDL",
             "print the generated SQL before running any migration against prod"),
        ],
    )),
    (22.5, dict(
        q="S3 uploads over 5MB silently truncate",
        stop="a 50MB file round-trips byte-identical, twice",
        budget_m=30, interval_m=20,
        hyps=["multipart threshold misconfigured", "the file object is read twice and not rewound", "a proxy body-size limit"],
        tl=[
            (5, "try", "upload a 50MB file and compare checksums", "I need the failure in front of me"),
            (12, "try", "log the file position before and after the hash step", "hypothesis 2 is cheap to test"),
            (20, "ping", True, 2),
            (27, "close",
             "we hashed the stream for the integrity check and never seek(0) before uploading",
             "file.tell() was 52428800 at upload time",
             "log tell() around any code that reads a stream twice"),
        ],
    )),
    (20.8, dict(
        q="the nightly reconciliation cron runs twice, sometimes",
        stop="14 consecutive nights with exactly one run recorded",
        budget_m=45, interval_m=20,
        hyps=["two schedulers after the blue/green cutover", "retry on a timeout that actually succeeded", "DST double-hour"],
        tl=[
            (6, "try", "grep the run table for duplicate timestamps", "the gap between them will name the cause"),
            (15, "try", "check how many scheduler pods are running", "the cutover is the obvious suspect"),
            (20, "miss"),
            (26, "kill", 1),
            (32, "try", "correlate duplicate nights against the DST calendar", "the duplicates cluster oddly"),
            (40, "ping", False, None),
            (47, "try", "read the job wrapper's retry logic", "a retry after a false timeout fits the 30-second gap"),
            (55, "kill", 3),
            (66, "close",
             "the wrapper retried on a client-side 30s timeout while the job kept running server-side",
             "every duplicate pair was exactly 30 seconds apart",
             "look at the gap between the duplicates — a constant gap is always a timeout, never a scheduler"),
        ],
    )),
    (18.2, dict(
        q="connection refused from the API pod after about 6 hours of uptime",
        stop="24h uptime with no refused connections",
        budget_m=45, interval_m=20,
        hyps=["file descriptor leak", "keepalive mismatch with the load balancer", "conntrack table filling on the node"],
        tl=[
            (5, "try", "graph open fds per pod against uptime", "a leak shows as a straight line"),
            (13, "kill", 1),
            (19, "try", "compare the LB idle timeout to the server keepalive", "6 hours is too regular to be a leak"),
            (20, "ping", True, 1),
            (28, "try", "tcpdump a refused connection", "I want to see who sends the RST"),
            (35, "kill", 3),
            (41, "close",
             "the LB idle timeout (60s) was longer than the server keepalive (55s), so the LB reused dead sockets",
             "tcpdump showed the RST coming from us on a socket the LB thought was open",
             "compare the two timeout numbers — this is a five-second check and it is always the answer"),
        ],
    )),
    (16.6, dict(
        q="the CSS bundle grew 400KB after the dependency bump",
        stop="bundle back under 180KB with the bump kept",
        budget_m=20, interval_m=20,
        hyps=["a dep now ships unpurged utility CSS", "sourcemaps inlined into the bundle", "duplicate copies of the same dep"],
        tl=[
            (4, "try", "run the bundle analyzer on both commits", "the treemap names the file"),
            (11, "kill", 3),
            (16, "kill", 2),
            (24, "close",
             "the new version ships its own tailwind preflight and our purge config did not cover node_modules",
             "the treemap showed one 380KB file with a name I did not recognise",
             "run the analyzer first — 30 seconds and it points at the file"),
        ],
    )),

    # ---------------------------------------------------------------- disciplined
    (14.1, dict(
        q="Sentry reports 500s but the load balancer reports 200s",
        stop="one request traced end to end with a status both agree on",
        budget_m=30, interval_m=20,
        hyps=["the error happens after the response is flushed", "two different services, same trace id", "Sentry sampling artefact"],
        tl=[
            (6, "try", "find one request id in both systems", "if they disagree on one id, it is real"),
            (14, "kill", 3),
            (20, "ping", True, 3),
            (24, "try", "check where in the middleware stack the exception fires", "a post-flush error explains both readings"),
            (30, "kill", 2),
            (33, "close",
             "a background task attached to the response fires after flush; the client already had its 200",
             "the exception timestamp was 40ms after the access log line",
             "compare the two timestamps for one request — the ordering was the whole answer"),
        ],
    )),
    (12.4, dict(
        q="Kafka consumer lag spikes every Sunday at 03:00",
        stop="one Sunday with lag under 1000 through the window",
        budget_m=45, interval_m=20,
        hyps=["log compaction running in the same window", "the weekly analytics job saturating the brokers", "partition rebalance from a scheduled restart"],
        tl=[
            (5, "try", "list everything scheduled at 03:00 Sunday", "a weekly spike means a weekly job"),
            (13, "kill", 1),
            (20, "ping", True, 1),
            (25, "try", "check consumer group generation ids across the window", "a rebalance bumps the generation"),
            (32, "kill", 3),
            (38, "try", "graph broker network out against the analytics job window", "hypothesis 2 is the last one standing"),
            (44, "close",
             "the weekly analytics export reads the whole topic from offset 0 and saturates broker network",
             "broker network out went to the NIC ceiling at exactly 03:00",
             "list the weekly cron entries first — a spike with a calendar has a calendar cause"),
        ],
    )),
    (10.0, dict(
        q="the dashboard takes 9 seconds to first paint",
        stop="first contentful paint under 2s on a cold cache",
        budget_m=45, interval_m=20,
        hyps=["render-blocking font CSS", "the summary endpoint is serial with four others", "no gzip on the JS bundle"],
        tl=[
            (6, "try", "record a cold-cache profile in devtools", "the waterfall shows what blocks what"),
            (15, "kill", 3),
            (20, "ping", True, 3),
            (23, "try", "check whether the five API calls are serial or parallel", "the waterfall looked like a staircase"),
            (31, "kill", 1),
            (34, "cp", "p75", "cut", False, "get first paint under 4s; the font work is its own loop"),
            (52, "close",
             "five summary calls chained on each other's promises — a staircase, not a fan-out",
             "the waterfall was a perfect staircase, each request starting when the last finished",
             "look at the waterfall shape before optimising anything; the shape names the bug"),
        ],
    )),
    (8.3, dict(
        q="session cookie dropped on the mobile web app",
        stop="a login that survives a page reload on iOS Safari, twice",
        budget_m=30, interval_m=20,
        hyps=["SameSite=None without Secure", "cookie set on a redirect that Safari drops", "ITP capping the expiry"],
        tl=[
            (5, "try", "read the Set-Cookie header on a real device", "the attributes will say it outright"),
            (12, "kill", 2),
            (18, "kill", 3),
            (22, "close",
             "SameSite=None without Secure — Safari rejects the pair outright, Chrome warned but accepted",
             "the Set-Cookie header had SameSite=None and no Secure flag, visible immediately",
             "read the actual header on the failing browser first"),
        ],
    )),

    # ------------------------------------------------- a loop interrupted by a loop
    (6.5, dict(
        q="invoice PDF renderer throws TypeError, EU customers only",
        stop="all 40 failing invoices render",
        budget_m=45, interval_m=20,
        hyps=["VAT id is None for non-registered customers", "the address formatter assumes a state field", "font missing for a non-latin name"],
        tl=[
            (6, "try", "group the 40 failures by country", "the grouping usually names the field"),
            (14, "kill", 3),
            (20, "ping", True, 3),
            (26, "pause", "prod search ordering broke, dropping this"),
            # the child loop below opens at +28m and closes at +46m
            (50, "resume"),
            (58, "try", "render one failing invoice with the address formatter stubbed", "narrowing to one of the two remaining"),
            (65, "kill", 2),
            (72, "close",
             "vat_id is None for non-registered EU customers and the template calls .upper() on it",
             "every failure was a customer with a blank VAT field",
             "group the failures by any field before opening the code — the group was the answer"),
        ],
    )),
    (6.5 - 28 * MIN / DAY, dict(  # nested: opens 28m into the paused loop above
        q="search results ordering changed and nobody knows why",
        stop="the old ordering is restored or the change is explained",
        budget_m=20, interval_m=20,
        parent=True,
        hyps=["someone changed the boost config", "the index was rebuilt with a new analyzer", "a tie-break field changed type"],
        tl=[
            (3, "try", "diff the search config against last week's deploy", "a behaviour change with no code change is config"),
            (9, "kill", 2),
            (14, "kill", 3),
            (18, "close",
             "a boost value went from 1.5 to 15 in a config PR nobody reviewed",
             "the config diff was two lines and one of them was the boost",
             "diff the config first when behaviour changed and code did not"),
        ],
    )),

    # Protocol followed, loop still abandoned — the checkpoint said stop and
    # stopping was right. A 100%-resolved logbook would be a lie.
    (9.2, dict(
        q="intermittent 502s on the checkout endpoint",
        stop="a 502 reproduced with a request id I can trace",
        budget_m=30, interval_m=20,
        hyps=["upstream timeout shorter than the handler's worst case", "a pod failing readiness mid-rollout", "the CDN retrying a POST"],
        tl=[
            (6, "try", "pull every 502 from the LB log for the week", "I need to know if there is a pattern before guessing"),
            (14, "kill", 3),
            (20, "ping", False, None),
            (23, "try", "correlate the 502 timestamps against deploy times", "hypothesis 2 predicts they cluster on deploys"),
            (30, "cp", "p100", "close", False),
            (31, "abandon"),
        ],
    )),
    (4.2, dict(
        q="pytest passes locally, ModuleNotFoundError on CI",
        stop="three consecutive green CI runs",
        budget_m=20, interval_m=20,
        hyps=["the package is not installed in the CI env", "rootdir differs so conftest is not picked up", "the local env has a stale editable install"],
        tl=[
            (4, "try", "print sys.path from inside a CI test", "the answer is always on sys.path"),
            (10, "kill", 1),
            (15, "kill", 2),
            (16, "close",
             "a stale editable install locally was masking a missing entry in the packages list",
             "sys.path on CI was missing the src dir that my local .pth file was adding",
             "print sys.path on both sides and diff them"),
        ],
    )),
    (2.6, dict(
        q="user avatars 404 in Safari but load in Chrome",
        stop="avatars render in Safari on two devices",
        budget_m=30, interval_m=20,
        hyps=["the CDN serves avif and Safari asks for something else", "CORS preflight failing only for Safari", "a service worker caching a bad response"],
        tl=[
            (5, "try", "compare the request headers Safari and Chrome send", "a browser-specific 404 is a negotiation problem"),
            (13, "kill", 2),
            (19, "kill", 3),
            (20, "ping", True, 3),
            (29, "close",
             "the CDN varied on Accept but did not send Vary, so Safari got a cached avif it cannot decode",
             "Safari's Accept header had no image/avif and the response was avif anyway",
             "diff the request headers between the working and broken browser"),
        ],
    )),
    (1.1, dict(
        q="worker queue drains to zero then stalls for 90 seconds",
        stop="sustained throughput with no stall over 30 minutes",
        budget_m=45, interval_m=20,
        hyps=["prefetch count too high so one worker hoards", "visibility timeout expiring and redelivering", "the poll loop backs off exponentially with no reset"],
        tl=[
            (6, "try", "log queue depth and in-flight count together", "a stall with work available is a distribution problem"),
            (14, "kill", 2),
            (20, "ping", True, 2),
            (23, "try", "read the poll loop's backoff code", "90 seconds is suspiciously round"),
            (31, "kill", 1),
            (38, "close",
             "the empty-poll backoff doubled to 90s and never reset when work arrived",
             "the stall was always exactly 90s — the backoff ceiling",
             "a constant-duration stall is always a timer; find the timer with that constant"),
        ],
    )),
]

# The loop left open on screen: 52 minutes into a 90-minute budget, seven
# actions deep, nothing ruled out. This is what the thrash panel is for.
LIVE = dict(
    q="ingest pod is OOM-killed only in prod",
    stop="24h in prod with no OOMKill events",
    budget_m=90, interval_m=20,
    hyps=[
        "we buffer the whole file instead of streaming it",
        "prod payloads are an order of magnitude larger",
        "a leak in the parser's regex cache",
        "the memory limit was lowered in a chart bump",
    ],
    tl=[
        (3, "try", "bump the memory limit to 1Gi", "more headroom will stop the kills"),
        (9, "try", "restart the deployment", "the current pods may be in a bad state"),
        (16, "try", "watch container_memory_usage_bytes in Grafana", "the shape of the curve will tell me something"),
        (20, "ping", False, None),
        (24, "try", "bump the limit again to 2Gi", "1Gi was not enough headroom"),
        (31, "try", "scale to 6 replicas", "spreading the load lowers per-pod memory"),
        (40, "ping", False, None),
        (43, "try", "diff the prod and staging helm values", "something must be different between them"),
        (50, "try", "restart the deployment again", "check whether the replica change took effect"),
        (50, "ping", False, None),
    ],
)

# How far the live loop is pinned in, expressed as a lead time before the
# next check-in rather than as an elapsed figure: the demo exists to show
# the check-in, so what matters is how long you wait for it. The p75
# checkpoint at 0.75 * budget arrives before the ping at +70m, and it is
# the interesting one — cut scope and extend estimate live on it.
LIVE_LEAD_M = 3.0


def live_elapsed_m(lead_m: float) -> float:
    return 0.75 * LIVE["budget_m"] - lead_m


def build(now: float, lead_m: float = LIVE_LEAD_M) -> list[dict]:
    log: list[dict] = []
    loop_id = 0
    parent_of_next: int | None = None

    def emit(type: str, ts: float, lid: int, **fields) -> None:
        log.append({"type": type, "ts": ts, "loop_id": lid, **fields})

    def render(spec: dict, open_ts: float, lid: int, *, live: bool) -> None:
        nonlocal parent_of_next
        parent = parent_of_next if spec.get("parent") else None
        if spec.get("parent"):
            parent_of_next = None

        emit("loop_opened", open_ts, lid,
             question=spec["q"], stop_condition=spec["stop"],
             budget_s=float(spec["budget_m"] * 60), interval_s=float(spec["interval_m"] * 60),
             hypotheses=list(spec["hyps"]), parent_id=parent)

        next_hyp = len(spec["hyps"]) + 1
        stop_condition = spec["stop"]
        budget_s = float(spec["budget_m"] * 60)

        for step in spec["tl"]:
            minute, op, args = step[0], step[1], step[2:]
            ts = open_ts + minute * MIN

            if op == "try":
                emit("action_logged", ts, lid, action=args[0], because=args[1])
            elif op == "hyp":
                emit("hypothesis_added", ts, lid, hyp_id=next_hyp, text=args[0])
                next_hyp += 1
            elif op == "kill":
                emit("hypothesis_eliminated", ts, lid, hyp_id=args[0])
            elif op == "ping":
                smaller, hyp_id = args[0], args[1]
                emit("ping_answered", ts, lid, smaller=smaller, eliminated=hyp_id,
                     shown_at=ts - 12.0)
                if hyp_id is not None:
                    emit("hypothesis_eliminated", ts, lid, hyp_id=hyp_id)
            elif op == "miss":
                emit("ping_unanswered", ts, lid, shown_at=ts - 300.0)
            elif op == "cpmiss":
                emit("checkpoint_shown", ts - 1.0, lid, kind=args[0])
                emit("checkpoint_unanswered", ts, lid, kind=args[0], shown_at=ts - 300.0)
            elif op == "cp":
                kind, decision, on_track = args[0], args[1], args[2]
                emit("checkpoint_shown", ts - 1.0, lid, kind=kind)
                emit("checkpoint_answered", ts, lid, kind=kind,
                     on_track=on_track, decision=decision)
                if decision == "cut":
                    emit("scope_cut", ts, lid,
                         old_stop_condition=stop_condition, new_stop_condition=args[3])
                    stop_condition = args[3]
                elif decision == "extend":
                    new_budget_s = float(args[3] * 60)
                    emit("budget_extended", ts, lid,
                         old_budget_s=budget_s, new_budget_s=new_budget_s, learned=args[4])
                    budget_s = new_budget_s
            elif op == "pause":
                emit("loop_paused", ts, lid, reason=args[0])
                parent_of_next = lid
            elif op == "resume":
                emit("loop_resumed", ts, lid)
            elif op == "close":
                emit("loop_closed", ts, lid,
                     what_was_it=args[0], giveaway=args[1], five_min_path=args[2])
            elif op == "abandon":
                emit("loop_abandoned", ts, lid)
            else:
                raise ValueError(f"unknown op {op!r}")

        if live:
            return

    # Oldest first, so loop ids read chronologically the way a real log's do.
    for days_ago, spec in sorted(LOOPS, key=lambda item: -item[0]):
        loop_id += 1
        render(spec, now - days_ago * DAY, loop_id, live=False)

    loop_id += 1
    render(LIVE, now - live_elapsed_m(lead_m) * MIN, loop_id, live=True)

    log.sort(key=lambda e: e["ts"])
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default=str(Path.home() / ".loop-demo"),
                        help="LOOP_HOME to seed (default: ~/.loop-demo)")
    parser.add_argument("--now", type=float, default=None,
                        help="epoch seconds to anchor the log to (default: now)")
    parser.add_argument("--lead", type=float, default=LIVE_LEAD_M,
                        help="minutes until the live loop's next check-in "
                             f"(default: {LIVE_LEAD_M:g})")
    parser.add_argument("--force", action="store_true",
                        help="overwrite a non-empty events.jsonl")
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser()
    target = home / "events.jsonl"

    if home.resolve() == (Path.home() / ".loop").resolve() and not args.force:
        print("refusing to seed the real ~/.loop; pass --home elsewhere", file=sys.stderr)
        return 2
    if target.exists() and target.stat().st_size and not args.force:
        print(f"{target} is not empty; pass --force to overwrite", file=sys.stderr)
        return 2

    log = build(args.now if args.now is not None else time.time(), args.lead)
    home.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for event in log:
            handle.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")

    loops = sum(1 for e in log if e["type"] == "loop_opened")
    print(f"wrote {len(log)} events across {loops} loops to {target}")
    print()
    print(f"  LOOP_HOME={home} .venv/bin/loop stats")
    print(f"  LOOP_HOME={home} .venv/bin/loop ls")
    print(f"  LOOP_HOME={home} .venv/bin/python -m loop.gui")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
