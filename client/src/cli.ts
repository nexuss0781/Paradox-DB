#!/usr/bin/env node
import { ClientEngine } from './engine.js';
import { ChangeTracker } from './change-tracker.js';
import { SyncManager } from './sync-manager.js';
import { ConflictHandler } from './conflict-handler.js';
import { loadConfig, saveConfig } from './config.js';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

const args = process.argv.slice(2);
const command = args[0];

function usage() {
  console.log(`
tgdb — Paradox-DB CLI

Usage: tgdb <command> [options]

Commands:
  init <name>              Create new encrypted database
  open <name>              Open existing database
  exec <sql>               Execute raw SQL
  insert <table> <json>    Insert row
  select <table> [where]   Query rows
  update <table> <set> <where>  Update rows
  delete <table> <where>   Delete rows
   sync                     Manual sync trigger
   push                     Push local changes to gateway
   pull [version]           Pull latest from Telegram
  status                   Show sync status
  logs                     Show sync history
  versions                 List remote versions
  rollback <version>       Rollback to version
  config show              Show config
  config set <key> <value> Update config
  shell                    Interactive REPL
  --help, -h               Show this help
  --json                   Machine-readable output
  --version, -v            Show version
`);
}

function getVersion(): string {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf-8'));
    return pkg.version || '0.1.0';
  } catch {
    return '0.1.0';
  }
}

function resolveDbPath(name?: string): string {
  const config = loadConfig();
  if (name) {
    const base = config.database_path.replace(/^~/, os.homedir());
    return path.join(path.dirname(base), `${name}.sqlcipher`);
  }
  return config.database_path.replace(/^~/, os.homedir());
}

function getEngine(name?: string): ClientEngine {
  const config = loadConfig();
  const dbPath = resolveDbPath(name);
  config.database_path = dbPath;
  return new ClientEngine(config);
}

function output(data: any, jsonMode: boolean) {
  if (jsonMode) {
    console.log(JSON.stringify(data, null, 2));
  } else {
    if (Array.isArray(data)) {
      if (data.length === 0) { console.log('(empty)'); return; }
      console.table(data);
    } else if (typeof data === 'object' && data !== null) {
      Object.entries(data).forEach(([k, v]) => console.log(`${k}: ${v}`));
    } else {
      console.log(data);
    }
  }
}

const jsonMode = args.includes('--json');
const cleanArgs = args.filter(a => a !== '--json');

async function main() {
  const passphrase = process.env.PARADOX_PASSPHRASE || 'default';

  switch (command) {
    case 'init': {
      const name = cleanArgs[1];
      if (!name) { console.error('Usage: tgdb init <name>'); process.exit(1); }
      const config = loadConfig();
      if (!config.sync.api_key) {
        console.log('No API key found. Registering with gateway...');
        const syncManager = new SyncManager(config);
        const baseUrl = config.sync.gateway_url.replace(/\/+$/, '');
        try {
          const result = await syncManager.httpPostJSON(`${baseUrl}/v1/auth/register`, {}, '');
          if (result.status === 200 && result.data?.api_key) {
            config.sync.api_key = result.data.api_key;
            saveConfig(config);
            console.log(`Registered. User ID: ${result.data.user_id}`);
            console.log(`API key saved to config.`);
          } else {
            console.error('Registration failed. You can set api_key manually in config.');
          }
        } catch (err: any) {
          console.error(`Registration failed: ${err.message}. Set api_key manually.`);
        }
      }
      const dbPath = resolveDbPath(name);
      if (fs.existsSync(dbPath)) { console.error(`Database '${name}' already exists`); process.exit(1); }
      const engine = getEngine(name);
      engine.open(passphrase, dbPath);
      engine.execute('CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)');
      engine.close();
      output({ status: 'created', name, path: dbPath }, jsonMode);
      break;
    }
    case 'open': {
      const name = cleanArgs[1];
      if (!name) { console.error('Usage: tgdb open <name>'); process.exit(1); }
      const engine = getEngine(name);
      engine.open(passphrase);
      output({ status: 'opened', name, isOpen: engine.isOpen }, jsonMode);
      engine.close();
      break;
    }
    case 'exec': {
      const sql = cleanArgs.slice(1).join(' ');
      if (!sql) { console.error('Usage: tgdb exec <sql>'); process.exit(1); }
      const engine = getEngine();
      engine.open(passphrase);
      const result = engine.execute(sql);
      if (result.rows.length > 0) output(result.rows, jsonMode);
      else output({ changes: result.changes, lastInsertRowid: result.lastInsertRowid }, jsonMode);
      engine.close();
      break;
    }
    case 'insert': {
      const table = cleanArgs[1];
      const jsonData = cleanArgs.slice(2).join(' ');
      if (!table || !jsonData) { console.error('Usage: tgdb insert <table> <json>'); process.exit(1); }
      const row = JSON.parse(jsonData);
      const engine = getEngine();
      engine.open(passphrase);
      const id = engine.insert(table, row);
      output({ inserted_id: id }, jsonMode);
      engine.close();
      break;
    }
    case 'select': {
      const table = cleanArgs[1];
      if (!table) { console.error('Usage: tgdb select <table>'); process.exit(1); }
      const whereStr = cleanArgs[2];
      const where = whereStr ? JSON.parse(whereStr) : undefined;
      const engine = getEngine();
      engine.open(passphrase);
      const rows = engine.select(table, where);
      output(rows, jsonMode);
      engine.close();
      break;
    }
    case 'update': {
      const table = cleanArgs[1];
      const setStr = cleanArgs[2];
      const whereStr = cleanArgs[3];
      if (!table || !setStr || !whereStr) { console.error('Usage: tgdb update <table> <set> <where>'); process.exit(1); }
      const set = JSON.parse(setStr);
      const where = JSON.parse(whereStr);
      const engine = getEngine();
      engine.open(passphrase);
      const changes = engine.update(table, set, where);
      output({ changes }, jsonMode);
      engine.close();
      break;
    }
    case 'delete': {
      const table = cleanArgs[1];
      const whereStr = cleanArgs.slice(2).join(' ');
      if (!table || !whereStr) { console.error('Usage: tgdb delete <table> <where>'); process.exit(1); }
      const where = JSON.parse(whereStr);
      const engine = getEngine();
      engine.open(passphrase);
      const changes = engine.delete(table, where);
      output({ changes }, jsonMode);
      engine.close();
      break;
    }
    case 'sync': {
      const config = loadConfig();
      const dbPath = resolveDbPath();
      config.database_path = dbPath;
      const engine = new ClientEngine(config);
      engine.open(passphrase);
      const tracker = new ChangeTracker(engine);
      tracker.startSession();
      const changeset = tracker.exportChangeset();
      const syncManager = new SyncManager(config);
      let pushResult: { success: boolean; error?: string; version?: number } | null = null;
      if (changeset && changeset.length > 0) {
        pushResult = await syncManager.push(changeset);
        if (pushResult.success) {
          tracker.truncateBuffer();
        }
      }
      const pulled = await syncManager.pullLatest();
      engine.close();
      output({ pushed: pushResult, pulled }, jsonMode);
      break;
    }
    case 'push': {
      const config = loadConfig();
      const dbPath = resolveDbPath();
      config.database_path = dbPath;
      const engine = new ClientEngine(config);
      engine.open(passphrase);
      const tracker = new ChangeTracker(engine);
      tracker.startSession();
      const changeset = tracker.exportChangeset();
      const syncManager = new SyncManager(config);
      let result: { success: boolean; error?: string; version?: number };
      if (changeset && changeset.length > 0) {
        result = await syncManager.push(changeset);
        if (result.success) {
          tracker.truncateBuffer();
        }
      } else {
        result = await syncManager.pushFullDatabase();
      }
      engine.close();
      output(result, jsonMode);
      break;
    }
    case 'pull': {
      const config = loadConfig();
      const syncManager = new SyncManager(config);
      const version = cleanArgs[1] ? parseInt(cleanArgs[1]) : undefined;
      const ok = version ? await syncManager.pullVersion(version) : await syncManager.pullLatest();
      output({ pulled: ok, version: version || 'latest' }, jsonMode);
      break;
    }
    case 'status': {
      const config = loadConfig();
      const syncManager = new SyncManager(config);
      const status = await syncManager.getStatus();
      if (!status) {
        output({ error: 'Could not fetch status' }, jsonMode);
        break;
      }
      const dbPath = config.database_path.replace(/^~/, os.homedir());
      const localExists = fs.existsSync(dbPath);
      const enriched = {
        ...status,
        databases: status.databases.map((d: any) => ({
          ...d,
          local_version: syncManager.getLocalVersion(),
          is_stale: syncManager.isLocalStale(),
          local_file_exists: localExists,
        })),
      };
      output(enriched, jsonMode);
      break;
    }
    case 'logs': {
      const logDir = path.join(os.homedir(), '.paradox', 'logs');
      if (fs.existsSync(logDir)) {
        const files = fs.readdirSync(logDir);
        output({ log_files: files, log_dir: logDir }, jsonMode);
      } else {
        output({ message: 'No logs found' }, jsonMode);
      }
      break;
    }
    case 'versions': {
      const config = loadConfig();
      const syncManager = new SyncManager(config);
      const baseUrl = config.sync.gateway_url.replace(/\/+$/, '');
      const params = new URLSearchParams();
      params.set('database_name', path.basename(config.database_path));
      const url = `${baseUrl}/v1/versions?${params.toString()}`;
      try {
        const data = await syncManager.httpGetJSON(url, config.sync.api_key);
        output(data, jsonMode);
      } catch {
        output({ error: 'Could not fetch versions' }, jsonMode);
      }
      break;
    }
    case 'rollback': {
      const version = parseInt(cleanArgs[1]);
      if (!version) { console.error('Usage: tgdb rollback <version>'); process.exit(1); }
      const config = loadConfig();
      const syncManager = new SyncManager(config);
      const baseUrl = config.sync.gateway_url.replace(/\/+$/, '');
      const url = `${baseUrl}/v1/rollback`;
      try {
        const result = await syncManager.httpPostJSON(url, {
          database_name: path.basename(config.database_path),
          target_version: version,
        }, config.sync.api_key);
        if (result.status === 200) {
          await syncManager.pullVersion(version);
          output({ rolled_back_to: version, success: true }, jsonMode);
        } else {
          output({ error: result.data?.detail || 'rollback_failed', status: result.status }, jsonMode);
        }
      } catch (err: any) {
        output({ error: err.message }, jsonMode);
      }
      break;
    }
    case 'config': {
      const sub = cleanArgs[1];
      if (sub === 'show') {
        const config = loadConfig();
        output(config, jsonMode);
      } else if (sub === 'set') {
        const key = cleanArgs[2];
        const value = cleanArgs[3];
        if (!key || !value) { console.error('Usage: tgdb config set <key> <value>'); process.exit(1); }
        const config = loadConfig();
        const keys = key.split('.');
        let obj: any = config;
        for (let i = 0; i < keys.length - 1; i++) obj = obj[keys[i]];
        obj[keys[keys.length - 1]] = value;
        saveConfig(config);
        output({ updated: key, value }, jsonMode);
      } else {
        console.error('Usage: tgdb config <show|set>');
      }
      break;
    }
    case 'shell': {
      const readline = require('readline');
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout, prompt: 'tgdb> ' });
      const engine = getEngine();
      engine.open(passphrase);
      console.log('Paradox-DB interactive shell. Type "help" for commands, "exit" to quit.');
      rl.prompt();
      rl.on('line', (line: string) => {
        const trimmed = line.trim();
        if (trimmed === 'exit' || trimmed === 'quit') { engine.close(); process.exit(0); }
        if (trimmed === 'help') console.log('Commands: <sql>, help, exit');
        else if (trimmed) {
          try {
            const result = engine.execute(trimmed);
            if (result.rows.length > 0) console.table(result.rows);
            else console.log(`OK (${result.changes} changes)`);
          } catch (e: any) { console.error(`Error: ${e.message}`); }
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

main().catch(err => { console.error(err); process.exit(1); });
