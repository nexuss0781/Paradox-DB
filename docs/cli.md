# Paradox-DB CLI Reference

```
tgdb <command> [options]
```

Global flags:
- `--json` — Machine-readable output
- `--version`, `-v` — Show version
- `--help`, `-h` — Show help

---

## Database Commands

### init

```bash
tgdb init <name>
```

Create a new encrypted database.

### open

```bash
tgdb open <name>
```

Verify a database can be opened with the current passphrase.

---

## Query Commands

### exec

```bash
tgdb exec "<sql>"
```

Execute raw SQL (DDL, DML).

### insert

```bash
tgdb insert <table> '<json>'
```

Insert a row from JSON.

### select

```bash
tgdb select <table> ['{"column":"value"}']
```

Query rows with optional WHERE filter.

### update

```bash
tgdb update <table> '<set>' '<where>'
```

Update rows. Both arguments are JSON.

### delete

```bash
tgdb delete <table> '<where>'
```

Delete rows matching the WHERE filter.

---

## Sync Commands

### sync

```bash
tgdb sync
```

Push local changes and pull remote updates.

### pull

```bash
tgdb pull [version]
```

Pull latest or a specific version from Telegram.

### status

```bash
tgdb status
```

Show sync status for all databases.

### versions

```bash
tgdb versions
```

List all remote versions for databases.

### rollback

```bash
tgdb rollback <version>
```

Rollback to a specific version via gateway.

---

## Config Commands

### config show

```bash
tgdb config show
```

Display current configuration.

### config set

```bash
tgdb config set <key> <value>
```

Update a config value (dot notation for nested keys).

---

## Interactive

### shell

```bash
tgdb shell
```

Open interactive SQL REPL. Type SQL directly, or `exit` to quit.

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PARADOX_PASSPHRASE` | Database encryption passphrase |
