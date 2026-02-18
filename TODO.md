# Future Feature Ideas

## Multi-speaker broadcasting (peer-aware servers with client-side fan-out)

Every Stentor server serves the same UI and knows about all peers. Each server
has the same peer list in its `.env` (e.g.,
`PEERS=speaker-a:8000,speaker-b:8000,speaker-c:8000`). You open any of them in
your browser, get the full list via `/config`, and broadcast to all.

```
Any server's .env:
  PEERS=speaker-a:8000,speaker-b:8000,speaker-c:8000

Browser loads from ANY server → gets peer list → connects to all via WS
```

Key properties:
- Every server is equal — no coordinator
- The browser does the fan-out
- No single point of failure — if speaker A is down, open speaker B's page and
  broadcast to B and C
- Just keep the `PEERS` list in sync across `.env` files
