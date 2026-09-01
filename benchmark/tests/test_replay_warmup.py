"""The declared replay warm-up walks the delimiter tree and anchors probes on returned keys."""

from __future__ import annotations

import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from benchmark import replay, replay_runtime

NS = "http://s3.amazonaws.com/doc/2006-03-01/"
TREE = {
    "": (["a/", "b/"], ["root.txt"]),
    "a/": (["a/x/"], ["a/1", "a/2"]),
    "b/": ([], ["b/1"]),
    "a/x/": ([], ["a/x/1"]),
}


def _listing(prefixes: list[str], keys: list[str]) -> bytes:
    body = [f'<ListBucketResult xmlns="{NS}"><Name>fx</Name><Prefix></Prefix>']
    body.extend(f"<Contents><Key>{key}</Key></Contents>" for key in keys)
    body.extend(
        f"<CommonPrefixes><Prefix>{prefix}</Prefix></CommonPrefixes>" for prefix in prefixes
    )
    body.append("</ListBucketResult>")
    return "".join(body).encode()


class _Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, str]]] = []
    lock: ClassVar[threading.Lock] = threading.Lock()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        with self.lock:
            self.seen.append(query)
        if parsed.path != "/fx":
            self.send_response(404)
            self.end_headers()
            return
        if "delimiter" in query:
            prefixes, keys = TREE.get(query.get("prefix", ""), ([], []))
            body = _listing(prefixes, keys)
        else:
            body = _listing([], ["after-" + query.get("start-after", "")])
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def test_warm_up_walks_breadth_first_and_reports_issued_counts() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = replay.parse_document(
            {
                "backend": {
                    "server_image_uri": "registry/replay@sha256:" + "a" * 64,
                    "fixture_sha256": "b" * 64,
                    "serving_mode": "sorted",
                    "latency_model": "none",
                    "warmup": {
                        "structure_probes": 10,
                        "pivot_probes": 3,
                        "worker_pages": 2,
                        "in_flight": 2,
                    },
                },
                "allocation": {
                    "subject_vcpus": 2,
                    "replay_vcpus": 2,
                    "replay_memory_gb": 4,
                    "replay_parquet_connections": 4,
                    "replay_max_concurrent_requests": 32,
                    "replay_heap_percent": 75,
                    "replay_prefetch_max_windows": 96,
                    "replay_prefetch": False,
                },
                "capacity_status": "uncalibrated",
            }
        )
        host, port = server.server_address[:2]
        outcome = replay_runtime.warm_up(config, "fx", endpoint_url=f"http://{host}:{port}")
    finally:
        server.shutdown()
        server.server_close()
    # The tree has four prefixes, so the walk exhausts before ten probes: truncated, visibly.
    assert outcome["state"] == "truncated"
    assert outcome["issued"] == {"structure_probes": 4, "pivot_probes": 3, "worker_pages": 2}
    assert outcome["distinct_prefixes"] == 3 and outcome["failures"] == 0
    structure = [q.get("prefix", "") for q in _Handler.seen if "delimiter" in q]
    # Breadth-first by wave: the root alone, then its two children in either arrival order
    # (they run concurrently), then the grandchild.
    assert structure[0] == "" and sorted(structure[1:3]) == ["a/", "b/"] and structure[3] == "a/x/"
    probes = [q for q in _Handler.seen if "delimiter" not in q]
    assert sorted(q["max-keys"] for q in probes) == ["1", "1", "1", "1000", "1000"]
    assert all(q["start-after"] in {"root.txt", "a/1", "a/2", "b/1", "a/x/1"} for q in probes)
