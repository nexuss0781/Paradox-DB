import { ClientEngine } from './engine.js';
import * as crypto from 'crypto';

export interface ChangeSet {
  id: string;
  operations: ChangeOp[];
  timestamp: number;
  baseVersion: number;
}

export interface ChangeOp {
  type: 'insert' | 'update' | 'delete';
  table: string;
  data?: Record<string, any>;
  where?: Record<string, any>;
  set?: Record<string, any>;
}

export interface ConflictInfo {
  localVersion: number;
  remoteVersion: number;
  localHash: string;
  remoteHash: string;
}

export class ChangeTracker {
  private engine: ClientEngine;
  private buffer: ChangeOp[] = [];
  private baseVersion: number = 0;
  private sessionActive: boolean = false;

  constructor(engine: ClientEngine) {
    this.engine = engine;
  }

  startSession(): void {
    this.sessionActive = true;
    this.buffer = [];
  }

  track(
    type: 'insert' | 'update' | 'delete',
    table: string,
    data?: Record<string, any>,
    where?: Record<string, any>,
    set?: Record<string, any>,
  ): void {
    if (!this.sessionActive) return;
    this.buffer.push({ type, table, data, where, set });
  }

  exportChangeset(): Buffer | null {
    if (this.buffer.length === 0) return null;
    const changeSet: ChangeSet = {
      id: crypto.randomUUID(),
      operations: [...this.buffer],
      timestamp: Date.now(),
      baseVersion: this.baseVersion,
    };
    return Buffer.from(JSON.stringify(changeSet), 'utf-8');
  }

  importChangeset(patch: Buffer): { success: boolean; conflicts?: ConflictInfo } {
    try {
      const changeSet: ChangeSet = JSON.parse(patch.toString('utf-8'));

      if (changeSet.baseVersion < this.baseVersion) {
        return {
          success: false,
          conflicts: {
            localVersion: this.baseVersion,
            remoteVersion: changeSet.baseVersion,
            localHash: this.hashBuffer(this.exportChangeset() || Buffer.alloc(0)),
            remoteHash: this.hashBuffer(patch),
          },
        };
      }

      for (const op of changeSet.operations) {
        switch (op.type) {
          case 'insert':
            if (op.data) this.engine.insert(op.table, op.data);
            break;
          case 'update':
            if (op.set && op.where) this.engine.update(op.table, op.set, op.where);
            break;
          case 'delete':
            if (op.where) this.engine.delete(op.table, op.where);
            break;
        }
      }
      this.baseVersion = changeSet.baseVersion + 1;
      return { success: true };
    } catch {
      return { success: false };
    }
  }

  truncateBuffer(): void {
    this.buffer = [];
  }

  bufferSize(): number {
    if (this.buffer.length === 0) return 0;
    return Buffer.byteLength(JSON.stringify(this.buffer), 'utf-8');
  }

  changesetCount(): number {
    return this.buffer.length;
  }

  get version(): number {
    return this.baseVersion;
  }

  incrementVersion(): void {
    this.baseVersion++;
  }

  get active(): boolean {
    return this.sessionActive;
  }

  private hashBuffer(buf: Buffer): string {
    return crypto.createHash('sha256').update(buf).digest('hex');
  }
}
