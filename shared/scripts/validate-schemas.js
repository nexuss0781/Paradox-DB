import { readFileSync, readdirSync } from 'fs';
import { join } from 'path';
import Ajv from 'ajv';

const ajv = new Ajv();
const schemasDir = join(import.meta.dirname, '..', 'schemas');
const files = readdirSync(schemasDir).filter(f => f.endsWith('.json'));

let passed = 0;
let failed = 0;

for (const file of files) {
  try {
    const schema = JSON.parse(readFileSync(join(schemasDir, file), 'utf-8'));
    ajv.compile(schema);
    console.log(`  ✓ ${file}`);
    passed++;
  } catch (err) {
    console.error(`  ✗ ${file}: ${err.message}`);
    failed++;
  }
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
