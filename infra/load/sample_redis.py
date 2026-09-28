"""
sample_redis.py — amostra o Redis durante a rodada de carga (PRD-02), sem dependência.

O Redis é o ponto único de estado quente (stream, ContextStore, pipeline_state, filas),
e dois números dizem se ele é o teto: conexões (o webchat abre 2 por WebSocket) e
clientes BLOQUEADOS (BLPOP do `receive`/`menu` — cada sessão de IA parada num menu é um).
Grava uma linha CSV a cada `--every` segundos.

Colunas: ts, connected_clients, blocked_clients, used_memory_mb, ops_per_sec,
rejected_connections, keyspace_keys.

Uso: python3 sample_redis.py --host redis --port 6379 [--password X] --every 5 --out redis.csv
"""
from __future__ import annotations

import argparse
import socket
import time

def _cmd(sock: socket.socket, *parts: str) -> bytes:
    req = f"*{len(parts)}\r\n" + "".join(f"${len(p.encode())}\r\n{p}\r\n" for p in parts)
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n" not in buf:
        buf += sock.recv(65536)
    head, rest = buf.split(b"\r\n", 1)
    if head.startswith(b"-"):
        raise RuntimeError(head.decode())
    if not head.startswith(b"$"):
        return head[1:]
    size = int(head[1:])
    while len(rest) < size + 2:
        rest += sock.recv(65536)
    return rest[:size]


def info(sock: socket.socket) -> dict[str, str]:
    out = {}
    for line in _cmd(sock, "INFO").decode().splitlines():
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            out[k] = v
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=6379)
    ap.add_argument("--password")
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--out", default="redis.csv")
    args = ap.parse_args()
    sock = socket.create_connection((args.host, args.port), timeout=10)
    if args.password:
        _cmd(sock, "AUTH", args.password)
    with open(args.out, "a", encoding="utf-8") as fh:
        fh.write("ts,connected_clients,blocked_clients,used_memory_mb,ops_per_sec,"
                 "rejected_connections,keyspace_keys\n")
        while True:
            i = info(sock)
            keys = sum(int(v.split(",")[0].split("=")[1]) for k, v in i.items() if k.startswith("db"))
            row = [time.strftime("%Y-%m-%dT%H:%M:%S"), i.get("connected_clients", ""),
                   i.get("blocked_clients", ""), f"{int(i.get('used_memory', 0)) / 2**20:.1f}",
                   i.get("instantaneous_ops_per_sec", ""), i.get("rejected_connections", ""), str(keys)]
            fh.write(",".join(row) + "\n")
            fh.flush()
            print(" ".join(row), flush=True)
            time.sleep(args.every)


if __name__ == "__main__":
    main()
