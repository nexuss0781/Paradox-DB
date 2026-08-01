#!/usr/bin/env node
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as path from 'node:path';
import * as os from 'node:os';
import * as readline from 'node:readline/promises';
import { stdin, stdout } from 'node:process';
import { connect } from './connection.js';
import { GatewayClient } from './gateway.js';
import { loadConfig, saveConfig, getDefaultConfigPath } from './config.js';
import * as state from './state.js';
import { encryptFile } from './crypto.js';
import * as fs from 'node:fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const args = process.argv.slice(2);
const command = args[0];

function usage(): void {
  console.log(`
parad — Paradox-DB CLI

Usage: parad <command> [options]

Commands:
  init <name>              Create new encrypted database
  connect <url>            Connect via postgres-like connection string
  exec <sql>               Execute raw SQL
  insert <table> <json>    Insert row
  select <table> [where]   Query rows
  update <table> <set> <where>  Update rows
  delete <table> <where>   Delete rows
  push                     Push local changes to gateway
  pull [version]           Pull latest (or specific version)
  sync                     Push then pull
  status                   Show sync status
  versions                 List remote versions
  rollback <version>       Rollback to version
  config show|set          Show / update config
  shell                    Interactive REPL
  --help, -h               Show this help
  --version, -v            Show version
`);
}

function getVersion(): string {
  try {
    const pkg = JSON.parse(readFileSync(path.join(__dirname, '..', 'package.json'), 'utf-8'));
    return pkg.version || '0.1.0';
  } catch {
    return '0.1.0';
  }
}

function output(data: unknown, jsonMode: boolean): void {
  if (jsonMode) {
    console.log(JSON.stringify(data, null, 2));
  } else if (Array.isArray(data)) {
    if (data.length === 0) {
      console.log('(empty)');
      return;
    }
    console.table(data);
  } else if (typeof data === 'object' && data !== null) {
    Object.entries(data).forEach(([k, v]) => console.log(`${k}: ${v}`));
  } else {
    console.log(data);
  }
}

const jsonMode = args.includes('--json');
const cleanArgs = args.filter((a) => a !== '--json');

async function main(): Promise<void> {
  switch (command) {
    case 'init': {
      const name = cleanArgs[1];
      if (!name) {
        console.error('Usage: parad init <name>');
        process.exit(1);
      }
      const conn = await connect({ name, autoSync: false });
      conn.execute('CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)');
      conn.close();
      output({ status: 'created', name, path: conn.engine.dbPath }, jsonMode);
      break;
    }
    case 'connect': {
      const url = cleanArgs[1];
      if (!url) {
        console.error('Usage: parad connect <url>');
        process.exit(1);
      }
      const conn = await connect({ url, autoSync: false });
      output({ status: 'connected', name: conn.dbKey, path: conn.engine.dbPath }, jsonMode);
      conn.close();
      break;
    }
    case 'exec': {
      const sql = cleanArgs.slice(1).join(' ');
      if (!sql) {
        console.error('Usage: parad exec <sql>');
        process.exit(1);
      }
      const conn = await connect({});
      const result = conn.execute(sql);
      if (result.rows.length > 0) output(result.rows, jsonMode);
      else output({ changes: result.changes, lastInsertRowid: result.lastInsertRowid }, jsonMode);
      conn.close();
      break;
    }
    case 'insert': {
      const table = cleanArgs[1];
      const jsonData = cleanArgs.slice(2).join(' ');
      if (!table || !jsonData) {
        console.error('Usage: parad insert <table> <json>');
        process.exit(1);
      }
      const row = JSON.parse(jsonData);
      const conn = await connect({});
      const id = conn.engine.insert(table, row);
      output({ inserted_id: id }, jsonMode);
      conn.close();
      break;
    }
    case 'select': {
      const table = cleanArgs[1];
      if (!table) {
        console.error('Usage: parad select <table>');
        process.exit(1);
      }
      const whereStr = cleanArgs[2];
      const where = whereStr ? JSON.parse(whereStr) : undefined;
      const conn = await connect({});
      const rows = conn.engine.select(table, where);
      output(rows, jsonMode);
      conn.close();
      break;
    }
    case 'update': {
      const table = cleanArgs[1];
      const setStr = cleanArgs[2];
      const whereStr = cleanArgs[3];
      if (!table || !setStr || !whereStr) {
        console.error('Usage: parad update <table> <set> <where>');
        process.exit(1);
      }
      const conn = await connect({});
      const changes = conn.engine.update(table, JSON.parse(setStr), JSON.parse(whereStr));
      output({ changes }, jsonMode);
      conn.close();
      break;
    }
    case 'delete': {
      const table = cleanArgs[1];
      const whereStr = cleanArgs.slice(2).join(' ');
      if (!table || !whereStr) {
        console.error('Usage: parad delete <table> <where>');
        process.exit(1);
      }
      const conn = await connect({});
      const changes = conn.engine.delete(table, JSON.parse(whereStr));
      output({ changes }, jsonMode);
      conn.close();
      break;
    }
    case 'push': {
      const conn = await connect({});
      const version = await conn.push();
      output({ pushed: version !== null, version }, jsonMode);
      conn.close();
      break;
    }
    case 'pull': {
      const conn = await connect({});
      const versionArg = cleanArgs[1];
      let pulled: boolean;
      if (versionArg) {
        pulled = await conn.pullVersion(parseInt(versionArg, 10));
      } else {
        pulled = await conn.pull();
      }
      output({ pulled }, jsonMode);
      conn.close();
      break;
    }
    case 'sync': {
      const conn = await connect({});
      const pushed = await conn.push();
      const pulled = await conn.pull();
      output({ pushed, pulled }, jsonMode);
      conn.close();
      break;
    }
    case 'status': {
      const cfg = loadConfig();
      const base = cfg.sync.gateway_url.replace(/\/+$/, '');
      try {
        const gw = new GatewayClient(base, cfg.sync.api_key);
        const status = await gw.status();
        const enriched = {
          ...status,
          databases: status.databases.map((d) => ({
            ...d,
            local_version: state.getRemoteVersion(d.name),
            dirty: state.isDirty(d.name),
            offline: state.isOffline(d.name),
          })),
        };
        output(enriched, jsonMode);
      } catch (err) {
        output({ error: `Could not fetch status: ${err instanceof Error ? err.message : String(err)}` }, jsonMode);
      }
      break;
    }
    case 'versions': {
      const cfg = loadConfig();
      const base = cfg.sync.gateway_url.replace(/\/+$/, '');
      try {
        const gw = new GatewayClient(base, cfg.sync.api_key);
        const data = await gw.versions(path.basename(cfg.database_path).replace(/\.db$/, ''));
        output(data, jsonMode);
      } catch (err) {
        output({ error: `Could not fetch versions: ${err instanceof Error ? err.message : String(err)}` }, jsonMode);
      }
      break;
    }
    case 'rollback': {
      const version = parseInt(cleanArgs[1], 10);
      if (!version) {
        console.error('Usage: parad rollback <version>');
        process.exit(1);
      }
      const cfg = loadConfig();
      const base = cfg.sync.gateway_url.replace(/\/+$/, '');
      try {
        const gw = new GatewayClient(base, cfg.sync.api_key);
        await gw.rollback(path.basename(cfg.database_path).replace(/\.db$/, ''), version);
        const conn = await connect({});
        await conn.pull();
        conn.close();
        output({ rolled_back_to: version, success: true }, jsonMode);
      } catch (err) {
        output({ error: `Rollback failed: ${err instanceof Error ? err.message : String(err)}` }, jsonMode);
      }
      break;
    }
    case 'config': {
      const sub = cleanArgs[1];
      if (sub === 'show') {
        output(loadConfig(), jsonMode);
      } else if (sub === 'set') {
        const key = cleanArgs[2];
        const value = cleanArgs[3];
        if (!key || !value) {
          console.error('Usage: parad config set <key> <value>');
          process.exit(1);
        }
        const config = loadConfig();
        const keys = key.split('.');
        let obj: any = config;
        for (let i = 0; i < keys.length - 1; i++) obj = obj[keys[i]];
        obj[keys[keys.length - 1]] = value;
        saveConfig(config);
        output({ updated: key, value }, jsonMode);
      } else {
        console.error('Usage: parad config <show|set>');
        process.exit(1);
      }
      break;
    }
    case 'shell': {
      const rl = readline.createInterface({ input: stdin, output: stdout, prompt: 'parad> ' });
      const conn = await connect({});
      console.log('Paradox-DB interactive shell. Type "help" for commands, "exit" to quit.');
      rl.prompt();
      rl.on('line', async (line: string) => {
        const trimmed = line.trim();
        if (trimmed === 'exit' || trimmed === 'quit') {
          conn.close();
          process.exit(0);
        }
        if (trimmed === 'help') console.log('Commands: <sql>, help, exit');
        else if (trimmed) {
          try {
            const result = conn.execute(trimmed);
            if (result.rows.length > 0) console.table(result.rows);
            else console.log(`OK (${result.changes} changes)`);
          } catch (e: any) {
            console.error(`Error: ${e.message}`);
          }
        }
        rl.prompt();
      });
      break;
    }
    case '--version':
    case '-v':
      console.log(getVersion());
      break;
    case '--help':
    case '-h':
    case undefined:
      usage();
      break;
    default:
      console.error(`Unknown command: ${command}`);
      usage();
      process.exit(1);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
