# Cataclysm conversion goal

## Goal

Completely convert the upstream AzerothCore project from World of Warcraft 3.3.5a (WotLK) to
Cataclysm 4.3.4.

Keep the fork's design, framework, interfaces, and code structure as close to upstream as practical
so upstream updates remain straightforward to integrate. Prefer the smallest Cata-specific delta;
depart from upstream only where Cataclysm protocol, data, or gameplay differences require it.

## Database safety

Never point conversion work, tests, or client smoke runs at an existing database. Treat existing
schemas, data, containers, and volumes as immutable.

When a database is required, use a disposable run-owned Docker container and volume. Bind it to
`127.0.0.1` on a confirmed unused host port, record the exact resources in the run manifest, and
delete only those recorded resources during cleanup. Reuse an existing suitable image; build a
separate test image only when the existing images cannot provide the required Cataclysm schema.
