import { ChangeTracker, ConflictInfo } from './change-tracker.js';
import { ClientEngine } from './engine.js';
import { SyncManager } from './sync-manager.js';

export interface ConflictResolution {
  strategy: 'lww' | 'merge' | 'manual';
  localVersion: number;
  remoteVersion: number;
  resolved: boolean;
  timestamp: number;
}

export class ConflictHandler {
  private engine: ClientEngine;
  private tracker: ChangeTracker;
  private syncManager: SyncManager;
  private conflictLog: ConflictResolution[] = [];

  constructor(engine: ClientEngine, tracker: ChangeTracker, syncManager: SyncManager) {
    this.engine = engine;
    this.tracker = tracker;
    this.syncManager = syncManager;
  }

  async handleConflict(conflict: ConflictInfo): Promise<ConflictResolution> {
    const pulled = await this.syncManager.pullLatest();
    const resolution: ConflictResolution = {
      strategy: 'lww',
      localVersion: conflict.localVersion,
      remoteVersion: conflict.remoteVersion,
      resolved: pulled,
      timestamp: Date.now(),
    };
    this.conflictLog.push(resolution);
    return resolution;
  }

  getConflictLog(): ConflictResolution[] {
    return [...this.conflictLog];
  }

  clearLog(): void {
    this.conflictLog = [];
  }
}
